package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
)

const doctorSchemaVersion = "1"

const (
	kubernetesCheckTimeout = 15 * time.Second
	policyCheckTimeout     = 2 * time.Minute
	commandWaitDelay       = 2 * time.Second
	maxReportedResources   = 10
	maxDoctorCommandOutput = 1 << 20
)

var localFluxGitRevision = regexp.MustCompile(`^main@sha1:[0-9a-f]{40}$`)
var localFluxDNSLabel = regexp.MustCompile(`^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$`)

func localFluxValidName(name string, maxLength int, subdomain bool) bool {
	if len(name) == 0 || len(name) > maxLength {
		return false
	}
	labels := strings.Split(name, ".")
	if !subdomain && len(labels) != 1 {
		return false
	}
	for _, label := range labels {
		if len(label) > 63 || !localFluxDNSLabel.MatchString(label) {
			return false
		}
	}
	return true
}

type commandRunner interface {
	Run(context.Context, string, ...string) ([]byte, error)
}

type execRunner struct {
	waitDelay time.Duration
}

type doctorOutput struct {
	buffer    bytes.Buffer
	overLimit bool
}

func (output *doctorOutput) Write(data []byte) (int, error) {
	if output.buffer.Len()+len(data) > maxDoctorCommandOutput {
		output.overLimit = true
		return 0, fmt.Errorf("doctor command output exceeded limit")
	}
	return output.buffer.Write(data)
}

func (runner execRunner) Run(ctx context.Context, name string, args ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, name, args...)
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	command.Cancel = func() error {
		if command.Process == nil {
			return os.ErrProcessDone
		}
		err := syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
		if errors.Is(err, syscall.ESRCH) {
			return os.ErrProcessDone
		}
		return err
	}
	command.WaitDelay = runner.waitDelay
	if command.WaitDelay <= 0 {
		command.WaitDelay = commandWaitDelay
	}
	var output doctorOutput
	command.Stdout = &output
	command.Stderr = io.Discard
	err := command.Run()
	if output.overLimit {
		return nil, fmt.Errorf("doctor command output exceeded limit")
	}
	if err != nil {
		return nil, err
	}
	return output.buffer.Bytes(), nil
}

type doctorCheck struct {
	Name    string `json:"name"`
	Status  string `json:"status"`
	Summary string `json:"summary"`
}

type doctorReport struct {
	SchemaVersion string        `json:"schema_version"`
	GeneratedAt   string        `json:"generated_at"`
	Profile       string        `json:"profile"`
	Context       string        `json:"context"`
	Status        string        `json:"status"`
	Checks        []doctorCheck `json:"checks"`
}

