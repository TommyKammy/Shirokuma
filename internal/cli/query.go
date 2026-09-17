package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const queryScript = "tools/local_experiment/bears/query_cli.py"

type queryEvent struct {
	SchemaVersion   int     `json:"schema_version"`
	Event           string  `json:"event"`
	Command         string  `json:"command"`
	EvidenceToken   string  `json:"evidence_token"`
	QueryRunToken   *string `json:"query_run_token"`
	Result          string  `json:"result"`
	Execution       string  `json:"execution"`
	Verification    string  `json:"verification"`
	CleanupVerified *bool   `json:"cleanup_verified"`
	RootCause       string  `json:"root_cause"`
	PerformanceGain string  `json:"performance_gain"`
	ErrorCode       *string `json:"error_code"`
	Evidence        *string `json:"evidence"`
}

func newQueryCommand(child dataChildRunner) *cobra.Command {
	var repositoryRoot, profile string
	parent := &cobra.Command{Use: "query", Short: "Run or inspect bounded local Query Doctor observations", Args: cobra.NoArgs, SilenceErrors: true, SilenceUsage: true}
	parent.RunE = func(cmd *cobra.Command, _ []string) error { return cmd.Help() }
	parent.PersistentFlags().StringVar(&repositoryRoot, "repo-root", "", "checkout root (auto-detected from the current directory)")
	parent.PersistentFlags().StringVar(&profile, "profile", localExperimentProfile, "query profile: local-experiment")
	for _, action := range []string{"run", "status"} {
		action := action
		var caseID string
		use := "status TOKEN"
		if action == "run" {
			use = "run --case retained-fixture"
		}
		command := &cobra.Command{Use: use, Short: map[string]string{"run": "Observe the two fixed fixture queries with the local model", "status": "Inspect saved evidence without model or API calls"}[action],
			Args: func(_ *cobra.Command, args []string) error {
				want := 1
				if action == "run" {
					want = 0
				}
				if len(args) != want {
					return errors.New("query command arguments are invalid")
				}
				return nil
			},
			RunE: func(cmd *cobra.Command, args []string) error {
				if profile != localExperimentProfile {
					return errors.New("query requires --profile local-experiment")
				}
				request := dataChildRequest{Action: action, Case: caseID}
				if action == "run" {
					if caseID != "retained-fixture" {
						return errors.New("query run requires --case retained-fixture")
					}
					var token [16]byte
					if _, err := io.ReadFull(rand.Reader, token[:]); err != nil {
						return errors.New("query evidence identity could not be generated")
					}
					request.Token = hex.EncodeToString(token[:])
				} else {
					request.Token = args[0]
					if !warehouseSessionPattern.MatchString(request.Token) {
						return errors.New("query token must be 32 lowercase hexadecimal characters")
					}
				}
				root, err := resolveQueryRoot(repositoryRoot)
				if err != nil {
					return err
				}
				request.Root = root
				ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
				defer stop()
				ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
				defer cancel()
				return runQueryCommand(ctx, child, request, cmd.OutOrStdout())
			},
		}
		if action == "run" {
			command.Flags().StringVar(&caseID, "case", "", "fixed diagnostic case: retained-fixture")
		}
		parent.AddCommand(command)
	}
	for _, action := range []string{"tables", "describe", "select"} {
		parent.AddCommand(newQueryReadCommand(action, execQueryReadChild{}, &repositoryRoot, &profile))
	}
	parent.AddCommand(newQueryDiagnoseCommand(execQueryDiagnoseChild{}, &repositoryRoot, &profile))
	parent.AddCommand(newQuerySnapshotsCommand(execQuerySnapshotsChild{}, &repositoryRoot, &profile))
	parent.AddCommand(newQueryCountCommand(execQueryCountChild{}, &repositoryRoot, &profile))
	return parent
}

func resolveQueryRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("query checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("query checkout could not be resolved")
	}
	for _, relative := range []string{queryScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("query checkout or existing dedicated environment is not ready; no installation was attempted")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("existing dedicated query Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func runQueryCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	allocated := false
	var final *queryEvent
	var finalLine []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeQueryEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("query child event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			_, e = output.Write(append(append([]byte(nil), line...), '\n'))
			return e
		}
		if !allocated && event.Execution != "not-started" {
			return errors.New("query child allocation missing")
		}
		final = &event
		finalLine = append([]byte(nil), line...)
		return nil // A passed receipt is withheld until its matching child exit.
	})
	if !result.Started {
		return errors.New("query child did not start; no workflow invocation was confirmed")
	}
	unknown := fmt.Sprintf("query outcome unknown; evidence_token=%s child_pid=%d; use query status %s before any further action (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
	if (result.ExitCode == 130 || result.ExitCode == 143) && result.Interrupted && errors.Is(err, errDataInterrupted) {
		return &dataExitError{code: result.ExitCode, message: unknown}
	}
	if err != nil || result.Unconfirmed || final == nil {
		return errors.New(unknown)
	}
	if (result.ExitCode == 130 || result.ExitCode == 143) &&
		(final.Result == "unknown" || (final.Result == "not_started" && final.Execution == "not-started")) &&
		final.ErrorCode != nil && *final.ErrorCode == "INTERRUPTED" {
		if _, err := output.Write(append(finalLine, '\n')); err != nil {
			return errors.New(unknown)
		}
		return &dataExitError{code: result.ExitCode, message: unknown}
	}
	want := 1
	if request.Action == "status" {
		if final.Verification == "journal-only" || final.Verification == "saved-evidence" {
			want = 0
		}
	} else if final.Result == "passed" {
		want = 0
	}
	if result.ExitCode != want {
		return fmt.Errorf("query exit and receipt disagree; outcome unknown; evidence_token=%s", request.Token)
	}
	if _, err := output.Write(append(finalLine, '\n')); err != nil {
		return fmt.Errorf("query result delivery unconfirmed; evidence_token=%s", request.Token)
	}
	if want == 0 {
		return nil
	}
	return &dataExitError{code: want, message: fmt.Sprintf("query result=%s; evidence_token=%s", final.Result, request.Token)}
}

func decodeQueryEvent(line []byte, request dataChildRequest) (queryEvent, error) {
	var value queryEvent
	invalid := errors.New("query child response failed the fixed protocol")
	if len(line) > dataResponseLimit || !utf8.Valid(line) || uniqueWarehouseJSON(line) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(line, &fields) != nil {
		return value, invalid
	}
	var kind string
	if json.Unmarshal(fields["event"], &kind) != nil {
		return value, invalid
	}
	keys := []string{"schema_version", "event", "command", "evidence_token"}
	if kind == "result" {
		keys = append(keys, "query_run_token", "result", "execution", "verification", "cleanup_verified", "root_cause", "performance_gain", "error_code", "evidence")
	} else if kind != "allocated" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) {
		return value, invalid
	}
	for _, key := range []string{"schema_version", "event", "command", "evidence_token", "result", "execution", "verification", "root_cause", "performance_gain"} {
		if raw, ok := fields[key]; ok && bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return value, invalid
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&value) != nil || value.SchemaVersion != 1 || value.Command != request.Action || value.EvidenceToken != request.Token {
		return value, invalid
	}
	if kind == "allocated" {
		return value, nil
	}
	if !dataOneOf(value.Result, "passed", "failed", "unknown", "not_started", "not_found", "busy") ||
		!dataOneOf(value.Execution, "not-started", "invoked", "read-only") ||
		!dataOneOf(value.Verification, "not-checked", "journal-only", "saved-evidence") ||
		value.RootCause != "undetermined" || value.PerformanceGain != "unmeasured" {
		return value, invalid
	}
	if value.QueryRunToken != nil && !warehouseSessionPattern.MatchString(*value.QueryRunToken) {
		return value, invalid
	}
	if value.Evidence != nil && *value.Evidence != "tmp/local-experiment/query-bear/"+request.Token {
		return value, invalid
	}
	if value.ErrorCode != nil && !queryErrorCode(*value.ErrorCode) {
		return value, invalid
	}
	if (request.Action == "status") != (value.Execution == "read-only") {
		return value, invalid
	}
	if value.Result == "not_started" && (request.Action != "run" || value.Execution != "not-started" ||
		value.Verification != "not-checked" || value.QueryRunToken != nil || value.CleanupVerified != nil || value.Evidence != nil) {
		return value, invalid
	}
	if value.Result == "passed" && (value.QueryRunToken == nil || value.Evidence == nil || value.ErrorCode != nil ||
		value.Verification != "saved-evidence" || value.CleanupVerified == nil || !*value.CleanupVerified ||
		(request.Action == "run" && value.Execution != "invoked")) {
		return value, invalid
	}
	return value, nil
}

func queryErrorCode(value string) bool {
	return dataOneOf(value, "ARGUMENTS_INVALID", "EVIDENCE_TOKEN_INVALID", "ENVIRONMENT_NOT_READY", "QUERY_NOT_FOUND", "QUERY_BUSY", "QUERY_ALREADY_EXISTS", "QUERY_JOURNAL_INVALID", "QUERY_EVIDENCE_INVALID", "QUERY_FAILED", "INTERRUPTED")
}
