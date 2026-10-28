package cli

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

func newAuditCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "audit", Short: "Read persisted audit metadata through authenticated HTTP"}
	for _, action := range []string{"list", "show", "export", "telemetry"} {
		action := action
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		names := []string{"api-url", "role"}
		values := map[string]*string{}
		values["api-url"] = cmd.Flags().String("api-url", "http://127.0.0.1:8888", "Workbench HTTP origin")
		values["role"] = cmd.Flags().String("role", "", "Selected role within authenticated authority")
		add := func(name, fallback string, required bool) {
			names = append(names, name)
			values[name] = cmd.Flags().String(name, fallback, name)
			if required {
				_ = cmd.MarkFlagRequired(name)
			}
		}
		if action != "telemetry" {
			add("scope", "self", false)
			add("database", "", false)
		}
		if action == "show" || action == "export" {
			add("id", "", true)
		}
		if action == "export" {
			add("output", "", true)
		}
		if action == "list" {
			for _, name := range []string{"source", "state", "search", "from", "to", "cursor"} {
				add(name, "", false)
			}
		}
		limit := 0
		if action == "list" || action == "telemetry" {
			fallback := 50
			if action == "telemetry" {
				fallback = 100
			}
			cmd.Flags().IntVar(&limit, "limit", fallback, "Maximum metadata rows (1-100)")
		}
		cmd.RunE = func(cmd *cobra.Command, _ []string) error {
			for name, value := range values {
				if cmd.Flags().Changed(name) && strings.TrimSpace(*value) == "" {
					return fmt.Errorf("nonempty %s required", name)
				}
			}
			role := *values["role"]
			if role != "" && role != "admin" && role != "data_engineer" && role != "bi_viewer" {
				return fmt.Errorf("invalid role")
			}
			if action != "telemetry" {
				scope := *values["scope"]
				if scope != "self" && scope != "database" && scope != "platform" {
					return fmt.Errorf("invalid scope")
				}
				if scope != "platform" && strings.TrimSpace(*values["database"]) == "" {
					return fmt.Errorf("explicit database ID required")
				}
			}
			if (action == "list" || action == "telemetry") && (limit < 1 || limit > 100) {
				return fmt.Errorf("limit must be between 1 and 100")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "audit_client.py"), action}
			for _, name := range names {
				if *values[name] != "" {
					args = append(args, "--"+name+"="+*values[name])
				}
			}
			if action == "list" || action == "telemetry" {
				args = append(args, fmt.Sprintf("--limit=%d", limit))
			}
			data, runErr := runner.Run(cmd.Context(), python, args...)
			if len(data) > 0 {
				if _, err := fmt.Fprint(cmd.OutOrStdout(), string(data)); err != nil {
					return err
				}
			}
			if runErr != nil {
				return fmt.Errorf("audit HTTP request failed (details withheld)")
			}
			return nil
		}
		parent.AddCommand(cmd)
	}
	return parent
}
