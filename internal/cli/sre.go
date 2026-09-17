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

const sreScript = "tools/local_experiment/bears/sre_cli.py"

type sreEvent struct {
	SchemaVersion       int     `json:"schema_version"`
	Event               string  `json:"event"`
	Command             string  `json:"command"`
	EvidenceToken       string  `json:"evidence_token"`
	WorkflowToken       *string `json:"workflow_token"`
	Result              string  `json:"result"`
	Execution           string  `json:"execution"`
	Verification        string  `json:"verification"`
	Assessment          *string `json:"assessment"`
	RootCause           string  `json:"root_cause"`
	RemediationExecuted bool    `json:"remediation_executed"`
	ErrorCode           *string `json:"error_code"`
	Evidence            *string `json:"evidence"`
}

func newSRECommand(child dataChildRunner) *cobra.Command {
	var repositoryRoot, profile string
	parent := &cobra.Command{Use: "sre", Short: "Run or inspect bounded local SRE observations", Args: cobra.NoArgs, SilenceErrors: true, SilenceUsage: true}
	parent.RunE = func(cmd *cobra.Command, _ []string) error { return cmd.Help() }
	parent.PersistentFlags().StringVar(&repositoryRoot, "repo-root", "", "checkout root (auto-detected from the current directory)")
	parent.PersistentFlags().StringVar(&profile, "profile", localExperimentProfile, "SRE profile: local-experiment")
	for _, action := range []string{"run", "status"} {
		action := action
		use := "status TOKEN"
		if action == "run" {
			use = "run"
		}
		command := &cobra.Command{Use: use, Short: map[string]string{"run": "Observe the fixed local sources with the local model", "status": "Inspect saved evidence without model or API calls"}[action],
			Args: func(_ *cobra.Command, args []string) error {
				want := 1
				if action == "run" {
					want = 0
				}
				if len(args) != want {
					return errors.New("SRE command arguments are invalid")
				}
				return nil
			},
			RunE: func(cmd *cobra.Command, args []string) error {
				if profile != localExperimentProfile {
					return errors.New("SRE requires --profile local-experiment")
				}
				request := dataChildRequest{Action: action}
				if action == "run" {
					var token [16]byte
					if _, err := io.ReadFull(rand.Reader, token[:]); err != nil {
						return errors.New("SRE evidence identity could not be generated")
					}
					request.Token = hex.EncodeToString(token[:])
				} else {
					request.Token = args[0]
					if !warehouseSessionPattern.MatchString(request.Token) {
						return errors.New("SRE token must be 32 lowercase hexadecimal characters")
					}
				}
				root, err := resolveSRERoot(repositoryRoot)
				if err != nil {
					return err
				}
				request.Root = root
				ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
				defer stop()
				ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
				defer cancel()
				return runSRECommand(ctx, child, request, cmd.OutOrStdout())
			},
		}
		parent.AddCommand(command)
	}
	return parent
}

func resolveSRERoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("SRE checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("SRE checkout could not be resolved")
	}
	for _, relative := range []string{sreScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("SRE checkout or existing dedicated environment is not ready; no installation was attempted")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("existing dedicated SRE Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func runSRECommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	allocated := false
	var final *sreEvent
	var finalLine []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeSREEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("SRE child event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			_, e = output.Write(append(append([]byte(nil), line...), '\n'))
			return e
		}
		if !allocated && event.Execution != "not-started" {
			return errors.New("SRE child allocation missing")
		}
		final = &event
		finalLine = append([]byte(nil), line...)
		return nil // A passed receipt is withheld until its matching child exit.
	})
	if !result.Started {
		return errors.New("SRE child did not start; no workflow invocation was confirmed")
	}
	unknown := fmt.Sprintf("SRE outcome unknown; evidence_token=%s child_pid=%d; use sre status %s before any further action (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
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
		return fmt.Errorf("SRE exit and receipt disagree; outcome unknown; evidence_token=%s", request.Token)
	}
	if _, err := output.Write(append(finalLine, '\n')); err != nil {
		return fmt.Errorf("SRE result delivery unconfirmed; evidence_token=%s", request.Token)
	}
	if want == 0 {
		return nil
	}
	return &dataExitError{code: want, message: fmt.Sprintf("SRE result=%s; evidence_token=%s", final.Result, request.Token)}
}

func decodeSREEvent(line []byte, request dataChildRequest) (sreEvent, error) {
	var value sreEvent
	invalid := errors.New("SRE child response failed the fixed protocol")
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
		keys = append(keys, "workflow_token", "result", "execution", "verification", "assessment", "root_cause", "remediation_executed", "error_code", "evidence")
	} else if kind != "allocated" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) {
		return value, invalid
	}
	for _, key := range []string{"schema_version", "event", "command", "evidence_token", "result", "execution", "verification", "root_cause", "remediation_executed"} {
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
		value.RootCause != "undetermined" || value.RemediationExecuted {
		return value, invalid
	}
	if value.Assessment != nil && !dataOneOf(*value.Assessment, "attention-required", "healthy-observed-scope", "unknown") {
		return value, invalid
	}
	if value.WorkflowToken != nil && !warehouseSessionPattern.MatchString(*value.WorkflowToken) {
		return value, invalid
	}
	if value.Evidence != nil && *value.Evidence != "tmp/local-experiment/sre-llm/"+request.Token {
		return value, invalid
	}
	if value.ErrorCode != nil && !sreErrorCode(*value.ErrorCode) {
		return value, invalid
	}
	if (request.Action == "status") != (value.Execution == "read-only") {
		return value, invalid
	}
	if value.Result == "not_started" && (request.Action != "run" || value.Execution != "not-started" ||
		value.Verification != "not-checked" || value.WorkflowToken != nil || value.Assessment != nil || value.Evidence != nil) {
		return value, invalid
	}
	if value.Result == "passed" && (value.WorkflowToken == nil || value.Evidence == nil || value.Assessment == nil || value.ErrorCode != nil ||
		value.Verification != "saved-evidence" || (request.Action == "run" && value.Execution != "invoked")) {
		return value, invalid
	}
	return value, nil
}

func sreErrorCode(value string) bool {
	return dataOneOf(value, "ARGUMENTS_INVALID", "EVIDENCE_TOKEN_INVALID", "ENVIRONMENT_NOT_READY", "SRE_NOT_FOUND", "SRE_BUSY", "SRE_ALREADY_EXISTS", "SRE_JOURNAL_INVALID", "SRE_EVIDENCE_INVALID", "SRE_FAILED", "INTERRUPTED")
}
