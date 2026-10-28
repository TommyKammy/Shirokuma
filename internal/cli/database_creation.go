package cli

import (
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/spf13/cobra"
)

// Creation has its own envelope because the shared runner discards failed output.
func newDatabaseCreationCommand(runner commandRunner) *cobra.Command {
	parent := &cobra.Command{Use: "creation", Short: "Create databases and inspect creation progress"}
	for _, action := range []string{"create", "principals", "list", "show", "plan", "approve", "replan", "reconcile", "withdraw", "resume", "retry"} {
		action := action
		var name, base, phase, display, description, retryAction string
		var grants []string
		var machine, recovery bool
		cmd := &cobra.Command{Use: action, Args: cobra.NoArgs}
		cmd.Flags().BoolVar(&machine, "json", false, "Print the public JSON result")
		cmd.Flags().StringVar(&base, "base-url", "", "Local management origin (defaults to active profile)")
		if action == "show" {
			cmd.Aliases = []string{"progress"}
		}
		if action == "create" {
			cmd.Flags().StringVar(&name, "sql-catalog", "", "SQL database name")
			cmd.MarkFlagRequired("sql-catalog")
			cmd.Flags().StringArrayVar(&grants, "grant", nil, "Existing principal:role (repeatable)")
			cmd.Flags().StringVar(&display, "display-name", "", "Display name")
			cmd.Flags().StringVar(&description, "description", "", "Description")
		} else if action != "list" && action != "principals" {
			cmd.Flags().StringVar(&name, "database", "", "Exact SQL database name")
			cmd.MarkFlagRequired("database")
		}
		if action == "plan" || action == "approve" || action == "replan" {
			cmd.Flags().StringVar(&phase, "phase", "bootstrap", "bootstrap or full")
			if action != "replan" {
				cmd.Flags().BoolVar(&recovery, "recovery", false, "Review or approve limited full verification recovery")
			}
		}
		if action == "retry" {
			cmd.Short = "Explicitly replay a frozen request using its original key and body"
			cmd.Flags().StringVar(&retryAction, "action", "", "Stored create, plan, approve, replan, reconcile, withdraw or resume intent")
			cmd.MarkFlagRequired("action")
			cmd.Flags().StringVar(&phase, "phase", "", "Required for plan, approve or replan: bootstrap or full")
			cmd.Flags().BoolVar(&recovery, "recovery", false, "Select the frozen full verification recovery approval")
		}
		cmd.RunE = func(cmd *cobra.Command, _ []string) error {
			if action == "retry" {
				phased := retryAction == "plan" || retryAction == "approve" || retryAction == "replan"
				if !phased && retryAction != "create" && retryAction != "reconcile" && retryAction != "withdraw" && retryAction != "resume" || phased && phase == "" || !phased && phase != "" || recovery && (retryAction != "approve" || phase != "full") {
					return fmt.Errorf("select one stored action and its explicit phase; recovery selects only full approval")
				}
			}
			if action != "list" && action != "principals" && name == "" {
				return fmt.Errorf("SQL database name required")
			}
			if base != "" {
				u, e := url.Parse(base)
				if e != nil {
					return fmt.Errorf("invalid local management origin")
				}
				ip := net.ParseIP(u.Hostname())
				if u.Scheme != "http" || (u.Hostname() != "localhost" && (ip == nil || !ip.IsLoopback())) || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
					return fmt.Errorf("invalid local management origin")
				}
			}
			if name != "" && (!regexp.MustCompile(`^[a-z][a-z0-9_]{0,30}$`).MatchString(name) || name == "iceberg" || name == "system" || name == "information_schema" || name == "tpch" || name == "tpcds") {
				return fmt.Errorf("invalid SQL database name")
			}
			if phase != "" && phase != "bootstrap" && phase != "full" || recovery && phase != "full" {
				return fmt.Errorf("invalid creation phase")
			}
			for _, grant := range grants {
				parts := strings.Split(grant, ":")
				if len(parts) != 2 || strings.TrimSpace(parts[0]) == "" || (parts[1] != "admin" && parts[1] != "data_engineer" && parts[1] != "bi_viewer") {
					return fmt.Errorf("grant must be principal:role")
				}
			}
			root, err := resolveRepositoryRoot("")
			if err != nil {
				return fmt.Errorf("creation client unavailable")
			}
			args := []string{filepath.Join(root, "tools", "local_experiment", "database_creation_client.py"), action}
			flag := "database"
			if action == "create" {
				flag = "sql-catalog"
			}
			for _, item := range []struct{ k, v string }{{flag, name}, {"base-url", base}, {"phase", phase}, {"display-name", display}, {"description", description}, {"action", retryAction}} {
				if item.v != "" {
					args = append(args, "--"+item.k, item.v)
				}
			}
			for _, grant := range grants {
				args = append(args, "--grant", grant)
			}
			if recovery {
				args = append(args, "--recovery")
			}
			data, err := runner.Run(cmd.Context(), "python3", args...)
			if err != nil {
				return fmt.Errorf("creation client failed; inspect progress before repeating")
			}
			var result struct {
				OK      *bool           `json:"ok"`
				Error   string          `json:"error"`
				Message string          `json:"message"`
				Data    json.RawMessage `json:"data"`
			}
			if json.Unmarshal(data, &result) != nil || result.OK == nil {
				return fmt.Errorf("invalid creation client response")
			}
			if machine {
				_, err = fmt.Fprintln(cmd.OutOrStdout(), string(data))
			} else if !*result.OK {
				_, err = fmt.Fprintln(cmd.OutOrStdout(), "Creation request: "+safeCreationCode(result.Error))
			} else {
				_, err = fmt.Fprintln(cmd.OutOrStdout(), result.Message)
			}
			if err != nil {
				return err
			}
			if !*result.OK {
				return fmt.Errorf("creation request failed: %s", safeCreationCode(result.Error))
			}
			return nil
		}
		parent.AddCommand(cmd)
	}
	return parent
}

func safeCreationCode(code string) string {
	if !regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,79}$`).MatchString(code) {
		return "REQUEST_FAILED"
	}
	return code
}
