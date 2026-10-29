package cli

import (
	"fmt"
	"github.com/spf13/cobra"
	"path/filepath"
	"strconv"
	"strings"
)

func newWarehouseRuntimeCommand(runner commandRunner) *cobra.Command {
	group := &cobra.Command{Use: "runtime", Short: "Manage shared LOCAL runtime through authenticated HTTP"}
	for _, action := range []string{"status", "wake", "chill", "autochill", "operation", "reconcile"} {
		action := action
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		api := cmd.Flags().String("api-url", "http://127.0.0.1:8888", "Workbench HTTP origin")
		role := cmd.Flags().String("role", "", "Selected role")
		key, id := "", ""
		minutes, revision := 0, 0
		if action != "status" && action != "operation" {
			cmd.Flags().StringVar(&key, "key", "", "Idempotency key")
			_ = cmd.MarkFlagRequired("key")
		}
		if action == "operation" {
			cmd.Flags().StringVar(&id, "id", "", "Operation ID")
			_ = cmd.MarkFlagRequired("id")
		}
		if action == "autochill" {
			cmd.Flags().IntVar(&minutes, "minutes", 0, "Idle minutes, zero disables")
			cmd.Flags().IntVar(&revision, "expected-revision", 0, "Saved policy revision")
			_ = cmd.MarkFlagRequired("minutes")
			_ = cmd.MarkFlagRequired("expected-revision")
		}
		cmd.RunE = func(cmd *cobra.Command, _ []string) error {
			if strings.TrimSpace(*api) == "" || (*role != "" && *role != "admin" && *role != "data_engineer" && *role != "bi_viewer") {
				return fmt.Errorf("invalid runtime HTTP options")
			}
			if (action != "status" && action != "operation" && strings.TrimSpace(key) == "") || (action == "operation" && strings.TrimSpace(id) == "") {
				return fmt.Errorf("explicit identifier required")
			}
			if action == "autochill" && (minutes < 0 || minutes > 1440 || revision < 0) {
				return fmt.Errorf("invalid policy")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "warehouse_runtime_client.py"), action, "--api-url=" + *api}
			if *role != "" {
				args = append(args, "--role="+*role)
			}
			if key != "" {
				args = append(args, "--key="+key)
			}
			if id != "" {
				args = append(args, "--id="+id)
			}
			if action == "autochill" {
				args = append(args, "--minutes="+strconv.Itoa(minutes), "--expected-revision="+strconv.Itoa(revision))
			}
			data, err := runner.Run(cmd.Context(), python, args...)
			if err != nil {
				return fmt.Errorf("warehouse runtime HTTP request failed (details withheld)")
			}
			_, err = fmt.Fprint(cmd.OutOrStdout(), string(data))
			return err
		}
		group.AddCommand(cmd)
	}
	return group
}
