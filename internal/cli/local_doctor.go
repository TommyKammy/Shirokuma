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
	{name: "trino", namespace: "shirokuma-dev", kind: "Deployment", resource: "trino-local"},
}

func runLocalExperimentDoctor(ctx context.Context, runner commandRunner, kubeContext string, now time.Time) doctorReport {
	scope := checkLocalExperimentScope(ctx, runner, kubeContext)
	checks := []doctorCheck{scope}
	if scope.Status == "healthy" {
		checks = append(checks, checkClusterForProfile(ctx, runner, kubeContext, true), checkFluxForProfile(ctx, runner, kubeContext, true))
		checks = append(checks, checkLocalLakehouse(ctx, runner, kubeContext)...)
	}
	checks = append(checks,
		doctorCheck{Name: "sql", Status: "informational", Summary: "SQL not evaluated; workload readiness does not prove query, write, or persistence success. Run the local Trino smoke separately"},
		doctorCheck{Name: "publication", Status: "informational", Summary: "Publication and historical supply-chain authorization gates not evaluated by local-experiment; this report is not production acceptance"},
	)
	status := "healthy"
	for _, check := range checks {
		if check.Status != "healthy" && check.Status != "informational" {
			status = "degraded"
		}
	}
	return doctorReport{
		SchemaVersion: doctorSchemaVersion,
		GeneratedAt:   now.Format(time.RFC3339),
		Profile:       localExperimentProfile,
		Context:       kubeContext,
		Status:        status,
		Checks:        checks,
	}
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
	rawEndpoint := string(output)
	endpoint, err := url.Parse(rawEndpoint)
	if err != nil || endpoint.Scheme != "https" || endpoint.User != nil || endpoint.Host == "" || endpoint.Path != "" || endpoint.RawQuery != "" || endpoint.ForceQuery || strings.Contains(rawEndpoint, "#") || endpoint.Opaque != "" {
		return doctorCheck{Name: "local-scope", Status: "degraded", Summary: "local-experiment requires a loopback HTTPS Kubernetes API endpoint"}
	}
	port, portErr := strconv.Atoi(endpoint.Port())
	if portErr != nil || port < 1 || port > 65535 {
		return doctorCheck{Name: "local-scope", Status: "degraded", Summary: "local-experiment requires a loopback HTTPS Kubernetes API endpoint"}
	}
	ip := net.ParseIP(endpoint.Hostname())
	if endpoint.Hostname() != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return doctorCheck{Name: "local-scope", Status: "degraded", Summary: "local-experiment requires a loopback HTTPS Kubernetes API endpoint"}
	}
	return doctorCheck{Name: "local-scope", Status: "healthy", Summary: "Dedicated context uses a loopback HTTPS Kubernetes API endpoint; diagnostics are read-only"}
}

func checkLocalLakehouse(ctx context.Context, runner commandRunner, kubeContext string) []doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	// Two namespace-scoped reads avoid relying on labels that may drift during experiments.
	resources := make(map[string]kubernetesResourceList)
	errors := make(map[string]doctorCheck)
	for _, namespace := range []string{"shirokuma-storage", "shirokuma-dev"} {
		output, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "-n", namespace, "get", "deployments.apps,statefulsets.apps", "-o", "json")
		if err != nil {
			errors[namespace] = failedCheck("", err)
			continue
		}
		var list kubernetesResourceList
		if err := json.Unmarshal(output, &list); err != nil {
			errors[namespace] = doctorCheck{Status: "degraded", Summary: "workload response was not valid JSON"}
			continue
		}
		if localInventoryIncomplete(list) {
			errors[namespace] = doctorCheck{Status: "degraded", Summary: "workload response was incomplete"}
			continue
		}
		// A scoped kubectl list must not silently mix in other resource types or namespaces.
		invalid := false
		seen := make(map[string]bool, len(list.Items))
		for _, resource := range list.Items {
			identity := resource.Kind + "/" + resource.Metadata.Name
			if (resource.Kind != "Deployment" && resource.Kind != "StatefulSet") || (resource.APIVersion != "" && resource.APIVersion != "apps/v1") || resource.Metadata.Namespace != namespace || !localFluxValidName(resource.Metadata.Name, 253, true) || seen[identity] {
				invalid = true
				break
			}
			seen[identity] = true
		}
		if invalid {
			errors[namespace] = doctorCheck{Status: "degraded", Summary: "workload response contained an out-of-scope or duplicated resource"}
			continue
		}
		resources[namespace] = list
	}
	checks := make([]doctorCheck, 0, len(localLakehouseWorkloads))
	for _, workload := range localLakehouseWorkloads {
		resourceID := fmt.Sprintf("%s/%s/%s", workload.kind, workload.namespace, workload.resource)
		check := doctorCheck{Name: workload.name, Status: "degraded", Summary: resourceID + " is missing"}
		if namespaceError, ok := errors[workload.namespace]; ok {
			namespaceError.Name = workload.name
			namespaceError.Summary = resourceID + ": " + namespaceError.Summary
			checks = append(checks, namespaceError)
			continue
		}
		matches := 0
		for _, resource := range resources[workload.namespace].Items {
			if resource.Kind != workload.kind || resource.Metadata.Name != workload.resource || resource.Metadata.Namespace != workload.namespace {
				continue
			}
			matches++
			desired := resource.Spec.Replicas
			switch {
			case resource.Metadata.DeletionTimestamp != "":
				check.Summary = resourceID + " is terminating"
			case workload.kind == "Deployment" && resource.Spec.Paused:
				check.Summary = resourceID + " rollout is paused"
			case resource.Metadata.Generation < 1 || resource.Status.ObservedGeneration == nil || *resource.Status.ObservedGeneration != resource.Metadata.Generation:
				check.Summary = resourceID + " controller has not observed the current generation"
			case desired == nil || *desired < 1:
				check.Summary = resourceID + " has no positive desired replica count"
			case resource.Status.Replicas != *desired || resource.Status.ReadyReplicas != *desired || resource.Status.UpdatedReplicas != *desired || resource.Status.AvailableReplicas != *desired:
				check.Summary = fmt.Sprintf("%s rollout incomplete: desired=%d total=%d ready=%d updated=%d available=%d", resourceID, *desired, resource.Status.Replicas, resource.Status.ReadyReplicas, resource.Status.UpdatedReplicas, resource.Status.AvailableReplicas)
			case resource.Status.UnavailableReplicas != nil && *resource.Status.UnavailableReplicas != 0:
				check.Summary = resourceID + " reports unavailable replicas despite complete rollout counters"
			default:
				check.Status = "healthy"
				check.Summary = fmt.Sprintf("%s current generation %d is ready (%d/%d replicas); Kubernetes readiness only", resourceID, resource.Metadata.Generation, resource.Status.ReadyReplicas, *desired)
				available := workload.kind != "Deployment"
				for _, condition := range resource.Status.Conditions {
					if condition.Type == "Available" && condition.Status == "True" {
						available = true
					}
					if (condition.Type == "ReplicaFailure" && condition.Status != "False") ||
						(condition.Type == "Progressing" && condition.Status != "True") ||
						(condition.Type == "Available" && condition.Status != "True") {
						check.Status = "degraded"
						check.Summary = resourceID + " reports a failed rollout condition"
					}
				}
				if !available {
					check.Status = "degraded"
					check.Summary = resourceID + " has no Available=True condition"
				}
			}
		}
		if matches > 1 {
			check.Status = "degraded"
			check.Summary = resourceID + " is duplicated in workload response"
		}
		checks = append(checks, check)
	}
	return checks
}
