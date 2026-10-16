package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/spf13/cobra"
)

type residentWarehouseRunner interface {
	Run(context.Context, string, []string) ([]byte, error)
}

type execResidentWarehouse struct{}

func (execResidentWarehouse) Run(ctx context.Context, explicit string, args []string) ([]byte, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return nil, err
	}
	command := exec.CommandContext(ctx, "python3", append([]string{filepath.Join(root, "tools", "local_experiment", "warehouse_resident.py")}, args...)...)
	command.Dir = root
	data, err := command.Output()
	if err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			return nil, fmt.Errorf("resident warehouse failed: %s", exit.Stderr)
		}
		return nil, err
	}
	return data, nil
}

func validResidentWarehouse(size string) bool {
	return size == "XS" || size == "S" || size == "LOCAL"
}

func runResidentWarehouse(cmd *cobra.Command, runner residentWarehouseRunner, root string, args []string) error {
	ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	data, err := runner.Run(ctx, root, args)
	if err != nil {
		return err
	}
	var report map[string]json.RawMessage
	if json.Unmarshal(data, &report) != nil || report == nil {
		return errors.New("resident warehouse returned invalid JSON")
	}
	var size, action, state, result string
	_ = json.Unmarshal(report["warehouse"], &size)
	_ = json.Unmarshal(report["action"], &action)
	_ = json.Unmarshal(report["state"], &state)
	_ = json.Unmarshal(report["result"], &result)
	if size != args[2] || (args[0] == "status" && state != "active" && state != "chilled" && state != "starting") ||
		(args[0] != "status" && (action != args[0] || (result != "verified" && result != "noop"))) {
		return errors.New("resident warehouse response does not match request")
	}
	return json.NewEncoder(cmd.OutOrStdout()).Encode(report)
}

func newResidentWarehouseCommand(action string, runner residentWarehouseRunner) *cobra.Command {
	var profile, root string
	command := &cobra.Command{
		Use: action + " [XS|S|LOCAL]", Short: action + " the resident Trino warehouse (XS/S/LOCAL share one backend)",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			size := "LOCAL"
			if len(args) == 1 {
				size = args[0]
			}
			if profile != localExperimentProfile || !validResidentWarehouse(size) {
				return errors.New("resident warehouse requires XS, S, or LOCAL and --profile local-experiment")
			}
			return runResidentWarehouse(cmd, runner, root, []string{action, "--warehouse", size})
		},
	}
	command.Flags().StringVar(&profile, "profile", localExperimentProfile, "warehouse profile: local-experiment")
	command.Flags().StringVar(&root, "repo-root", "", "repository root")
	return command
}
