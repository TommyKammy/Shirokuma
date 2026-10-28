package cli

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

func newScheduleCommand(runner commandRunner) *cobra.Command {
	return newScheduleAlertCommand(runner, "schedule", []string{"list", "show", "save", "ticks"})
}
func newAlertCommand(runner commandRunner) *cobra.Command {
	return newScheduleAlertCommand(runner, "alert", []string{"list", "ack"})
}

func newScheduleAlertCommand(runner commandRunner, group string, actions []string) *cobra.Command {
	parent := &cobra.Command{Use: group, Short: "Manage database " + group + " through authenticated HTTP"}
	for _, action := range actions {
		action := action
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		values := map[string]*string{}
		names := []string{}
		add := func(name, fallback string, required bool) {
			names = append(names, name)
			values[name] = cmd.Flags().String(name, fallback, name)
			if required {
				cmd.MarkFlagRequired(name)
			}
		}
		add("api-url", "http://127.0.0.1:8080", false)
		for _, name := range []string{"database", "schema", "context-file"} {
			add(name, "", false)
		}
		if group == "schedule" && (action == "show" || action == "ticks") {
			add("schedule-id", "", true)
		}
		if group == "schedule" && action == "list" {
			add("target-kind", "", false)
			add("target-id", "", false)
		}
		if action == "save" {
			add("definition", "", true)
		}
		if action == "ack" {
			add("alert-id", "", true)
		}
		if action == "save" || action == "ack" {
			add("idempotency-key", "", true)
		}
		cmd.RunE = func(cmd *cobra.Command, _ []string) error {
			value := func(name string) string {
				if values[name] == nil {
					return ""
				}
				return *values[name]
			}
			if value("context-file") == "" && (value("database") == "" || value("schema") == "") {
				return fmt.Errorf("explicit database and schema or context-file required")
			}
			for name, v := range values {
				if cmd.Flags().Changed(name) && strings.TrimSpace(*v) == "" {
					return fmt.Errorf("nonempty %s required", name)
				}
			}
			if kind := value("target-kind"); kind != "" && kind != "job" && kind != "stage" && kind != "mart" {
				return fmt.Errorf("invalid target-kind")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "schedules_client.py"), group, action}
			for _, name := range names {
				if value(name) != "" {
					args = append(args, "--"+name+"="+value(name))
				}
			}
			data, runErr := runner.Run(cmd.Context(), python, args...)
			if len(data) > 0 {
				if _, err := fmt.Fprint(cmd.OutOrStdout(), string(data)); err != nil {
					return err
				}
			}
			if runErr != nil {
				return fmt.Errorf("%s HTTP request failed (details withheld)", group)
			}
			return nil
		}
		parent.AddCommand(cmd)
	}
	return parent
}
