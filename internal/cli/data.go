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
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const dataExecutionTimeout = 20 * time.Minute
const dataResponseLimit = 4096
const dataScript = "tools/local_experiment/bears/data_catalog_cli.py"
const dataPython = "tools/local_experiment/llm/.venv/bin/python"

type dataExitError struct {
	code    int
	message string
}

func (err *dataExitError) Error() string { return err.message }

// ExitCode preserves the explicit unsupported outcome without changing other CLI errors.
func ExitCode(err error) int {
	if err == nil {
		return 0
	}
	var coded *dataExitError
	if errors.As(err, &coded) && (coded.code == 2 || coded.code == 130 || coded.code == 143) {
		return coded.code
	}
	return 1
}

type dataChildRequest struct {
	Root    string
	Action  string
	Token   string
	Case    string
	Request []byte
}
type dataChildResult struct {
	Started     bool
	PID         int
	ExitCode    int
	Unconfirmed bool
	Interrupted bool
}
type dataChildRunner interface {
	Run(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error)
}

type dataEvent struct {
	SchemaVersion    int     `json:"schema_version"`
	Event            string  `json:"event"`
	Command          string  `json:"command"`
	HandoffToken     string  `json:"handoff_token"`
	Result           string  `json:"result"`
	Phase            *string `json:"phase"`
	Execution        string  `json:"execution"`
	Verification     string  `json:"verification"`
	DataAttemptToken *string `json:"data_attempt_token"`
	DataPlanToken    *string `json:"data_plan_token"`
	CatalogAttempts  int     `json:"catalog_attempts"`
	Replayed         bool    `json:"replayed"`
	ErrorCode        *string `json:"error_code"`
	Evidence         *string `json:"evidence"`
}

func newDataCommand(child dataChildRunner) *cobra.Command {
	var repositoryRoot, profile string
	parent := &cobra.Command{Use: "data", Short: "Run, resume, or inspect a bounded local Data-to-Catalog handoff", Args: cobra.NoArgs, SilenceErrors: true, SilenceUsage: true}
	parent.PersistentFlags().StringVar(&repositoryRoot, "repo-root", "", "checkout root (auto-detected from the current directory)")
	parent.PersistentFlags().StringVar(&profile, "profile", localExperimentProfile, "data profile: local-experiment")
	for _, action := range []string{"run", "resume", "status"} {
		action := action
		var caseID string
		var requestStdin bool
		use := action + " TOKEN"
		if action == "run" {
			use = "run (--request-stdin | --case NAME)"
		}
		command := &cobra.Command{Use: use, Short: map[string]string{"run": "Start one Data invocation and its Catalog handoff", "resume": "Resume Catalog work or verify saved completion under the same token", "status": "Read the saved handoff journal without model or API calls"}[action],
			Args: func(_ *cobra.Command, args []string) error {
				want := 1
				if action == "run" {
					want = 0
				}
				if len(args) != want {
					return errors.New("data command arguments are invalid")
				}
				return nil
			},
			RunE: func(cmd *cobra.Command, args []string) error {
				if profile != localExperimentProfile {
					return errors.New("data requires --profile local-experiment")
				}
				request := dataChildRequest{Action: action, Case: caseID}
				if action == "run" {
					if (cmd.Flags().Changed("case") && cmd.Flags().Changed("request-stdin")) || requestStdin == (caseID != "") || (caseID != "" && !dataOneOf(caseID, "uppercase_names_ja", "name_lengths_ja", "row_count_ja")) {
						return errors.New("data run requires exactly one --request-stdin or a fixed --case (uppercase_names_ja, name_lengths_ja, row_count_ja)")
					}
					if requestStdin {
						var err error
						request.Request, err = readDataRequest(cmd.InOrStdin())
						if err != nil {
							return err
						}
					}
					var token [16]byte
					if _, err := io.ReadFull(rand.Reader, token[:]); err != nil {
						return errors.New("data identity could not be generated")
					}
					request.Token = hex.EncodeToString(token[:])
				} else {
					request.Token = args[0]
					if !warehouseSessionPattern.MatchString(request.Token) {
						return errors.New("data token must be 32 lowercase hexadecimal characters")
					}
				}
				root, err := resolveDataRoot(repositoryRoot)
				if err != nil {
					return err
				}
				request.Root = root
				ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
				defer stop()
				ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
				defer cancel()
				return runDataCommand(ctx, child, request, cmd.OutOrStdout())
			},
		}
		if action == "run" {
			command.Flags().StringVar(&caseID, "case", "", "fixed fixture request: uppercase_names_ja, name_lengths_ja, row_count_ja")
			command.Flags().BoolVar(&requestStdin, "request-stdin", false, "read one bounded UTF-8 request from stdin; request text is not retained")
		}
		parent.AddCommand(command)
	}
	return parent
}

func readDataRequest(input io.Reader) ([]byte, error) {
	invalid := errors.New("data request must be nonblank UTF-8 plain text, at most 512 characters and 4096 bytes")
	raw, err := io.ReadAll(io.LimitReader(input, 4097))
	if err != nil || len(raw) > 4096 || !utf8.Valid(raw) {
		return nil, invalid
	}
	text := strings.TrimSuffix(strings.TrimSuffix(string(raw), "\n"), "\r")
	if utf8.RuneCountInString(text) < 1 || utf8.RuneCountInString(text) > 512 || strings.TrimSpace(text) == "" {
		return nil, invalid
	}
	for _, r := range text {
		if r < 32 || (r >= 127 && r <= 159) {
			return nil, invalid
		}
	}
	return []byte(text), nil
}

func resolveDataRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("data checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("data checkout could not be resolved")
	}
	// The venv interpreter itself is normally a symlink to its managed Python.
	// Repository scripts and the environment directory cannot redirect elsewhere.
	for _, relative := range []string{dataScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("data checkout or existing dedicated environment is not ready; no installation was attempted")
		}
	}
	python := filepath.Join(root, dataPython)
	info, err := os.Stat(python)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("existing dedicated data Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func runDataCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	allocated := false
	var final *dataEvent
	var finalLine []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeDataEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("data child event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
		} else {
			if !allocated && event.Execution != "not-started" {
				return errors.New("data child allocation missing")
			}
			final = &event
			finalLine = append([]byte(nil), line...)
			// A syntactically passed event is not a completed child process yet.
			// Release it only after the matching process exit is observed.
			return nil
		}
		// Decode before copying a bounded, exact-schema line. No raw child output.
		_, e = output.Write(append(append([]byte(nil), line...), '\n'))
		return e
	})
	if !result.Started {
		return errors.New("data child did not start; no workflow invocation was confirmed")
	}
	unknown := fmt.Sprintf("data outcome unknown; handoff_token=%s child_pid=%d; use data status %s before any further action (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
	if (result.ExitCode == 130 || result.ExitCode == 143) && result.Interrupted && errors.Is(err, errDataInterrupted) {
		return &dataExitError{code: result.ExitCode, message: unknown}
	}
	if err != nil || result.Unconfirmed || final == nil {
		return errors.New(unknown)
	}
	if (result.ExitCode == 130 || result.ExitCode == 143) && final.Result == "unknown" && final.ErrorCode != nil && *final.ErrorCode == "INTERRUPTED" {
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
	} else if final.Result == "unsupported" {
		want = 2
	}
	if result.ExitCode != want {
		return fmt.Errorf("data exit and receipt disagree; outcome unknown; handoff_token=%s", request.Token)
	}
	if _, err := output.Write(append(finalLine, '\n')); err != nil {
		return fmt.Errorf("data result delivery unconfirmed; handoff_token=%s", request.Token)
	}
	if want == 0 {
		return nil
	}
	return &dataExitError{code: want, message: fmt.Sprintf("data result=%s; handoff_token=%s", final.Result, request.Token)}
}

func dataOneOf(value string, choices ...string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

func decodeDataEvent(line []byte, request dataChildRequest) (dataEvent, error) {
	var value dataEvent
	invalid := errors.New("data child response failed the fixed protocol")
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
	keys := []string{"schema_version", "event", "command", "handoff_token"}
	if kind == "result" {
		keys = append(keys, "result", "phase", "execution", "verification", "data_attempt_token", "data_plan_token", "catalog_attempts", "replayed", "error_code", "evidence")
	} else if kind != "allocated" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) {
		return value, invalid
	}
	for _, key := range []string{"schema_version", "event", "command", "handoff_token", "result", "execution", "verification", "catalog_attempts", "replayed"} {
		if raw, ok := fields[key]; ok && bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return value, invalid
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&value) != nil || value.SchemaVersion != 1 || value.Command != request.Action || value.HandoffToken != request.Token {
		return value, invalid
	}
	if kind == "allocated" {
		return value, nil
	}
	if !dataOneOf(value.Result, "passed", "unsupported", "failed", "unknown", "not_started", "not_found", "busy") ||
		!dataOneOf(value.Execution, "not-started", "invoked", "read-only") ||
		!dataOneOf(value.Verification, "not-checked", "journal-only", "saved-evidence") || value.CatalogAttempts < 0 || value.CatalogAttempts > 8 {
		return value, invalid
	}
	if value.Phase != nil && !dataOneOf(*value.Phase, "data_pending", "data_verified", "catalog_pending", "catalog_failed", "completed", "unsupported", "failed", "unknown") {
		return value, invalid
	}
	for _, token := range []*string{value.DataAttemptToken, value.DataPlanToken} {
		if token != nil && !warehouseSessionPattern.MatchString(*token) {
			return value, invalid
		}
	}
	if value.Evidence != nil && *value.Evidence != "tmp/local-experiment/data-catalog/"+request.Token {
		return value, invalid
	}
	if value.ErrorCode != nil && !dataOneOf(*value.ErrorCode, "ARGUMENTS_INVALID", "INPUT_INVALID", "HANDOFF_TOKEN_INVALID", "ENVIRONMENT_NOT_READY", "HANDOFF_NOT_FOUND", "HANDOFF_BUSY", "HANDOFF_JOURNAL_INVALID", "HANDOFF_EVIDENCE_INVALID", "HANDOFF_CONFIG_CHANGED", "CATALOG_ATTEMPT_LIMIT", "HANDOFF_FAILED", "INTERRUPTED") {
		return value, invalid
	}
	if request.Action == "status" {
		if value.Execution != "read-only" {
			return value, invalid
		}
	} else if value.Execution == "read-only" {
		return value, invalid
	}
	if value.Result == "passed" || value.Result == "unsupported" {
		phase := "completed"
		if value.Result == "unsupported" {
			phase = "unsupported"
		}
		if value.Phase == nil || *value.Phase != phase || value.ErrorCode != nil || value.Evidence == nil || value.DataAttemptToken == nil {
			return value, invalid
		}
		if value.Verification != "saved-evidence" || (request.Action != "status" && value.Execution != "invoked") {
			return value, invalid
		}
		if value.Result == "passed" && (value.DataPlanToken == nil || value.CatalogAttempts < 1) {
			return value, invalid
		}
		if value.Result == "unsupported" && (value.DataPlanToken != nil || value.CatalogAttempts != 0) {
			return value, invalid
		}
	}
	return value, nil
}
