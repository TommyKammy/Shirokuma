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
	"strconv"
	"strings"
	"syscall"

	"github.com/spf13/cobra"
)

type lakeRunner interface {
	RunLake(context.Context, string, []string) ([]byte, error)
}

func (execResidentWarehouse) RunLake(ctx context.Context, explicit string, args []string) ([]byte, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return nil, err
	}
	command := exec.CommandContext(ctx, "python3", append([]string{filepath.Join(root, "tools", "local_experiment", "lake_ingest.py")}, args...)...)
	// Keep the caller's working directory so relative input paths retain their meaning.
	data, err := command.Output()
	if err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			return data, fmt.Errorf("lake command failed: %s: %w", exit.Stderr, err)
		}
		return nil, err
	}
	return data, nil
}

func newLakeCommand(runner lakeRunner) *cobra.Command {
	group := &cobra.Command{Use: "lake", Short: "Load local files into the resident Iceberg lake"}
	var root, table, mode, format, key, schemaHints, encoding string
	var batchSize int
	var atomicAppend, preview bool
	databaseOptions := &lakeDatabaseOptions{}
	command := &cobra.Command{
		Use: "ingest <file>", Short: "Ingest CSV, JSONL, or Parquet into Iceberg", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if (mode != "create" && mode != "replace" && mode != "append" && mode != "upsert") ||
				(format != "auto" && format != "csv" && format != "json" && format != "jsonl" && format != "parquet") || batchSize < 1 {
				return errors.New("invalid ingestion mode, format, or batch-size (must be >= 1)")
			}
			if mode == "upsert" && strings.TrimSpace(key) == "" {
				return errors.New("upsert requires --key matching input columns")
			}
			forward := []string{"--mode", mode, "--format", format, "--batch-size", strconv.Itoa(batchSize)}
			if err := databaseOptions.validate(!preview); err != nil {
				return err
			}
			if databaseOptions.database != "" && (!cmd.Flags().Changed("mode") || table == "" || format == "parquet") {
				return errors.New("database ingest requires explicit --mode and --table and CSV/JSON input")
			}
			forward = append(forward, databaseOptions.forward()...)
			if atomicAppend {
				forward = append(forward, "--atomic-append")
			}
			if preview {
				forward = append(forward, "--preview")
			}
			if table != "" {
				forward = append(forward, "--table", table)
			}
			if key != "" || cmd.Flags().Changed("key") {
				forward = append(forward, "--key", key)
			}
			if schemaHints != "" || cmd.Flags().Changed("schema-hints") {
				forward = append(forward, "--schema-hints", schemaHints)
			}
			if encoding != "" || cmd.Flags().Changed("encoding") {
				forward = append(forward, "--encoding", encoding)
			}
			forward = append(forward, "--", args[0])
			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			data, err := runner.RunLake(ctx, root, forward)
			if err != nil && len(data) == 0 {
				return err
			}
			var report map[string]json.RawMessage
			if json.Unmarshal(data, &report) != nil || report == nil {
				return errors.New("lake ingestion returned invalid JSON")
			}
			var action string
			if json.Unmarshal(report["action"], &action) != nil || action != "ingest" {
				return errors.New("lake ingestion response does not match request")
			}
			if writeErr := json.NewEncoder(cmd.OutOrStdout()).Encode(report); writeErr != nil {
				return writeErr
			}
			return err
		},
	}
	command.Flags().StringVar(&table, "table", "", "target table (default: iceberg.raw.<file stem>)")
	command.Flags().StringVar(&mode, "mode", "replace", "replace, append, or upsert")
	command.Flags().StringVar(&key, "key", "", "comma-separated input key columns (required for upsert)")
	command.Flags().StringVar(&format, "format", "auto", "auto, csv, jsonl, or parquet")
	command.Flags().StringVar(&schemaHints, "schema-hints", "", "JSON string of schema hints")
	command.Flags().StringVar(&encoding, "encoding", "", "input file encoding")
	command.Flags().IntVar(&batchSize, "batch-size", 500, "rows per write batch")
	command.Flags().StringVar(&root, "repo-root", "", "repository root")
	command.Flags().BoolVar(&atomicAppend, "atomic-append", false, "publish append atomically in database mode")
	command.Flags().BoolVar(&preview, "preview", false, "validate and preview input without execution")
	databaseOptions.flags(command)
	group.AddCommand(command)
	group.AddCommand(newLakeExportCommand(runner))
	group.AddCommand(newLakeDataOpsGroup(runner, "wap"))
	group.AddCommand(newLakeDataOpsGroup(runner, "operation"))
	return group
}

