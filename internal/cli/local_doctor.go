package cli

import (
	"context"
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
	{name: "trino", namespace: "shirokuma-dev", kind: "Deployment", resource: "trino-local"},
	{name: "polaris-bootstrap", namespace: "shirokuma-dev", kind: "Job", resource: "polaris-bootstrap"},
	{name: "iceberg-table-bootstrap", namespace: "shirokuma-dev", kind: "Job", resource: "iceberg-table-bootstrap"},
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
	resources := make(map[string]kubernetesResourceList)
	readErrors := make(map[string]error)
	for _, namespace := range []string{"shirokuma-storage", "shirokuma-dev"} {
		resources[namespace], readErrors[namespace] = readKubernetesResources(checkContext, runner, kubeContext,
			"-n", namespace, "get", "deployments.apps,statefulsets.apps,jobs.batch", "-o", "json")
	}
	var checks []doctorCheck
	for _, workload := range localLakehouseWorkloads {
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
