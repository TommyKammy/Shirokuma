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

type bearRunner interface {
	RunBear(context.Context, string, []string) ([]byte, error)
}

func (execResidentWarehouse) RunBear(ctx context.Context, explicit string, args []string) ([]byte, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return nil, err
	}
	command := exec.CommandContext(ctx, "python3", append([]string{filepath.Join(root, "tools", "local_experiment", "bears_resident.py")}, args...)...)
	command.Dir = root
	data, err := command.Output()
	if err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			return nil, fmt.Errorf("resident bear failed: %s %s", data, exit.Stderr)
		}
		return nil, err
	}
	return data, nil
}

func newBearCommand(runner bearRunner) *cobra.Command {
	group := &cobra.Command{Use: "bear", Short: "Inspect and advise on the resident lakehouse"}
	for _, name := range []string{"sre", "query", "data", "catalog", "policy"} {
		name := name
		var root, sql string
		wapFlags := []string{"stage-table", "publish", "rollback", "snapshot-id"}
		command := &cobra.Command{
			Use: name + " [arguments]", Short: "Run the resident " + name + " Bear",
			RunE: func(cmd *cobra.Command, args []string) error {
				forward := append([]string{name}, args...)
				if cmd.Flags().Changed("sql") {
					forward = append(forward, "--sql", sql)
				}
				for _, flag := range wapFlags {
					if cmd.Flags().Changed(flag) {
						value, _ := cmd.Flags().GetString(flag)
						forward = append(forward, "--"+flag, value)
					}
				}
				ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
				defer stop()
				data, err := runner.RunBear(ctx, root, forward)
				if err != nil {
					return err
				}
				var report map[string]json.RawMessage
				if json.Unmarshal(data, &report) != nil || report == nil {
					return errors.New("resident bear returned invalid JSON")
				}
				var bear string
				_ = json.Unmarshal(report["bear"], &bear)
				expected := map[string]string{"sre": "sre", "query": "query_doctor", "data": "data_engineer", "catalog": "catalog_curator", "policy": "policy_gatekeeper"}
				if bear != expected[name] {
					return errors.New("resident bear response does not match request")
				}
				return json.NewEncoder(cmd.OutOrStdout()).Encode(report)
			},
		}
		command.Flags().StringVar(&root, "repo-root", "", "repository root")
		if name == "query" || name == "data" {
			command.Flags().StringVar(&sql, "sql", "", "SQL to explain")
		}
		if name == "data" {
			for _, flag := range wapFlags {
				command.Flags().String(flag, "", "Data Bear "+flag)
			}
		}
		group.AddCommand(command)
	}
	return group
}
