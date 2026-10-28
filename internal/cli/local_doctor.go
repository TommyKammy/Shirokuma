package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	localExperimentProfile = "local-experiment"
	localExperimentContext = "colima-mac-studio-solo"
)

type localWorkload struct {
	name      string
	namespace string
	kind      string
	resource  string
}

var localLakehouseWorkloads = []localWorkload{
	{name: "seaweedfs", namespace: "shirokuma-storage", kind: "StatefulSet", resource: "seaweedfs"},
	{name: "postgresql", namespace: "shirokuma-dev", kind: "StatefulSet", resource: "polaris-postgresql"},
	{name: "polaris", namespace: "shirokuma-dev", kind: "Deployment", resource: "polaris"},
	{name: "polaris-finance", namespace: "shirokuma-dev", kind: "Deployment", resource: "polaris-finance"},
	{name: "trino", namespace: "shirokuma-dev", kind: "Deployment", resource: "trino-local"},
	{name: "polaris-bootstrap", namespace: "shirokuma-dev", kind: "Job", resource: "polaris-bootstrap"},
	{name: "iceberg-table-bootstrap", namespace: "shirokuma-dev", kind: "Job", resource: "iceberg-table-bootstrap"},
	{name: "iceberg-finance-table-bootstrap", namespace: "shirokuma-dev", kind: "Job", resource: "iceberg-finance-table-bootstrap"},
}

func runLocalExperimentDoctor(ctx context.Context, runner commandRunner, kubeContext string, now time.Time) doctorReport {
	scope := checkLocalExperimentScope(ctx, runner, kubeContext)
	checks := []doctorCheck{scope}
	if scope.Status == "healthy" {
		checks = append(checks, checkCluster(ctx, runner, kubeContext), checkFluxForProfile(ctx, runner, kubeContext, true))
		checks = append(checks, checkLocalLakehouse(ctx, runner, kubeContext)...)
	}
	checks = append(checks, doctorCheck{Name: "sql", Status: "informational", Summary: "Workload readiness only; run the local Trino smoke separately to check SQL and persistence"})
	return newDoctorReport(localExperimentProfile, kubeContext, now, checks)
}

func checkLocalExperimentScope(ctx context.Context, runner commandRunner, kubeContext string) doctorCheck {
	if kubeContext != localExperimentContext {
		return doctorCheck{Name: "local-scope", Status: "degraded", Summary: "local-experiment requires context " + localExperimentContext}
	}
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	output, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}")
	if err != nil {
		return failedCheck("local-scope", err)
	}
	endpoint, err := url.Parse(strings.TrimSpace(string(output)))
	if err == nil && endpoint.Scheme == "https" && endpoint.User == nil &&
		endpoint.Path == "" && endpoint.RawQuery == "" && endpoint.Fragment == "" {
		ip := net.ParseIP(endpoint.Hostname())
		port, portErr := strconv.Atoi(endpoint.Port())
		if ip != nil && ip.IsLoopback() && portErr == nil && port > 0 && port <= 65535 {
			return doctorCheck{Name: "local-scope", Status: "healthy", Summary: "Dedicated context uses a loopback HTTPS Kubernetes API endpoint"}
		}
	}
	return doctorCheck{Name: "local-scope", Status: "degraded", Summary: "local-experiment requires a loopback HTTPS Kubernetes API endpoint"}
}

func checkLocalLakehouse(ctx context.Context, runner commandRunner, kubeContext string) []doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	workloads, generated, err := localGeneratedInventory(checkContext, runner, kubeContext)
	if err != nil {
		return []doctorCheck{failedCheck("database-generation", err)}
	}
	resourceTypes := "deployments.apps,statefulsets.apps,jobs.batch"
	if generated {
		resourceTypes += ",configmaps"
	} else {
		workloads = localLakehouseWorkloads
	}
	resources := make(map[string]kubernetesResourceList)
	readErrors := make(map[string]error)
	for _, namespace := range []string{"shirokuma-storage", "shirokuma-dev"} {
		resources[namespace], readErrors[namespace] = readKubernetesResources(checkContext, runner, kubeContext,
			"-n", namespace, "get", resourceTypes, "-o", "json")
	}
	var checks []doctorCheck
	for _, workload := range workloads {
		id := fmt.Sprintf("%s/%s/%s", workload.kind, workload.namespace, workload.resource)
		check := doctorCheck{Name: workload.name, Status: "degraded", Summary: id + " is missing"}
		if err := readErrors[workload.namespace]; err != nil {
			check = failedCheck(workload.name, err)
		} else {
			for _, resource := range resources[workload.namespace].Items {
				if resource.Kind != workload.kind || resource.Metadata.Namespace != workload.namespace || resource.Metadata.Name != workload.resource {
					continue
				}
				ready := resource.workloadReady()
				if workload.kind == "ConfigMap" {
					ready = resource.Metadata.DeletionTimestamp == ""
				}
				if workload.kind == "Job" {
					ready = resource.conditionTrue("Complete", false) && !resource.conditionTrue("Failed", false) && resource.Metadata.DeletionTimestamp == ""
				}
				if ready {
					check.Status, check.Summary = "healthy", id+" is Ready"
					if workload.kind == "Job" {
						check.Summary = id + " is Complete"
					}
				} else {
					check.Summary = id + " is not Ready"
					if workload.kind == "Job" {
						check.Summary = id + " is not Complete"
					}
				}
				break
			}
		}
		checks = append(checks, check)
	}
	return checks
}

