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

// Decode only the Kubernetes fields needed for health diagnostics.
type kubernetesResource struct {
	Kind     string `json:"kind"`
	Metadata struct {
		Name              string `json:"name"`
		Namespace         string `json:"namespace"`
		Generation        int64  `json:"generation"`
		DeletionTimestamp string `json:"deletionTimestamp"`
	} `json:"metadata"`
	Spec struct {
		Suspend  bool `json:"suspend"`
		Paused   bool `json:"paused"`
		Replicas *int `json:"replicas"`
	} `json:"spec"`
	Status struct {
		Replicas           int   `json:"replicas"`
		AvailableReplicas  int   `json:"availableReplicas"`
		ReadyReplicas      int   `json:"readyReplicas"`
		UpdatedReplicas    int   `json:"updatedReplicas"`
		ObservedGeneration int64 `json:"observedGeneration"`
		Conditions         []struct {
			Type               string `json:"type"`
			Status             string `json:"status"`
			ObservedGeneration int64  `json:"observedGeneration"`
		} `json:"conditions"`
	} `json:"status"`
}

type kubernetesResourceList struct {
	Items []kubernetesResource `json:"items"`
}

func (resource kubernetesResource) conditionTrue(kind string, currentGeneration bool) bool {
	for _, condition := range resource.Status.Conditions {
		if condition.Type == kind {
			return condition.Status == "True" && (!currentGeneration ||
				(resource.Metadata.Generation > 0 && condition.ObservedGeneration == resource.Metadata.Generation))
		}
	}
	return false
}

func (resource kubernetesResource) workloadReady() bool {
	desired := resource.Spec.Replicas
	return desired != nil && *desired > 0 && !resource.Spec.Paused &&
		resource.Metadata.DeletionTimestamp == "" && resource.Metadata.Generation > 0 &&
		resource.Status.ObservedGeneration == resource.Metadata.Generation &&
		resource.Status.Replicas == *desired && resource.Status.ReadyReplicas == *desired &&
		resource.Status.UpdatedReplicas == *desired &&
		(resource.Kind != "Deployment" || resource.Status.AvailableReplicas == *desired)
}

