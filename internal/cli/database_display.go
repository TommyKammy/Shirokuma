package cli

import (
	"fmt"
	"path/filepath"
	"strconv"

	"github.com/spf13/cobra"
)

func newDatabaseDisplayCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "display", Short: "Manage database display metadata through authenticated HTTP"}
	for _, action := range []string{"list", "set", "schema", "operation"} {
		action := action
		var database, schema, name, description, key, operation, apiURL string
		var revision int
		command := &cobra.Command{Use: action, Args: cobra.NoArgs}
		command.Flags().StringVar(&apiURL, "api-url", "http://127.0.0.1:8888", "Workbench HTTP origin")
		if action == "operation" {
			command.Flags().StringVar(&operation, "operation-id", "", "metadata operation identifier")
			command.MarkFlagRequired("operation-id")
		}
		if action == "set" || action == "schema" {
			if action == "set" {
				command.Flags().StringVar(&database, "database-id", "", "database identifier")
				command.Flags().StringVar(&description, "description", "", "database description (may be empty)")
				command.MarkFlagRequired("database-id")
				command.MarkFlagRequired("description")
			} else {
				command.Flags().StringVar(&schema, "schema-id", "", "schema identifier")
				command.MarkFlagRequired("schema-id")
			}
			command.Flags().StringVar(&name, "display-name", "", "display label")
			command.Flags().StringVar(&key, "idempotency-key", "", "mutation idempotency key")
			command.Flags().IntVar(&revision, "expected-revision", 0, "current metadata revision")
			for _, flag := range []string{"display-name", "idempotency-key", "expected-revision"} {
				command.MarkFlagRequired(flag)
			}
		}
		command.RunE = func(cmd *cobra.Command, _ []string) error {
			if (action == "set" || action == "schema") && revision < 1 {
				return fmt.Errorf("expected-revision must be positive")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "display_client.py"), action, "--api-url", apiURL}
			for _, flag := range []struct{ name, value string }{{"database-id", database}, {"schema-id", schema}, {"operation-id", operation}} {
				if flag.value != "" {
					args = append(args, "--"+flag.name, flag.value)
				}
			}
			if action == "set" || action == "schema" {
				args = append(args, "--display-name="+name, "--idempotency-key="+key, "--expected-revision", strconv.Itoa(revision))
			}
			if action == "set" {
				args = append(args, "--description="+description)
			}
			data, err := runner.Run(cmd.Context(), "python3", args...)
			if err != nil {
				return fmt.Errorf("display HTTP request failed (details withheld)")
			}
			_, err = fmt.Fprint(cmd.OutOrStdout(), string(data))
			return err
		}
		parent.AddCommand(command)
	}
	return parent
}
