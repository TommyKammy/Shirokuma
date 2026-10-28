package cli

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
)

// Python owns authentication and the private operator; help never loads credentials.
func newDatabaseCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "database", Short: "Manage public generations and one-shot warehouse operations"}
	parent.AddCommand(newDatabaseGrantCommand(runner))
	parent.AddCommand(newDatabaseDisplayCommand(runner))
	parent.AddCommand(newDatabaseCreationCommand(runner))
	addDatabaseRetirementCommands(parent, runner)
	for _, action := range []string{"create", "register-spec", "export-spec", "render", "check", "plan", "apply", "inspect-operation", "reconcile", "reconcile-query"} {
		action := action
		var config, operationID, key, step, spec, output, bundle, root string
		var queryID, authHeaders string
		var sqlCatalog, profileID, referenceSpec, generationID string
		var revision int
		var verifyReadonly bool
		var deliveryDigest, receiptDigest string
		command := &cobra.Command{Use: action, Short: action + " a database generation or operation", Args: cobra.NoArgs}
		if action == "create" {
			command.Flags().StringVar(&sqlCatalog, "sql-catalog", "", "SQL database name")
			command.Flags().StringVar(&profileID, "profile-id", "local-polaris-1.8.0", "registered local profile")
			command.Flags().StringVar(&key, "idempotency-key", "", "create idempotency key")
			command.Flags().StringVar(&authHeaders, "auth-headers", "", "private current authentication headers file")
			command.MarkFlagRequired("sql-catalog")
			command.MarkFlagRequired("idempotency-key")
			command.MarkFlagRequired("auth-headers")
		} else if action == "register-spec" {
			command.Flags().StringVar(&spec, "spec", "", "approved public database specification JSON")
			command.Flags().StringVar(&key, "idempotency-key", "", "registration idempotency key")
			command.Flags().StringVar(&authHeaders, "auth-headers", "", "private current authentication headers file")
			command.MarkFlagRequired("spec")
			command.MarkFlagRequired("idempotency-key")
			command.MarkFlagRequired("auth-headers")
		} else if action == "export-spec" {
			command.Flags().StringVar(&referenceSpec, "reference-spec", "", "approved public profile and existing Secret references")
			command.Flags().StringVar(&generationID, "generation-id", "", "new frozen generation identifier")
			command.MarkFlagRequired("reference-spec")
			command.MarkFlagRequired("generation-id")
		} else if action == "render" {
			command.Flags().StringVar(&spec, "spec", "", "public specification JSON")
			command.Flags().StringVar(&output, "output", "", "new public bundle directory")
			command.MarkFlagRequired("spec")
			command.MarkFlagRequired("output")
		} else if action == "check" {
			command.Flags().StringVar(&bundle, "bundle", "", "public bundle directory")
			command.MarkFlagRequired("bundle")
		} else if action == "reconcile-query" || action == "inspect-operation" {
			if action == "reconcile-query" {
				command.Flags().StringVar(&queryID, "query-id", "", "UNKNOWN query identifier")
				command.MarkFlagRequired("query-id")
			} else {
				command.Flags().StringVar(&operationID, "operation-id", "", "operation identifier")
				command.MarkFlagRequired("operation-id")
			}
			command.Flags().StringVar(&authHeaders, "auth-headers", "", "private current authentication headers file")
			command.MarkFlagRequired("auth-headers")
		} else {
			command.Flags().StringVar(&config, "config", "", "explicit private operator JSON file")
			command.MarkFlagRequired("config")
			if action == "plan" {
				command.Flags().StringVar(&key, "idempotency-key", "", "plan idempotency key")
				command.MarkFlagRequired("idempotency-key")
			} else {
				command.Flags().StringVar(&operationID, "operation-id", "", "operation identifier")
				command.MarkFlagRequired("operation-id")
			}
			if action == "apply" {
				command.Flags().IntVar(&revision, "expected-revision", 0, "inspected operation revision")
				command.MarkFlagRequired("expected-revision")
			}
			if action == "reconcile" {
				command.Flags().BoolVar(&verifyReadonly, "verify-readonly", false, "claim one fresh read-only standalone verification")
				command.Flags().IntVar(&revision, "expected-revision", 0, "inspected operation revision")
				command.Flags().StringVar(&deliveryDigest, "delivery-digest", "", "exact delivery SHA256")
				command.Flags().StringVar(&receiptDigest, "receipt-digest", "", "raw parent receipt SHA256")
				command.Flags().StringVar(&step, "step", "", "fixed operation step to observe")
				command.MarkFlagRequired("step")
			}
		}
		if action == "render" || action == "check" {
			command.Flags().StringVar(&root, "root", "", "approved source root")
		}
		command.RunE = func(cmd *cobra.Command, _ []string) error {
			if action == "reconcile" {
				all := cmd.Flags().Changed("expected-revision") && cmd.Flags().Changed("delivery-digest") && cmd.Flags().Changed("receipt-digest")
				any := cmd.Flags().Changed("expected-revision") || cmd.Flags().Changed("delivery-digest") || cmd.Flags().Changed("receipt-digest")
				digest := regexp.MustCompile(`^[0-9a-f]{64}$`)
				if verifyReadonly && (step != "verify" || !all || revision < 1 || !digest.MatchString(deliveryDigest) || !digest.MatchString(receiptDigest)) || !verifyReadonly && (any || cmd.Flags().Changed("verify-readonly")) {
					return fmt.Errorf("read-only verify requires --step verify and all revision/digest flags together")
				}
			}
			if action == "apply" && revision < 1 {
				return fmt.Errorf("expected-revision must be positive")
			}
			if action == "reconcile" && step != "publish_snapshot" && step != "apply_secrets" && step != "reconcile_storage" && step != "reconcile_polaris" && step != "reconcile_trino" && step != "verify" {
				return fmt.Errorf("invalid operation step")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "catalog_admin.py"), action}
			for _, flag := range []struct{ name, value string }{{"config", config}, {"operation-id", operationID}, {"idempotency-key", key}, {"step", step}, {"spec", spec}, {"output", output}, {"bundle", bundle}, {"root", root}, {"query-id", queryID}, {"auth-headers", authHeaders}, {"sql-catalog", sqlCatalog}, {"profile-id", profileID}, {"reference-spec", referenceSpec}, {"generation-id", generationID}} {
				if flag.value != "" {
					args = append(args, "--"+flag.name, flag.value)
				}
			}
			if action == "apply" {
				args = append(args, "--expected-revision", strconv.Itoa(revision))
			}
			if verifyReadonly {
				args = append(args, "--verify-readonly", "--expected-revision", strconv.Itoa(revision), "--delivery-digest", deliveryDigest, "--receipt-digest", receiptDigest)
			}
			python, err := workbenchPython(repository)
			if err != nil {
				return err
			}
			data, err := runner.Run(cmd.Context(), python, args...)
			if err != nil {
				return fmt.Errorf("database command failed (details withheld)")
			}
			_, err = fmt.Fprint(cmd.OutOrStdout(), string(data))
			return err
		}
		parent.AddCommand(command)
	}
	return parent
}