func newDoctorCommand(runner commandRunner) *cobra.Command {
	var outputFormat, profile, kubeContext, repositoryRoot string
	command := &cobra.Command{
		Use:   "doctor",
		Short: "Report cluster, Flux, and policy diagnostics",
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
	command.Flags().StringVar(&kubeContext, "context", localExperimentContext, "Kubernetes context")
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
	return newDoctorReport(profile, kubeContext, now, []doctorCheck{
		checkCluster(ctx, runner, kubeContext),
		checkFlux(ctx, runner, kubeContext),
		checkPolicy(ctx, runner, repositoryRoot),
	})
}

func newDoctorReport(profile, kubeContext string, now time.Time, checks []doctorCheck) doctorReport {
	report := doctorReport{
		SchemaVersion: doctorSchemaVersion, GeneratedAt: now.Format(time.RFC3339),
		Profile: profile, Context: kubeContext, Status: "healthy", Checks: checks,
	}
	for _, check := range checks {
		if check.Status == "degraded" {
			report.Status = "degraded"
		}
	}
	return report
}

func readKubernetesResources(ctx context.Context, runner commandRunner, kubeContext string, args ...string) (kubernetesResourceList, error) {
	var list kubernetesResourceList
	output, err := runner.Run(ctx, "kubectl", append([]string{"--context", kubeContext}, args...)...)
	if err != nil {
		return list, err
	}
	err = json.Unmarshal(output, &list)
	return list, err
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
	nodes, err := readKubernetesResources(checkContext, runner, kubeContext, "get", "nodes", "-o", "json")
	if err != nil {
		return failedCheck("cluster", err)
	}
	if len(nodes.Items) == 0 {
		return doctorCheck{Name: "cluster", Status: "degraded", Summary: "no Kubernetes nodes found"}
	}
	for _, node := range nodes.Items {
		if !node.conditionTrue("Ready", false) || node.Metadata.DeletionTimestamp != "" {
			return doctorCheck{Name: "cluster", Status: "degraded", Summary: "Kubernetes node " + node.Metadata.Name + " is not Ready"}
		}
	}
	return doctorCheck{Name: "cluster", Status: "healthy", Summary: fmt.Sprintf("Kubernetes API and %d node(s) are Ready", len(nodes.Items))}
}

func checkFlux(ctx context.Context, runner commandRunner, kubeContext string) doctorCheck {
	return checkFluxForProfile(ctx, runner, kubeContext, false)
}

func checkFluxForProfile(ctx context.Context, runner commandRunner, kubeContext string, localExperiment bool) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	controllers, err := readKubernetesResources(checkContext, runner, kubeContext,
		"-n", "flux-system", "get", "deployments.apps", "-l", "app.kubernetes.io/part-of=flux", "-o", "json")
	if err != nil {
		return failedCheck("flux", err)
	}
	required := map[string]bool{
		"source-controller": false, "kustomize-controller": false,
		"helm-controller": false, "notification-controller": false,
	}
	var unhealthy []string
	for _, controller := range controllers.Items {
		if _, ok := required[controller.Metadata.Name]; ok {
			required[controller.Metadata.Name] = true
		}
		ready := controller.Status.Replicas > 0 && controller.Status.AvailableReplicas >= controller.Status.Replicas
		if localExperiment {
			ready = controller.workloadReady()
		}
		if !ready {
			unhealthy = append(unhealthy, "Deployment/"+controller.Metadata.Name)
		}
	}
	for name, found := range required {
		if !found {
			unhealthy = append(unhealthy, "missing Deployment/"+name)
		}
	}
	if len(unhealthy) > 0 {
		return unhealthyFlux(unhealthy)
	}
	resources, err := readKubernetesResources(checkContext, runner, kubeContext, "get",
		"gitrepositories.source.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io,buckets.source.toolkit.fluxcd.io,helmrepositories.source.toolkit.fluxcd.io,helmcharts.source.toolkit.fluxcd.io,kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io",
		"-A", "-o", "json")
	if err != nil {
		return failedCheck("flux", err)
	}
	kinds := map[string]int{}
	found := map[string]bool{}
	rootSuspended := false
	for _, resource := range resources.Items {
		kinds[resource.Kind]++
		id := resource.Kind + "/" + resource.Metadata.Namespace + "/" + resource.Metadata.Name
		found[id] = true
		if localExperiment && id == "GitRepository/flux-system/flux-system" {
			continue // Local children use the Bucket, not the legacy Git bootstrap source.
		}
		if localExperiment && id == "Kustomization/flux-system/flux-system" &&
			resource.Spec.Suspend && resource.Metadata.DeletionTimestamp == "" {
			rootSuspended = true
			continue
		}
		switch {
		case resource.Spec.Suspend:
			unhealthy = append(unhealthy, id+" (suspended)")
		case resource.Metadata.DeletionTimestamp != "" || !resource.conditionTrue("Ready", true):
			unhealthy = append(unhealthy, id)
		}
	}
	if !localExperiment && (kinds["GitRepository"] == 0 || kinds["Kustomization"] == 0) {
		unhealthy = append(unhealthy, "Flux GitRepository and Kustomization resources are required")
	}
	if localExperiment {
		for _, id := range []string{"Bucket/flux-system/shirokuma-local-source", "Kustomization/flux-system/flux-system",
			"Kustomization/flux-system/shirokuma-dev", "Kustomization/flux-system/shirokuma-object-storage",
			"Kustomization/flux-system/shirokuma-catalog-database", "Kustomization/flux-system/shirokuma-catalog-bootstrap",
			"Kustomization/flux-system/shirokuma-catalog", "Kustomization/flux-system/shirokuma-iceberg-bootstrap",
			"Kustomization/flux-system/shirokuma-trino"} {
			if !found[id] {
				unhealthy = append(unhealthy, "missing "+id)
			}
		}
	}
	if len(unhealthy) > 0 {
		return unhealthyFlux(unhealthy)
	}
	summary := fmt.Sprintf("4 controllers and %d reconciled resource(s) are Ready", len(resources.Items))
	if localExperiment {
		summary = "4 controllers, local source Bucket and child Kustomizations are Ready"
		if rootSuspended {
			summary += "; root Kustomization/flux-system/flux-system is suspended in local-experiment"
		}
	}
	return doctorCheck{Name: "flux", Status: "healthy", Summary: summary}
}

func unhealthyFlux(resources []string) doctorCheck {
	sort.Strings(resources)
	total := len(resources)
	if len(resources) > maxReportedResources {
		resources = resources[:maxReportedResources]
	}
	return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("%d Flux resource(s) failed readiness: %s", total, strings.Join(resources, ", "))}
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
	var syntaxError *json.SyntaxError
	var typeError *json.UnmarshalTypeError
	if errors.As(err, &syntaxError) || errors.As(err, &typeError) {
		summary = "Kubernetes response was not valid JSON"
	} else if exitError, ok := err.(*exec.ExitError); ok {
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