// The seven generated children are the public inventory contract. Read only
// their metadata and health references; never follow Secret references.
func localGeneratedInventory(ctx context.Context, runner commandRunner, kubeContext string) ([]localWorkload, bool, error) {
	const annotation = "shirokuma.dev/database-generation"
	children := []string{"shirokuma-dev", "shirokuma-object-storage", "shirokuma-catalog-database", "shirokuma-catalog-bootstrap", "shirokuma-catalog", "shirokuma-iceberg-bootstrap", "shirokuma-trino"}
	type healthReference struct {
		APIVersion string `json:"apiVersion"`
		Kind       string `json:"kind"`
		Namespace  string `json:"namespace"`
		Name       string `json:"name"`
	}
	type child struct {
		Metadata struct {
			Name        string            `json:"name"`
			Namespace   string            `json:"namespace"`
			Annotations map[string]string `json:"annotations"`
		} `json:"metadata"`
		Spec struct {
			SourceRef struct {
				Kind      string `json:"kind"`
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"sourceRef"`
			HealthChecks []healthReference `json:"healthChecks"`
		} `json:"spec"`
	}
	output, err := runner.Run(ctx, "kubectl", "--context", kubeContext, "-n", "flux-system", "get", "kustomizations.kustomize.toolkit.fluxcd.io", "-o", "json")
	if err != nil {
		return nil, false, err
	}
	var list struct {
		Items []child `json:"items"`
	}
	if err := json.Unmarshal(output, &list); err != nil {
		return nil, false, err
	}
	expected := make(map[string]bool)
	for _, name := range children {
		expected[name] = true
	}
	byName := make(map[string][]child)
	generated := false
	for _, item := range list.Items {
		if item.Metadata.Namespace != "flux-system" || !expected[item.Metadata.Name] {
			continue
		}
		byName[item.Metadata.Name] = append(byName[item.Metadata.Name], item)
		if _, present := item.Metadata.Annotations[annotation]; present {
			generated = true
		}
	}
	if !generated {
		return nil, false, nil
	}
	var workloads []localWorkload
	seen := make(map[string]bool)
	generation := ""
	for _, name := range children {
		if len(byName[name]) != 1 {
			return nil, true, fmt.Errorf("incomplete generated child set")
		}
		item := byName[name][0]
		marker := item.Metadata.Annotations[annotation]
		if marker == "" || (generation != "" && marker != generation) {
			return nil, true, fmt.Errorf("inconsistent database generation")
		}
		generation = marker
		source := item.Spec.SourceRef
		if source.Kind != "Bucket" || source.Name != "shirokuma-local-source" || (source.Namespace != "" && source.Namespace != "flux-system") {
			return nil, true, fmt.Errorf("generated child does not use dedicated local source")
		}
		// The namespace-only dev child intentionally declares no workloads.
		if name != "shirokuma-dev" && len(item.Spec.HealthChecks) == 0 {
			return nil, true, fmt.Errorf("generated child has no health inventory")
		}
		for _, ref := range item.Spec.HealthChecks {
			apiVersions := map[string]string{"Deployment": "apps/v1", "StatefulSet": "apps/v1", "Job": "batch/v1", "ConfigMap": "v1"}
			if apiVersions[ref.Kind] == "" || ref.APIVersion != apiVersions[ref.Kind] || ref.Name == "" ||
				(ref.Namespace != "shirokuma-dev" && ref.Namespace != "shirokuma-storage") {
				return nil, true, fmt.Errorf("unsupported generated health reference")
			}
			id := ref.Kind + "/" + ref.Namespace + "/" + ref.Name
			if !seen[id] {
				workloads = append(workloads, localWorkload{name: id, kind: ref.Kind, namespace: ref.Namespace, resource: ref.Name})
				seen[id] = true
			}
		}
	}
	return workloads, true, nil
}