func newLakeExportCommand(runner lakeRunner) *cobra.Command {
	var root, output, format string
	var maxRows int
	var full bool
	databaseOptions := &lakeDatabaseOptions{}
	command := &cobra.Command{
		Use: "export <table>", Short: "Export an Iceberg table to CSV, JSONL, or Parquet", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if strings.TrimSpace(output) == "" {
				return errors.New("export requires --output")
			}
			limitRows := !full && cmd.Flags().Changed("max-rows")
			if (format != "auto" && format != "csv" && format != "jsonl" && format != "parquet") || (limitRows && maxRows < 1) {
				return errors.New("invalid export format or max-rows (must be >= 1)")
			}
			forward := []string{"--export", args[0], "--output", output, "--format", format}
			if err := databaseOptions.validate(true); err != nil {
				return err
			}
			if databaseOptions.database != "" && (cmd.Flags().Changed("max-rows") || format == "parquet") {
				return errors.New("database exports support full CSV/JSONL only")
			}
			forward = append(forward, databaseOptions.forward()...)
			if limitRows {
				forward = append(forward, "--max-rows", strconv.Itoa(maxRows))
			}
			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			data, err := runner.RunLake(ctx, root, forward)
			if err != nil && len(data) == 0 {
				return err
			}
			var report map[string]json.RawMessage
			if json.Unmarshal(data, &report) != nil || report == nil {
				return errors.New("lake export returned invalid JSON")
			}
			var action string
			if json.Unmarshal(report["action"], &action) != nil || action != "export" {
				return errors.New("lake export response does not match request")
			}
			if writeErr := json.NewEncoder(cmd.OutOrStdout()).Encode(report); writeErr != nil {
				return writeErr
			}
			return err
		},
	}
	command.Flags().StringVar(&output, "output", "", "destination file (required)")
	command.Flags().StringVar(&format, "format", "auto", "auto, csv, jsonl, or parquet")
	command.Flags().IntVar(&maxRows, "max-rows", 10000, "maximum rows to export (only when explicitly set; ignored with --full)")
	command.Flags().BoolVar(&full, "full", false, "export all rows (overrides --max-rows)")
	command.Flags().StringVar(&root, "repo-root", "", "repository root")
	databaseOptions.flags(command)
	return command
}

type lakeDatabaseOptions struct{ database, schema, apiURL, key, delimiter, fingerprint string }

func (o *lakeDatabaseOptions) flags(cmd *cobra.Command) {
	cmd.Flags().StringVar(&o.database, "database", "", "explicit database SQL catalog or ID")
	cmd.Flags().StringVar(&o.schema, "schema", "", "explicit schema SQL name or ID")
	cmd.Flags().StringVar(&o.apiURL, "api-url", "", "local authenticated HTTP API origin (default http://127.0.0.1:8888)")
	cmd.Flags().StringVar(&o.key, "idempotency-key", "", "required operation idempotency key")
	cmd.Flags().StringVar(&o.delimiter, "delimiter", "", "input CSV delimiter")
	cmd.Flags().StringVar(&o.fingerprint, "expected-preview-fingerprint", "", "confirm the exact preview request")
}

func (o *lakeDatabaseOptions) validate(submission bool) error {
	if o.database == "" && o.schema == "" && o.apiURL == "" && o.key == "" {
		return nil
	}
	if strings.TrimSpace(o.database) == "" || strings.TrimSpace(o.schema) == "" || submission && strings.TrimSpace(o.key) == "" {
		return errors.New("database operations require --database, --schema and submissions require --idempotency-key")
	}
	return nil
}

func (o *lakeDatabaseOptions) forward() []string {
	var out []string
	for _, pair := range [][2]string{{"--database", o.database}, {"--schema", o.schema}, {"--api-url", o.apiURL}, {"--idempotency-key", o.key}, {"--delimiter", o.delimiter}, {"--expected-preview-fingerprint", o.fingerprint}} {
		if pair[1] != "" {
			out = append(out, pair[0], pair[1])
		}
	}
	return out
}

