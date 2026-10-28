package cli

import (
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
)

func newJobCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "job", Short: "Save and manually run database Jobs through authenticated HTTP"}
	for _, action := range []string{"list", "save", "show", "run", "runs", "inspect", "cancel", "reconcile"} {
		action := action
		var database, schema, contextFile, apiURL, jobID, runID, key, definition, arguments string
		var revision int
		var inputs []string
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		flags := cmd.Flags()
		flags.StringVar(&database, "database", "", "explicit database ID or unambiguous SQL name")
		flags.StringVar(&schema, "schema", "", "explicit schema ID or unambiguous SQL name")
		flags.StringVar(&contextFile, "context-file", "", "original full resolved context JSON file")
		flags.StringVar(&apiURL, "api-url", "http://127.0.0.1:8888", "Workbench loopback HTTP origin")
		if action == "save" || action == "show" || action == "run" || action == "runs" {
			flags.StringVar(&jobID, "job-id", "", "Job UUID")
			if action == "show" || action == "run" {
				cmd.MarkFlagRequired("job-id")
			}
		}
		if action == "inspect" || action == "cancel" || action == "reconcile" {
			flags.StringVar(&runID, "run-id", "", "accepted run UUID")
			cmd.MarkFlagRequired("run-id")
		}
		if action == "save" || action == "run" {
			flags.IntVar(&revision, "expected-revision", -1, "0 creates; positive revision updates/runs")
			flags.StringVar(&key, "idempotency-key", "", "stable caller-owned mutation key")
			cmd.MarkFlagRequired("expected-revision")
			cmd.MarkFlagRequired("idempotency-key")
		}
		if action == "save" {
			flags.StringVar(&definition, "definition", "", "definition JSON file")
			cmd.MarkFlagRequired("definition")
		}
		if action == "run" {
			flags.StringVar(&arguments, "arguments", "", "JSON object or @path to JSON file")
			flags.StringArrayVar(&inputs, "input", nil, "name=path (repeatable; captured before POST)")
			cmd.MarkFlagRequired("arguments")
		}
		cmd.RunE = func(cmd *cobra.Command, _ []string) error {
			if contextFile == "" && (database == "" || schema == "") {
				return fmt.Errorf("explicit database and schema or context-file required")
			}
			if (action == "run" && revision < 1) || (action == "save" && (revision < 0 || (jobID != "") != (revision > 0))) {
				return fmt.Errorf("invalid expected-revision and job-id combination")
			}
			for _, name := range []string{"job-id", "run-id", "idempotency-key", "definition", "arguments", "context-file", "database", "schema"} {
				if flags.Lookup(name) != nil && flags.Changed(name) {
					v, _ := flags.GetString(name)
					if strings.TrimSpace(v) == "" {
						return fmt.Errorf("nonempty %s required", name)
					}
				}
			}
			seen := map[string]bool{}
			for _, input := range inputs {
				name, path, ok := strings.Cut(input, "=")
				if !ok || strings.TrimSpace(name) == "" || path == "" || seen[name] {
					return fmt.Errorf("unique input name=path required")
				}
				seen[name] = true
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "jobs_client.py"), action, "--api-url=" + apiURL}
			for _, flag := range []struct{ name, value string }{{"database", database}, {"schema", schema}, {"context-file", contextFile}, {"job-id", jobID}, {"run-id", runID}, {"idempotency-key", key}, {"definition", definition}, {"arguments", arguments}} {
				if flag.value != "" {
					args = append(args, "--"+flag.name+"="+flag.value)
				}
			}
			if action == "save" || action == "run" {
				args = append(args, "--expected-revision", strconv.Itoa(revision))
			}
			for _, input := range inputs {
				args = append(args, "--input="+input)
			}
			data, err := runner.Run(cmd.Context(), python, args...)
			if err != nil {
				return fmt.Errorf("job HTTP request failed (details withheld)")
			}
			_, err = fmt.Fprint(cmd.OutOrStdout(), string(data))
			return err
		}
		parent.AddCommand(cmd)
	}
	return parent
}