func addDatabaseRetirementCommands(parent *cobra.Command, runner commandRunner) {
	for _, action := range []string{"retire-preview", "retire", "retire-status", "retire-reconcile"} {
		action := action
		var identifier, key, baseURL string
		var revision int
		mutation := action == "retire" || action == "retire-reconcile"
		idFlag := "database-id"
		if action == "retire-status" || action == "retire-reconcile" {
			idFlag = "operation-id"
		}
		command := &cobra.Command{Use: action, Short: "Inspect or request non-purge database retirement through authenticated HTTP", Args: cobra.NoArgs}
		command.Flags().StringVar(&identifier, idFlag, "", "Explicit management identifier")
		command.MarkFlagRequired(idFlag)
		command.Flags().StringVar(&baseURL, "base-url", "http://127.0.0.1:8888", "Management HTTP origin")
		if mutation {
			command.Flags().IntVar(&revision, "expected-revision", 0, "Positive inspected database or operation revision")
			command.Flags().StringVar(&key, "idempotency-key", "", "Frozen mutation key")
			command.MarkFlagRequired("expected-revision")
			command.MarkFlagRequired("idempotency-key")
		}
		command.RunE = func(cmd *cobra.Command, _ []string) error {
			validID, _ := regexp.MatchString(`^[A-Za-z0-9_-]{1,128}$`, identifier)
			if !validID || strings.TrimSpace(baseURL) == "" || mutation && (revision < 1 || strings.TrimSpace(key) == "" || len(key) > 256 || strings.IndexFunc(key, func(r rune) bool { return r < 32 || r == 127 }) >= 0) {
				return fmt.Errorf("explicit identifier, positive revision and nonempty key required")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "database_retirement_client.py"), action, "--" + idFlag, identifier, "--base-url", baseURL}
			if mutation {
				args = append(args, "--expected-revision", strconv.Itoa(revision), "--idempotency-key", key)
			}
			data, err := runner.Run(cmd.Context(), "python3", args...)
			if err != nil {
				return fmt.Errorf("retirement HTTP request failed; inspect status using the original identifier and key")
			}
			_, err = fmt.Fprint(cmd.OutOrStdout(), string(data))
			return err
		}
		parent.AddCommand(command)
	}
}

func newDatabaseGrantCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "grant", Short: "Manage desired database grants through authenticated HTTP"}
	for _, action := range []string{"list", "set", "revoke", "operation"} {
		action := action
		var database, principal, role, key, operation, apiURL string
		var revision int
		command := &cobra.Command{Use: action, Args: cobra.NoArgs}
		command.Flags().StringVar(&apiURL, "api-url", "http://127.0.0.1:8888", "Workbench HTTP origin")
		if action == "operation" {
			command.Flags().StringVar(&operation, "operation-id", "", "grant operation identifier")
			command.MarkFlagRequired("operation-id")
		} else {
			command.Flags().StringVar(&database, "database-id", "", "database identifier")
			command.MarkFlagRequired("database-id")
		}
		if action == "set" || action == "revoke" {
			command.Flags().StringVar(&principal, "principal-id", "", "target principal")
			command.Flags().StringVar(&key, "idempotency-key", "", "mutation idempotency key")
			command.Flags().IntVar(&revision, "expected-revision", -1, "desired revision (0 creates)")
			for _, name := range []string{"principal-id", "idempotency-key", "expected-revision"} {
				command.MarkFlagRequired(name)
			}
		}
		if action == "set" {
			command.Flags().StringVar(&role, "maximum-role", "", "bi_viewer, data_engineer or admin")
			command.MarkFlagRequired("maximum-role")
		}
		command.RunE = func(cmd *cobra.Command, _ []string) error {
			if (action == "set" || action == "revoke") && revision < 0 {
				return fmt.Errorf("expected-revision must be nonnegative")
			}
			if action == "set" && role != "bi_viewer" && role != "data_engineer" && role != "admin" {
				return fmt.Errorf("invalid database grant role")
			}
			repository, err := resolveRepositoryRoot("")
			if err != nil {
				return err
			}
			args := []string{filepath.Join(repository, "tools", "local_experiment", "grant_client.py"), action}
			for _, flag := range []struct{ name, value string }{{"database-id", database}, {"principal-id", principal}, {"maximum-role", role}, {"idempotency-key", key}, {"operation-id", operation}, {"api-url", apiURL}} {
				if flag.value != "" {
					args = append(args, "--"+flag.name, flag.value)
				}
			}
			if action == "set" || action == "revoke" {
				args = append(args, "--expected-revision", strconv.Itoa(revision))
			}
			data, err := runner.Run(cmd.Context(), "python3", args...)
			if err != nil {
				return fmt.Errorf("grant HTTP request failed (details withheld)")
			}
			_, err = fmt.Fprint(cmd.OutOrStdout(), string(data))
			return err
		}
		parent.AddCommand(command)
	}
	return parent
}