func newLakeDataOpsGroup(runner lakeRunner, groupName string) *cobra.Command {
	group := &cobra.Command{Use: groupName, Short: "Authenticated database " + groupName + " operations"}
	actions := []string{"save", "models", "model", "build", "audit", "publish", "rollback", "get-build", "get-audit", "get-publication"}
	if groupName == "operation" {
		actions = []string{"get", "cancel"}
	}
	for _, action := range actions {
		action := action
		o := &lakeDatabaseOptions{}
		var root string
		values := map[string]*string{}
		cmd := &cobra.Command{Use: action, Args: func(cmd *cobra.Command, args []string) error {
			if groupName == "operation" {
				return cobra.ExactArgs(1)(cmd, args)
			}
			return cobra.NoArgs(cmd, args)
		}, RunE: func(cmd *cobra.Command, args []string) error {
			if groupName == "operation" && cmd.Flags().Changed("id") {
				return errors.New("operation ID must be supplied as the positional argument")
			}
			submission := groupName == "wap" && (action == "build" || action == "audit" || action == "publish" || action == "rollback")
			if groupName != "operation" && (o.database == "" || o.schema == "") {
				return errors.New("explicit --database and --schema required")
			}
			if err := o.validate(submission); err != nil && groupName != "operation" {
				return err
			}
			required := map[string][]string{
				"save":  {"model-id", "sql", "sources", "target", "expected-version"},
				"build": {"model-id", "version"}, "audit": {"build-id"},
				"publish": {"build-id", "audit-id"}, "rollback": {"publication-id"},
				"get-build": {"id"}, "get-audit": {"id"}, "get-publication": {"id"},
				"get": {"context-file"}, "cancel": {"context-file"},
			}
			for _, name := range required[action] {
				if !cmd.Flags().Changed(name) || strings.TrimSpace(*values[name]) == "" {
					return fmt.Errorf("%s requires --%s", action, name)
				}
			}
			forward := append([]string{"--" + groupName, action}, o.forward()...)
			if groupName == "operation" {
				forward = append(forward, "--id", args[0])
			}
			names := []string{"id", "model-id", "version", "expected-version", "sql", "sources", "target", "min-rows", "not-null", "build-id", "audit-id", "publication-id", "context", "context-file"}
			for _, name := range names {
				if cmd.Flags().Changed(name) {
					forward = append(forward, "--"+name, *values[name])
				}
			}
			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			data, err := runner.RunLake(ctx, root, forward)
			if err != nil && len(data) == 0 {
				return err
			}
			var report map[string]json.RawMessage
			if json.Unmarshal(data, &report) != nil || report == nil || report["context"] == nil {
				return errors.New("invalid DataOps response")
			}
			var responseAction string
			if json.Unmarshal(report["action"], &responseAction) != nil || responseAction != groupName+"-"+action {
				return errors.New("DataOps response does not match request")
			}
			field := "operation"
			if groupName == "wap" {
				switch action {
				case "save":
					field = "model"
				case "models", "model":
					field = "items"
					if cmd.Flags().Changed("model-id") {
						field = "model"
					}
				case "get-build", "get-audit", "get-publication":
					field = strings.TrimPrefix(action, "get-")
				}
			}
			if err != nil && report["operation"] != nil {
				field = "operation"
			}
			if report[field] == nil || string(report[field]) == "null" {
				return errors.New("DataOps response does not match request")
			}
			if writeErr := json.NewEncoder(cmd.OutOrStdout()).Encode(report); writeErr != nil {
				return writeErr
			}
			return err
		}}
		if groupName == "operation" {
			cmd.Use = action + " <id>"
		}
		o.flags(cmd)
		cmd.Flags().StringVar(&root, "repo-root", "", "repository root")
		for _, name := range []string{"id", "model-id", "version", "expected-version", "sql", "sources", "target", "min-rows", "not-null", "build-id", "audit-id", "publication-id", "context", "context-file"} {
			values[name] = cmd.Flags().String(name, "", "explicit "+name+" (structured values use JSON)")
		}
		group.AddCommand(cmd)
	}
	return group
}
