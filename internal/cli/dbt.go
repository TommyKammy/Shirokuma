package cli

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

func newDbtCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "dbt", Short: "Compile SQL candidates, test and publish through authenticated HTTP", SilenceUsage: true, SilenceErrors: true}
	for _, action := range []string{"projects", "compile", "plans", "plan", "run", "runs", "status", "cancel", "reconcile", "publish", "publish-preview", "publication"} {
		action := action
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		flags := cmd.Flags()
		values := map[string]*string{}
		var nativeTests *bool
		names := []string{}
		add := func(name, fallback string, required bool) {
			names = append(names, name)
			values[name] = flags.String(name, fallback, name)
			if required {
				cmd.MarkFlagRequired(name)
			}
		}
		add("database", "", false)
		add("schema", "sandbox", false)
		add("api-url", "http://127.0.0.1:8080", false)
		add("context", "", false)
		add("context-file", "", false)
		mutation := action == "compile" || action == "run" || action == "cancel" || action == "reconcile" || action == "publish"
		if mutation {
			add("idempotency-key", "", false)
		}
		if action == "compile" {
			add("project", "lake_marts", false)
			add("source-schema", "raw", false)
			add("target-schema", "marts", false)
			add("staging-materialization", "default", false)
		}
		if action == "plan" || action == "run" || action == "runs" {
			add("plan-id", "", action != "runs")
		}
		if action == "run" {
			nativeTests = flags.Bool("native-tests", false, "Run attached quality tests with native TABLE")
			add("executor", "sql_wap", false)
			add("node-id", "", false)
		}
		if action == "status" || action == "cancel" || action == "reconcile" || action == "publish" || action == "publish-preview" {
			add("run-id", "", true)
		}
		if action == "publish" || action == "publish-preview" {
			add("node-id", "", true)
		}
		if action == "publish" {
			add("expected-target-file", "", false)
			flags.Lookup("expected-target-file").Usage = "JSON returned by publish-preview"
		}
		if action == "publication" {
			add("publication-id", "", true)
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
			saved := value("context") != "" || value("context-file") != ""
			if value("context") != "" && value("context-file") != "" || saved && (flags.Changed("database") || flags.Changed("schema")) {
				return fmt.Errorf("context conflicts with database/schema")
			}
			if action == "publish-preview" && !saved {
				return fmt.Errorf("explicit saved context required for publication review")
			}
			if !saved && value("database") == "" {
				return fmt.Errorf("explicit database or context required")
			}
			if action == "compile" {
				v := value("staging-materialization")
				if v != "default" && v != "table" && v != "ephemeral" {
					return fmt.Errorf("invalid staging-materialization")
				}
			}
			if action == "run" {
				if *nativeTests && value("executor") != "native_table" {
					return fmt.Errorf("native-tests requires native_table executor")
				}
				switch value("executor") {
				case "sql_wap":
					if value("node-id") != "" {
						return fmt.Errorf("node-id requires native_table executor")
					}
				case "native_table":
					if strings.TrimSpace(value("node-id")) == "" {
						return fmt.Errorf("nonempty node-id required for native_table executor")
					}
				default:
					return fmt.Errorf("invalid executor")
				}
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "dbt_client.py"), action}
			for _, name := range names {
				if action == "run" && value("executor") == "sql_wap" && (name == "executor" || name == "node-id") {
					continue
				}
				if saved && name == "schema" && !flags.Changed(name) {
					continue
				}
				if value(name) != "" {
					args = append(args, "--"+name+"="+value(name))
				}
			}
			if nativeTests != nil && *nativeTests {
				args = append(args, "--native-tests")
			}
			data, runErr := runner.Run(cmd.Context(), python, args...)
			if runErr != nil {
				return fmt.Errorf("dbt HTTP request failed (details withheld)")
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
