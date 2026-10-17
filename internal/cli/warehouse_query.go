package cli

import (
	"errors"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
)

func newWarehouseQueryCommand(resident residentWarehouseRunner) *cobra.Command {
	group := &cobra.Command{Use: "warehouse", Short: "Control and query a local warehouse"}
	var profile, sql, catalog, schema, repoRoot, mode string
	var noAutoWake bool
	var maxRows int
	command := &cobra.Command{
		Use: "query <XS|S|LOCAL> [SQL]", Short: "Query the resident Trino warehouse (XS/S/LOCAL share one backend)",
		Args: cobra.RangeArgs(1, 2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if mode != "" && mode != "read-only" && mode != "sandbox-write" && mode != "admin-write" {
				return errors.New("invalid SQL guardrail mode")
			}
			if maxRows < 1 {
				return errors.New("--max-rows must be >= 1")
			}
			if profile != localExperimentProfile || !validResidentWarehouse(args[0]) ||
				(len(args) == 2 && cmd.Flags().Changed("sql")) {
				return errors.New("invalid resident warehouse query arguments")
			}
			if len(args) == 2 {
				sql = args[1]
			}
			if strings.TrimSpace(sql) == "" {
				return errors.New("resident warehouse query requires SQL")
			}
			childArgs := []string{"query", "--warehouse", args[0], "--sql=" + sql, "--max-rows", strconv.Itoa(maxRows)}
			if mode != "" {
				childArgs = append(childArgs, "--mode", mode)
			}
			for _, option := range []struct{ name, value string }{{"catalog", catalog}, {"schema", schema}} {
				if option.value != "" {
					childArgs = append(childArgs, "--"+option.name, option.value)
				}
			}
			if noAutoWake {
				childArgs = append(childArgs, "--no-auto-wake")
			}
			return runResidentWarehouse(cmd, resident, repoRoot, childArgs)
		},
	}
	command.Flags().StringVar(&profile, "profile", localExperimentProfile, "warehouse profile: local-experiment")
	command.Flags().StringVar(&sql, "sql", "", "SQL to execute against resident Trino")
	command.Flags().StringVar(&catalog, "catalog", "", "Trino catalog")
	command.Flags().StringVar(&schema, "schema", "", "Trino schema")
	command.Flags().StringVar(&mode, "mode", "", "SQL guardrail mode: read-only, sandbox-write, admin-write")
	command.Flags().StringVar(&repoRoot, "repo-root", "", "repository root")
	command.Flags().BoolVar(&noAutoWake, "no-auto-wake", false, "fail if the resident warehouse is chilled")
	command.Flags().IntVar(&maxRows, "max-rows", 1000, "maximum resident query result rows")
	group.AddCommand(command)
	for _, action := range []string{"status", "chill", "wake"} {
		group.AddCommand(newResidentWarehouseCommand(action, resident))
	}
	return group
}
