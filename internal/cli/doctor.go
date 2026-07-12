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
	Items []struct {
		Kind     string `json:"kind"`
		Metadata struct {
			Name       string `json:"name"`
			Namespace  string `json:"namespace"`
			Generation int64  `json:"generation"`
		} `json:"metadata"`
		Spec struct {
			Suspend bool `json:"suspend"`
		} `json:"spec"`
		Status struct {
			Replicas          int `json:"replicas"`
			AvailableReplicas int `json:"availableReplicas"`
			Conditions        []struct {
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
			resolvedRoot, err := resolveRepositoryRoot(repositoryRoot)
			if err != nil {
				return err
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
	command.Flags().StringVar(&profile, "profile", "local-lite", "diagnostic profile")
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
		if controller.Status.Replicas < 1 || controller.Status.AvailableReplicas < controller.Status.Replicas {
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
	kinds := map[string]int{}
	for _, resource := range resources.Items {
		kinds[resource.Kind]++
		resourceID := fmt.Sprintf("%s/%s/%s", resource.Kind, resource.Metadata.Namespace, resource.Metadata.Name)
		if resource.Spec.Suspend {
			unhealthy = append(unhealthy, resourceID+" (suspended)")
			continue
		}
		ready := false
		for _, condition := range resource.Status.Conditions {
			if condition.Type == "Ready" && condition.Status == "True" && condition.ObservedGeneration == resource.Metadata.Generation {
				ready = true
				break
			}
		}
		if !ready {
			unhealthy = append(unhealthy, resourceID)
		}
	}
	if kinds["GitRepository"] == 0 || kinds["Kustomization"] == 0 {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "Flux GitRepository and Kustomization resources are required"}
	}
	if len(unhealthy) > 0 {
		sort.Strings(unhealthy)
		reported := unhealthy
		if len(reported) > maxReportedResources {
			reported = reported[:maxReportedResources]
		}
		return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("%d Flux resource(s) are suspended or not Ready; first %d: %s", len(unhealthy), len(reported), strings.Join(reported, ", "))}
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
