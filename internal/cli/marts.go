package cli

import (
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
)

func newMartCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "mart", Short: "Manage database Marts through authenticated HTTP", SilenceUsage: true, SilenceErrors: true}
	for _, action := range []string{"save", "list", "get", "check", "refresh", "runs", "inspect"} {
		action := action
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		flags := cmd.Flags()
		values := map[string]*string{}
		add := func(name, fallback string, required bool) {
			values[name] = flags.String(name, fallback, name)
			if required {
				cmd.MarkFlagRequired(name)
			}
		}
		for _, name := range []string{"database", "schema", "context-file"} {
			add(name, "", false)
		}
		add("api-url", "http://127.0.0.1:8080", false)
		if action != "list" {
			add("mart-id", "", action != "save")
		}
		mutation := action == "save" || action == "check" || action == "refresh"
		revision := -1
		if mutation {
			add("idempotency-key", "", true)
			flags.IntVar(&revision, "expected-revision", -1, "0 creates; positive saved revision otherwise")
			cmd.MarkFlagRequired("expected-revision")
		}
		if action == "save" {
			add("definition", "", true)
		}
		if action == "inspect" {
			add("run-id", "", true)
		}
		checkChanges := false
		if action == "refresh" {
			flags.BoolVar(&checkChanges, "check-changes", false, "refresh only when watched sources changed")
		}
		cmd.RunE = func(cmd *cobra.Command, _ []string) error {
			value := func(name string) string {
				if values[name] == nil {
					return ""
				}
				return *values[name]
			}
			for name, v := range values {
				if flags.Changed(name) && strings.TrimSpace(*v) == "" {
					return fmt.Errorf("nonempty %s required", name)
				}
			}
			if value("context-file") != "" && (flags.Changed("database") || flags.Changed("schema")) {
				return fmt.Errorf("context-file conflicts with database/schema")
			}
			if value("context-file") == "" && (strings.TrimSpace(value("database")) == "" || strings.TrimSpace(value("schema")) == "") {
				return fmt.Errorf("explicit database/schema or context-file required")
			}
			if mutation && (revision < 0 || action != "save" && revision < 1 || action == "save" && (value("mart-id") != "") != (revision > 0)) {
				return fmt.Errorf("invalid expected-revision/mart-id")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "marts_client.py"), action}
			for _, name := range []string{"api-url", "database", "schema", "context-file", "mart-id", "definition", "run-id", "idempotency-key"} {
				if value(name) != "" {
					args = append(args, "--"+name+"="+value(name))
				}
			}
			if mutation {
				args = append(args, "--expected-revision="+strconv.Itoa(revision))
			}
			if action == "refresh" {
				args = append(args, "--check-changes="+strconv.FormatBool(checkChanges))
			}
			data, runErr := runner.Run(cmd.Context(), python, args...)
			if runErr != nil {
				return fmt.Errorf("mart HTTP request failed (details withheld)")
			}
			if len(data) > 0 {
				_, err = fmt.Fprint(cmd.OutOrStdout(), string(data))
			}
			return err
		}
		parent.AddCommand(cmd)
	}
	return parent
}
