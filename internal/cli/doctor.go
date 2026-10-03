package cli

import (
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
)

var localFluxGitRevision = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}@sha1:[0-9a-f]{40}$`)

type commandRunner interface {
	Run(context.Context, string, ...string) ([]byte, error)
}

type execRunner struct {
	waitDelay time.Duration
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
	return command.Output()
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
	Metadata struct {
		Continue string `json:"continue"`
	} `json:"metadata"`
	Items []struct {
		Kind     string `json:"kind"`
		Metadata struct {
			Name              string `json:"name"`
			Namespace         string `json:"namespace"`
			Generation        int64  `json:"generation"`
			DeletionTimestamp string `json:"deletionTimestamp"`
		} `json:"metadata"`
		Spec struct {
			Suspend   bool `json:"suspend"`
			Replicas  *int `json:"replicas"`
			SourceRef struct {
				Kind      string `json:"kind"`
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"sourceRef"`
		} `json:"spec"`
		Status struct {
			Replicas           int    `json:"replicas"`
			AvailableReplicas  int    `json:"availableReplicas"`
			UnavailableReplicas *int  `json:"unavailableReplicas"`
			ReadyReplicas      int    `json:"readyReplicas"`
			UpdatedReplicas    int    `json:"updatedReplicas"`
			ObservedGeneration *int64 `json:"observedGeneration"`
			Artifact           struct {
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
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	output, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "get", "--raw=/readyz")
	if err != nil {
		return failedCheck("cluster", err)
	}
	if strings.TrimSpace(string(output)) != "ok" {
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
	if localExperiment && (controllers.Items == nil || controllers.Metadata.Continue != "") {
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
			unready = unready || controller.Kind != "Deployment" || controller.Metadata.Namespace != "flux-system" ||
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
	if localExperiment && (resources.Items == nil || resources.Metadata.Continue != "") {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "resource response was incomplete"}
	}
	kinds := map[string]int{}
	seenResources := map[string]bool{}
	rootCount, rootSourceCount := 0, 0
	rootSuspended := false
	for _, resource := range resources.Items {
		kinds[resource.Kind]++
		resourceID := fmt.Sprintf("%s/%s/%s", resource.Kind, resource.Metadata.Namespace, resource.Metadata.Name)
		if localExperiment {
			switch resource.Kind {
			case "GitRepository", "OCIRepository", "Bucket", "HelmRepository", "HelmChart", "Kustomization", "HelmRelease":
			default:
				unhealthy = append(unhealthy, "Flux resource (unexpected kind)")
			}
			if resource.Kind == "" || resource.Metadata.Namespace == "" || resource.Metadata.Name == "" {
				unhealthy = append(unhealthy, "Flux resource (identity unverified)")
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
		if isRoot {
			rootCount++
			if localExperiment && (resource.Spec.SourceRef.Kind != "GitRepository" || resource.Spec.SourceRef.Name != "flux-system" ||
				(resource.Spec.SourceRef.Namespace != "" && resource.Spec.SourceRef.Namespace != "flux-system")) {
				unhealthy = append(unhealthy, resourceID+" (source reference mismatch)")
			}
		}
		if isRootSource {
			rootSourceCount++
			if localExperiment && !localFluxGitRevision.MatchString(resource.Status.Artifact.Revision) {
				unhealthy = append(unhealthy, resourceID+" (revision unverified)")
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
