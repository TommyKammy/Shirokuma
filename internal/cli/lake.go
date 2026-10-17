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
			return nil, fmt.Errorf("lake ingestion failed: %s", exit.Stderr)
		}
		return nil, err
	}
	return data, nil
}

func newLakeCommand(runner lakeRunner) *cobra.Command {
	group := &cobra.Command{Use: "lake", Short: "Load local files into the resident Iceberg lake"}
	var root, table, mode, format, key, schemaHints, encoding string
	var batchSize int
	command := &cobra.Command{
		Use: "ingest <file>", Short: "Ingest CSV, JSONL, or Parquet into Iceberg", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if (mode != "replace" && mode != "append" && mode != "upsert") ||
				(format != "auto" && format != "csv" && format != "jsonl" && format != "parquet") || batchSize < 1 {
				return errors.New("invalid ingestion mode, format, or batch-size (must be >= 1)")
			}
			if mode == "upsert" && strings.TrimSpace(key) == "" {
				return errors.New("upsert requires --key matching input columns")
			}
			forward := []string{"--mode", mode, "--format", format, "--batch-size", strconv.Itoa(batchSize)}
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
			if err != nil {
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
			return json.NewEncoder(cmd.OutOrStdout()).Encode(report)
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
	group.AddCommand(command)
	group.AddCommand(newLakeExportCommand(runner))
	return group
}

func newLakeExportCommand(runner lakeRunner) *cobra.Command {
	var root, output, format string
	var maxRows int
	var full bool
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
			if limitRows {
				forward = append(forward, "--max-rows", strconv.Itoa(maxRows))
			}
			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			data, err := runner.RunLake(ctx, root, forward)
			if err != nil {
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
			return json.NewEncoder(cmd.OutOrStdout()).Encode(report)
		},
	}
	command.Flags().StringVar(&output, "output", "", "destination file (required)")
	command.Flags().StringVar(&format, "format", "auto", "auto, csv, jsonl, or parquet")
	command.Flags().IntVar(&maxRows, "max-rows", 10000, "maximum rows to export (only when explicitly set; ignored with --full)")
	command.Flags().BoolVar(&full, "full", false, "export all rows (overrides --max-rows)")
	command.Flags().StringVar(&root, "repo-root", "", "repository root")
	return command
}
