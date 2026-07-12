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
	maxReportedApps        = 10
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

type argoApplicationList struct {
	Items []struct {
		Metadata struct {
			Name string `json:"name"`
		} `json:"metadata"`
		Status struct {
			Sync struct {
				Status string `json:"status"`
			} `json:"sync"`
			Health struct {
				Status string `json:"status"`
			} `json:"health"`
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
		Short: "Report bounded cluster, Argo CD, and policy diagnostics",
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
		checkArgoCD(ctx, runner, kubeContext),
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

func checkArgoCD(ctx context.Context, runner commandRunner, kubeContext string) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	output, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "-n", "argocd", "get", "applications.argoproj.io", "-o", "json")
	if err != nil {
		return failedCheck("argocd", err)
	}
	var applications argoApplicationList
	if err := json.Unmarshal(output, &applications); err != nil {
		return doctorCheck{Name: "argocd", Status: "degraded", Summary: "application response was not valid JSON"}
	}
	if len(applications.Items) == 0 {
		return doctorCheck{Name: "argocd", Status: "degraded", Summary: "no Argo CD applications found"}
	}
	var unhealthy []string
	for _, application := range applications.Items {
		if application.Status.Sync.Status != "Synced" || application.Status.Health.Status != "Healthy" {
			unhealthy = append(unhealthy, application.Metadata.Name)
		}
	}
	if len(unhealthy) > 0 {
		sort.Strings(unhealthy)
		reported := unhealthy
		if len(reported) > maxReportedApps {
			reported = reported[:maxReportedApps]
		}
		return doctorCheck{Name: "argocd", Status: "degraded", Summary: fmt.Sprintf("%d application(s) are not Synced and Healthy; first %d: %s", len(unhealthy), len(reported), strings.Join(reported, ", "))}
	}
	return doctorCheck{Name: "argocd", Status: "healthy", Summary: fmt.Sprintf("%d application(s) are Synced and Healthy", len(applications.Items))}
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