type kubernetesResourceList struct {
	Kind       string `json:"kind"`
	APIVersion string `json:"apiVersion"`
	Metadata   struct {
		Continue           string `json:"continue"`
		RemainingItemCount *int64 `json:"remainingItemCount"`
	} `json:"metadata"`
	Items []struct {
		Kind       string `json:"kind"`
		APIVersion string `json:"apiVersion"`
		Metadata   struct {
			Name              string `json:"name"`
			Namespace         string `json:"namespace"`
			Generation        int64  `json:"generation"`
			DeletionTimestamp string `json:"deletionTimestamp"`
		} `json:"metadata"`
		Spec struct {
			Suspend bool   `json:"suspend"`
			Paused  bool   `json:"paused"`
			Path    string `json:"path"`
			Prune   bool   `json:"prune"`
			Wait    bool   `json:"wait"`
			Ref     struct {
				Branch string `json:"branch"`
			} `json:"ref"`
			URL       string `json:"url"`
			SecretRef struct {
				Name string `json:"name"`
			} `json:"secretRef"`
			Replicas  *int `json:"replicas"`
			SourceRef struct {
				Kind      string `json:"kind"`
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"sourceRef"`
		} `json:"spec"`
		Status struct {
			Replicas            int    `json:"replicas"`
			AvailableReplicas   int    `json:"availableReplicas"`
			UnavailableReplicas *int   `json:"unavailableReplicas"`
			ReadyReplicas       int    `json:"readyReplicas"`
			UpdatedReplicas     int    `json:"updatedReplicas"`
			ObservedGeneration  *int64 `json:"observedGeneration"`
			LastAppliedRevision string `json:"lastAppliedRevision"`
			Artifact            struct {
				Revision string `json:"revision"`
			} `json:"artifact"`
			Conditions []struct {
				Type               string `json:"type"`
				Status             string `json:"status"`
				ObservedGeneration int64  `json:"observedGeneration"`
			} `json:"conditions"`
		} `json:"status"`
	} `json:"items"`
}

// A local read is a single, complete Kubernetes v1 List, not an arbitrary
// projection that happens to include the expected objects.
func localInventoryIncomplete(list kubernetesResourceList) bool {
	return list.Items == nil || list.Metadata.Continue != "" ||
		(list.Metadata.RemainingItemCount != nil && *list.Metadata.RemainingItemCount != 0) ||
		(list.Kind != "" && list.Kind != "List") ||
		(list.APIVersion != "" && list.APIVersion != "v1")
}

func newDoctorCommand(runner commandRunner) *cobra.Command {
	var outputFormat string
	var profile string
	var kubeContext string
	var repositoryRoot string

	command := &cobra.Command{
		Use:   "doctor",
		Short: "Report bounded cluster, Flux, and policy diagnostics",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if outputFormat != "json" && outputFormat != "markdown" {
				return fmt.Errorf("unsupported output format %q (use json or markdown)", outputFormat)
			}
			if profile == localExperimentProfile && kubeContext != localExperimentContext {
				return fmt.Errorf("profile %s requires context %s", localExperimentProfile, localExperimentContext)
			}
			var resolvedRoot string
			if profile != localExperimentProfile {
				var err error
				resolvedRoot, err = resolveRepositoryRoot(repositoryRoot)
				if err != nil {
					return err
				}
			}
			report := runDoctor(cmd.Context(), runner, profile, kubeContext, resolvedRoot, time.Now().UTC())
			if outputFormat == "json" {
				encoder := json.NewEncoder(cmd.OutOrStdout())
				encoder.SetIndent("", "  ")
				return encoder.Encode(report)
			}
			return writeDoctorMarkdown(cmd.OutOrStdout(), report)
		},
	}
	command.Flags().StringVar(&outputFormat, "output", "markdown", "output format: json or markdown")
	command.Flags().StringVar(&profile, "profile", "local-lite", "diagnostic profile: local-lite or local-experiment")
	command.Flags().StringVar(&kubeContext, "context", "colima-mac-studio-solo", "Kubernetes context")
	command.Flags().StringVar(&repositoryRoot, "repo-root", "", "repository root (auto-detected from the current directory)")
	return command
}

func resolveRepositoryRoot(explicit string) (string, error) {
	start := explicit
	if start == "" {
		var err error
		start, err = os.Getwd()
		if err != nil {
			return "", fmt.Errorf("determine current directory: %w", err)
		}
	}
	current, err := filepath.Abs(start)
	if err != nil {
		return "", fmt.Errorf("resolve repository root %q: %w", start, err)
	}
	for {
		if regularFile(filepath.Join(current, "Makefile")) && regularFile(filepath.Join(current, "security", "resident-images.json")) {
			return current, nil
		}
		parent := filepath.Dir(current)
		if parent == current || explicit != "" {
			break
		}
		current = parent
	}
	return "", fmt.Errorf("Shirokuma repository root not found from %q; use --repo-root", start)
}

func regularFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func runDoctor(ctx context.Context, runner commandRunner, profile, kubeContext, repositoryRoot string, now time.Time) doctorReport {
	if profile == localExperimentProfile {
		return runLocalExperimentDoctor(ctx, runner, kubeContext, now)
	}
	checks := []doctorCheck{
		checkCluster(ctx, runner, kubeContext),
		checkFlux(ctx, runner, kubeContext),
		checkPolicy(ctx, runner, repositoryRoot),
	}
	status := "healthy"
	for _, check := range checks {
		if check.Status != "healthy" {
			status = "degraded"
			break
		}
	}
	return doctorReport{
		SchemaVersion: doctorSchemaVersion,
		GeneratedAt:   now.Format(time.RFC3339),
		Profile:       profile,
		Context:       kubeContext,
		Status:        status,
		Checks:        checks,
	}
}

func checkCluster(ctx context.Context, runner commandRunner, kubeContext string) doctorCheck {
	return checkClusterForProfile(ctx, runner, kubeContext, false)
}

func checkClusterForProfile(ctx context.Context, runner commandRunner, kubeContext string, localExperiment bool) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	output, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "get", "--raw=/readyz")
	if err != nil {
		return failedCheck("cluster", err)
	}
	if (localExperiment && string(output) != "ok" && string(output) != "ok\n") ||
		(!localExperiment && strings.TrimSpace(string(output)) != "ok") {
		return doctorCheck{Name: "cluster", Status: "degraded", Summary: "readiness endpoint did not return ok"}
	}
	return doctorCheck{Name: "cluster", Status: "healthy", Summary: "Kubernetes readiness endpoint returned ok"}
}

func checkFlux(ctx context.Context, runner commandRunner, kubeContext string) doctorCheck {
	return checkFluxForProfile(ctx, runner, kubeContext, false)
}

func checkFluxForProfile(ctx context.Context, runner commandRunner, kubeContext string, localExperiment bool) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	controllerOutput, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "-n", "flux-system", "get", "deployments.apps", "-l", "app.kubernetes.io/part-of=flux", "-o", "json")
	if err != nil {
		return failedCheck("flux", err)
	}
	var controllers kubernetesResourceList
	if err := json.Unmarshal(controllerOutput, &controllers); err != nil {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "controller response was not valid JSON"}
	}
	if localExperiment && localInventoryIncomplete(controllers) {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "controller response was incomplete"}
	}
	if len(controllers.Items) != 4 {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("expected 4 Flux controllers, found %d", len(controllers.Items))}
	}
	requiredControllers := []string{"source-controller", "kustomize-controller", "helm-controller", "notification-controller"}
	foundControllers := make(map[string]bool, len(controllers.Items))
	for _, controller := range controllers.Items {
		foundControllers[controller.Metadata.Name] = true
	}
	var missingControllers []string
	for _, name := range requiredControllers {
		if !foundControllers[name] {
			missingControllers = append(missingControllers, name)
		}
	}
	if len(missingControllers) > 0 {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("missing required Flux controllers: %s", strings.Join(missingControllers, ", "))}
	}
	var unhealthy []string
	for _, controller := range controllers.Items {
		unready := controller.Status.Replicas < 1 || controller.Status.AvailableReplicas < controller.Status.Replicas
		if localExperiment {
			unready = unready || (controller.APIVersion != "" && controller.APIVersion != "apps/v1") ||
				controller.Kind != "Deployment" || controller.Metadata.Namespace != "flux-system" ||
				controller.Spec.Paused ||
				controller.Metadata.DeletionTimestamp != "" ||
				controller.Metadata.Generation < 1 || controller.Status.ObservedGeneration == nil || *controller.Status.ObservedGeneration != controller.Metadata.Generation ||
				controller.Spec.Replicas == nil || *controller.Spec.Replicas < 1 ||
				controller.Status.Replicas != *controller.Spec.Replicas ||
				controller.Status.AvailableReplicas != *controller.Spec.Replicas ||
				controller.Status.ReadyReplicas != *controller.Spec.Replicas ||
				controller.Status.UpdatedReplicas != *controller.Spec.Replicas
			if controller.Status.UnavailableReplicas != nil && *controller.Status.UnavailableReplicas != 0 {
				unready = true
			}
			available := false
			for _, condition := range controller.Status.Conditions {
				if condition.Type == "Available" && condition.Status == "True" {
					available = true
				}
				if (condition.Type == "ReplicaFailure" && (condition.Status == "True" || (localExperiment && condition.Status != "False"))) ||
					(condition.Type == "Progressing" && condition.Status != "True") ||
					(condition.Type == "Available" && condition.Status != "True") {
					unready = true
				}
			}
			unready = unready || !available
		}
		if unready {
			unhealthy = append(unhealthy, "Deployment/"+controller.Metadata.Name)
		}
	}

	resourceOutput, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "get", "gitrepositories.source.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io,buckets.source.toolkit.fluxcd.io,helmrepositories.source.toolkit.fluxcd.io,helmcharts.source.toolkit.fluxcd.io,kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json")
	if err != nil {
		return failedCheck("flux", err)
	}
	var resources kubernetesResourceList
	if err := json.Unmarshal(resourceOutput, &resources); err != nil {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "resource response was not valid JSON"}
	}
	if localExperiment && localInventoryIncomplete(resources) {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "resource response was incomplete"}
	}
	kinds := map[string]int{}
	seenResources := map[string]bool{}
	rootCount, rootSourceCount, devCount := 0, 0, 0
	rootSuspended := false
	sourceRevision, devRevision := "", ""
	for _, resource := range resources.Items {
		kinds[resource.Kind]++
		resourceID := fmt.Sprintf("%s/%s/%s", resource.Kind, resource.Metadata.Namespace, resource.Metadata.Name)
		if localExperiment {
			// Do not echo arbitrary API fields in a human-facing diagnostic.
			// Even malformed resource names may contain credentials or controls.
			validIdentity := localFluxValidName(resource.Metadata.Name, 253, true) &&
				localFluxValidName(resource.Metadata.Namespace, 63, false)
			switch resource.Kind {
			case "GitRepository", "OCIRepository", "Bucket", "HelmRepository", "HelmChart", "Kustomization", "HelmRelease":
			default:
				unhealthy = append(unhealthy, "Flux resource (unexpected kind)")
				validIdentity = false
			}
			if !validIdentity {
				unhealthy = append(unhealthy, "Flux resource (identity unverified)")
				resourceID = "Flux resource (identity unverified)"
			} else if seenResources[resourceID] {
				unhealthy = append(unhealthy, resourceID+" (duplicated)")
			}
			seenResources[resourceID] = true
			if resource.Metadata.Generation < 1 {
				unhealthy = append(unhealthy, resourceID+" (generation unverified)")
			}
			if resource.Status.ObservedGeneration != nil && *resource.Status.ObservedGeneration != resource.Metadata.Generation {
				unhealthy = append(unhealthy, resourceID+" (status generation mismatch)")
			}
		}
		isRoot := resource.Kind == "Kustomization" && resource.Metadata.Namespace == "flux-system" && resource.Metadata.Name == "flux-system"
		isRootSource := resource.Kind == "GitRepository" && resource.Metadata.Namespace == "flux-system" && resource.Metadata.Name == "flux-system"
		isDev := resource.Kind == "Kustomization" && resource.Metadata.Namespace == "flux-system" && resource.Metadata.Name == "shirokuma-dev"
		if isRoot {
			rootCount++
			if localExperiment && !resource.Spec.Prune {
				unhealthy = append(unhealthy, resourceID+" (prune disabled)")
			}
			if localExperiment && resource.Spec.Path != "./deploy/gitops/clusters/local-lite" {
				unhealthy = append(unhealthy, resourceID+" (path mismatch)")
			}
			if localExperiment && (resource.Spec.SourceRef.Kind != "GitRepository" || resource.Spec.SourceRef.Name != "flux-system" ||
				(resource.Spec.SourceRef.Namespace != "" && resource.Spec.SourceRef.Namespace != "flux-system")) {
				unhealthy = append(unhealthy, resourceID+" (source reference mismatch)")
			}
		}
		if isRootSource {
			rootSourceCount++
			sourceRevision = resource.Status.Artifact.Revision
			if localExperiment && resource.Spec.Suspend {
				unhealthy = append(unhealthy, resourceID+" (declared source suspended)")
			}
			if localExperiment && resource.Spec.Ref.Branch != "main" {
				unhealthy = append(unhealthy, resourceID+" (declared branch mismatch)")
			}
			if localExperiment && resource.Spec.URL != "ssh://git@github.com/TommyKammy/Shirokuma" {
				unhealthy = append(unhealthy, resourceID+" (declared origin mismatch)")
			}
			if localExperiment && resource.Spec.SecretRef.Name != "flux-system" {
				unhealthy = append(unhealthy, resourceID+" (declared credentials reference mismatch)")
			}
			if localExperiment && !localFluxGitRevision.MatchString(resource.Status.Artifact.Revision) {
				unhealthy = append(unhealthy, resourceID+" (revision unverified)")
			}
		}
		if localExperiment && isDev {
			devCount++
			devRevision = resource.Status.LastAppliedRevision
			if !resource.Spec.Wait {
				unhealthy = append(unhealthy, resourceID+" (wait disabled)")
			}
			if !resource.Spec.Prune {
				unhealthy = append(unhealthy, resourceID+" (prune disabled)")
			}
			if resource.Spec.Path != "./deploy/gitops/dev" {
				unhealthy = append(unhealthy, resourceID+" (path mismatch)")
			}
			if resource.Spec.SourceRef.Kind != "GitRepository" || resource.Spec.SourceRef.Name != "flux-system" ||
				(resource.Spec.SourceRef.Namespace != "" && resource.Spec.SourceRef.Namespace != "flux-system") {
				unhealthy = append(unhealthy, resourceID+" (source reference mismatch)")
			}
		}
		if localExperiment && resource.Metadata.DeletionTimestamp != "" {
			unhealthy = append(unhealthy, resourceID+" (terminating)")
		}
		// Suspension relaxes Ready freshness, not a current-generation failure.
		if localExperiment {
			for _, condition := range resource.Status.Conditions {
				if condition.ObservedGeneration == resource.Metadata.Generation &&
					(condition.Type == "Stalled" || condition.Type == "Reconciling") && condition.Status != "False" {
					unhealthy = append(unhealthy, resourceID+" (unresolved reconciliation condition)")
				}
			}
		}
		if localExperiment && isRoot && resource.Spec.Suspend && resource.Metadata.DeletionTimestamp == "" {
			// A stale Ready condition is expected while suspended, but an
			// explicitly failed current generation is not healthy history.
			for _, condition := range resource.Status.Conditions {
				if condition.Type == "Ready" && condition.ObservedGeneration == resource.Metadata.Generation && condition.Status != "True" {
					unhealthy = append(unhealthy, resourceID+" (conflicting Ready condition)")
				}
			}
			rootSuspended = true
			continue
		}
		if resource.Spec.Suspend {
			unhealthy = append(unhealthy, resourceID+" (suspended)")
			continue
		}
		ready := false
		for _, condition := range resource.Status.Conditions {
			if condition.Type == "Ready" && condition.ObservedGeneration == resource.Metadata.Generation {
				if condition.Status == "True" {
					ready = true
				} else if localExperiment {
					unhealthy = append(unhealthy, resourceID+" (conflicting Ready condition)")
				}
			}
		}
		if !ready {
			unhealthy = append(unhealthy, resourceID)
		}
	}
	if kinds["GitRepository"] == 0 || kinds["Kustomization"] == 0 {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "Flux GitRepository and Kustomization resources are required"}
	}
	if localExperiment && rootCount != 1 {
		unhealthy = append(unhealthy, "Kustomization/flux-system/flux-system (missing or duplicated)")
	}
	if localExperiment && rootSourceCount != 1 {
		unhealthy = append(unhealthy, "GitRepository/flux-system/flux-system (missing or duplicated)")
	}
	if localExperiment && devCount != 1 {
		unhealthy = append(unhealthy, "Kustomization/flux-system/shirokuma-dev (missing or duplicated)")
	}
	if localExperiment && devCount == 1 && rootSourceCount == 1 && (devRevision == "" || devRevision != sourceRevision) {
		unhealthy = append(unhealthy, "Kustomization/flux-system/shirokuma-dev (applied revision differs from source)")
	}
	if len(unhealthy) > 0 {
		sort.Strings(unhealthy)
		reported := unhealthy
		if len(reported) > maxReportedResources {
			reported = reported[:maxReportedResources]
		}
		return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("%d Flux resource(s) failed readiness or fixed-source checks; first %d: %s", len(unhealthy), len(reported), strings.Join(reported, ", "))}
	}
	if rootSuspended {
		return doctorCheck{Name: "flux", Status: "informational", Summary: fmt.Sprintf("4 controllers and %d other reconciled resource(s) are Ready; root Kustomization/flux-system/flux-system is suspended in local-experiment (intent and source revision provenance not independently verified)", len(resources.Items)-1)}
	}
	return doctorCheck{Name: "flux", Status: "healthy", Summary: fmt.Sprintf("4 controllers and %d reconciled resource(s) are Ready", len(resources.Items))}
}

func checkPolicy(ctx context.Context, runner commandRunner, repositoryRoot string) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, policyCheckTimeout)
	defer cancel()
	if _, err := runner.Run(checkContext, "make", "-C", repositoryRoot, "verify-security"); err != nil {
		return failedCheck("policy", err)
	}
	return doctorCheck{Name: "policy", Status: "healthy", Summary: "repository supply-chain policy passed"}
}

func failedCheck(name string, err error) doctorCheck {
	summary := "command could not be executed"
	if exitError, ok := err.(*exec.ExitError); ok {
		summary = fmt.Sprintf("command exited with status %d", exitError.ExitCode())
	}
	return doctorCheck{Name: name, Status: "degraded", Summary: summary}
}

func writeDoctorMarkdown(output io.Writer, report doctorReport) error {
	if _, err := fmt.Fprintf(output, "# Shirokuma doctor\n\n- Status: **%s**\n- Profile: `%s`\n- Context: `%s`\n- Generated: `%s`\n\n", report.Status, report.Profile, report.Context, report.GeneratedAt); err != nil {
		return err
	}
	for _, check := range report.Checks {
		if _, err := fmt.Fprintf(output, "- **%s**: %s — %s\n", check.Name, check.Status, check.Summary); err != nil {
			return err
		}
	}
	return nil
}
