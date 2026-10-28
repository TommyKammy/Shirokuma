package cli

import (
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
)

func newStageCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "stage", Short: "Manage database Stages through authenticated HTTP"}
	for _, action := range []string{"save", "list", "get", "files", "preview", "import", "export", "runs", "inspect", "cancel", "exports", "receipt", "download", "scan", "scans", "scan-inspect", "scan-retry"} {
		action := action
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		flags := cmd.Flags()
		values := map[string]*string{}
		add := func(name, fallback, help string, required bool) {
			values[name] = flags.String(name, fallback, help)
			if required {
				cmd.MarkFlagRequired(name)
			}
		}
		for _, name := range []string{"database", "schema", "context-file"} {
			add(name, "", "explicit selection or original full context file", false)
		}
		add("api-url", "http://127.0.0.1:8080", "local HTTP origin", false)
		if action != "list" {
			add("stage-id", "", "Stage UUID", action != "save")
		}
		mutation := action == "save" || action == "preview" || action == "import" || action == "export" || action == "cancel" || action == "scan" || action == "scan-retry"
		if mutation {
			add("idempotency-key", "", "stable caller-owned mutation key", true)
		}
		revisionAction := action == "save" || action == "preview" || action == "import" || action == "export" || action == "scan" || action == "scan-retry"
		var revision int
		if revisionAction {
			flags.IntVar(&revision, "expected-revision", -1, "0 creates; positive saved revision otherwise")
			cmd.MarkFlagRequired("expected-revision")
		}
		if action == "save" {
			add("definition", "", "Stage definition JSON file", true)
		}
		if action == "scan-inspect" || action == "scan-retry" {
			add("scan-id", "", "original scan UUID", true)
		}
		var attempt int
		if action == "scan-retry" {
			add("item-id", "", "explicit failed item UUID", true)
			flags.IntVar(&attempt, "expected-attempt", 0, "positive current item attempt CAS")
			cmd.MarkFlagRequired("expected-attempt")
		}
		if action == "preview" || action == "import" || action == "export" {
			add("file", "", "direct filename", true)
		}
		if action == "preview" || action == "import" {
			add("file-version", "", "listed file version", true)
		}
		if action == "import" {
			add("expected-preview-fingerprint", "", "explicit preview confirmation fingerprint", true)
		}
		if action == "export" {
			add("source", "", "stable ResourceRef JSON or @JSON-file", true)
			add("format", "", "csv or jsonl", true)
		}
		if action == "inspect" || action == "cancel" {
			add("run-id", "", "accepted run UUID", true)
		}
		if action == "receipt" || action == "download" {
			add("export-id", "", "published export UUID", true)
		}
		if action == "download" {
			add("output", "", "local output path", true)
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
				if flags.Changed(name) && strings.TrimSpace(*v) == "" {
					return fmt.Errorf("nonempty %s required", name)
				}
			}
			if revisionAction && (revision < 0 || action != "save" && revision < 1 || action == "save" && (value("stage-id") != "") != (revision > 0)) {
				return fmt.Errorf("invalid expected-revision and stage-id combination")
			}
			if action == "scan-retry" && attempt < 1 {
				return fmt.Errorf("positive expected-attempt required")
			}
			if values["file"] != nil && (strings.ContainsAny(value("file"), "/\\\x00\n\r") || strings.HasPrefix(value("file"), ".")) {
				return fmt.Errorf("direct filename required")
			}
			if action == "export" && value("format") != "csv" && value("format") != "jsonl" {
				return fmt.Errorf("csv/jsonl format required")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "stages_client.py"), action}
			for _, name := range []string{"api-url", "database", "schema", "context-file", "stage-id", "idempotency-key", "definition", "file", "file-version", "expected-preview-fingerprint", "source", "format", "run-id", "export-id", "output", "scan-id", "item-id"} {
				if value(name) != "" {
					args = append(args, "--"+name+"="+value(name))
				}
			}
			if revisionAction {
				args = append(args, "--expected-revision="+strconv.Itoa(revision))
			}
			if action == "scan-retry" {
				args = append(args, "--expected-attempt="+strconv.Itoa(attempt))
			}
			data, runErr := runner.Run(cmd.Context(), python, args...)
			// Accepted/unknown recovery output remains available even on CLI failure.
			if len(data) > 0 {
				if _, err := fmt.Fprint(cmd.OutOrStdout(), string(data)); err != nil {
					return err
				}
			}
			if runErr != nil {
				return fmt.Errorf("stage HTTP request failed (details withheld)")
			}
			return nil
		}
		parent.AddCommand(cmd)
	}
	return parent
}
