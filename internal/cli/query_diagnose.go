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
	"regexp"
	"sort"
	"syscall"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

type queryDiagnoseRequest struct {
	ReadToken string `json:"read_token"`
}

func newQueryDiagnoseCommand(child dataChildRunner, root, profile *string) *cobra.Command {
	return newQueryDiagnoseCommandWithReport(child, execQueryDiagnoseReportChild{}, root, profile)
}

func newQueryDiagnoseCommandWithReport(child, reportChild dataChildRunner, root, profile *string) *cobra.Command {
	var readToken string
	command := &cobra.Command{Use: "diagnose --read-token TOKEN", Short: "Diagnose one saved bounded SELECT without replaying it", Args: func(_ *cobra.Command, args []string) error {
		if len(args) != 0 {
			return errors.New("query diagnose does not accept positional arguments")
		}
		return nil
	}, RunE: func(cmd *cobra.Command, _ []string) error {
		if *profile != localExperimentProfile {
			return errors.New("query diagnose requires --profile local-experiment")
		}
		if !warehouseSessionPattern.MatchString(readToken) {
			return errors.New("query diagnose read token must be 32 lowercase hexadecimal characters")
		}
		root, err := resolveQueryDiagnoseRoot(*root)
		if err != nil {
			return err
		}
		var token [16]byte
		if _, err := io.ReadFull(rand.Reader, token[:]); err != nil {
			return errors.New("query diagnosis evidence identity unavailable")
		}
		evidenceToken := hex.EncodeToString(token[:])
		if evidenceToken == readToken {
			return errors.New("query diagnosis requires a fresh evidence identity")
		}
		raw, err := json.Marshal(queryDiagnoseRequest{ReadToken: readToken})
		if err != nil {
			return errors.New("query diagnosis input is invalid")
		}
		request := dataChildRequest{Root: root, Action: "diagnose", Token: evidenceToken, Request: raw}
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
		defer cancel()
		return runQueryDiagnoseCommand(ctx, child, request, cmd.OutOrStdout())
	}}
	command.Flags().StringVar(&readToken, "read-token", "", "successful query select evidence token")
	command.SetFlagErrorFunc(func(_ *cobra.Command, _ error) error { return errors.New("query diagnose flags are invalid") })
	command.AddCommand(newQueryDiagnoseReportCommand(reportChild, root, profile))
	return command
}

func decodeQueryDiagnoseRequest(request dataChildRequest) (queryDiagnoseRequest, error) {
	var value queryDiagnoseRequest
	invalid := errors.New("invalid fixed query diagnosis invocation")
	if request.Action != "diagnose" || request.Case != "" || !warehouseSessionPattern.MatchString(request.Token) || len(request.Request) == 0 || len(request.Request) > 1024 || !utf8.Valid(request.Request) || uniqueWarehouseJSON(request.Request) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(request.Request, &fields) != nil || !warehouseKeys(fields, "read_token") || json.Unmarshal(request.Request, &value) != nil || !warehouseSessionPattern.MatchString(value.ReadToken) || value.ReadToken == request.Token {
		return value, invalid
	}
	return value, nil
}

type queryDiagnoseEvent struct {
	SchemaVersion   int             `json:"schema_version"`
	Event           string          `json:"event"`
	Command         string          `json:"command"`
	EvidenceToken   string          `json:"evidence_token"`
	Result          string          `json:"result"`
	Observation     json.RawMessage `json:"observation"`
	ErrorCode       *string         `json:"error_code"`
	Evidence        string          `json:"evidence"`
	CleanupVerified bool            `json:"cleanup_verified"`
}

type queryDiagnoseSummary struct {
	SchemaVersion            int      `json:"schema_version"`
	ReadToken                string   `json:"read_token"`
	QueryID                  string   `json:"query_id"`
	History                  string   `json:"history"`
	Findings                 []string `json:"findings"`
	NextSteps                []string `json:"next_steps"`
	RootCause                string   `json:"root_cause"`
	PerformanceGain          string   `json:"performance_gain"`
	OriginalSelectReexecuted bool     `json:"original_select_reexecuted"`
	ModelCalls               int      `json:"model_calls"`
}

func runQueryDiagnoseCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if _, err := decodeQueryDiagnoseRequest(request); err != nil {
		return err
	}
	allocated := false
	var final *queryDiagnoseEvent
	var finalLine []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeQueryDiagnoseEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("query diagnosis event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			return writeQueryReadLine(output, line)
		}
		if event.Result == "passed" && !allocated {
			return errors.New("query diagnosis allocation missing")
		}
		final = &event
		finalLine = append([]byte(nil), line...)
		return nil
	})
	if !result.Started {
		return errors.New("query diagnosis child did not start; no diagnosis was confirmed")
	}
	unknown := fmt.Sprintf("query diagnosis outcome unknown; evidence_token=%s child_pid=%d; inspect tmp/local-experiment/query-diagnose/%s/summary.json before another diagnosis (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
	if result.Interrupted && errors.Is(err, errDataInterrupted) && (result.ExitCode == 130 || result.ExitCode == 143) {
		return &dataExitError{code: result.ExitCode, message: unknown}
	}
	if err != nil || result.Unconfirmed || final == nil {
		return errors.New(unknown)
	}
	want := 1
	if final.Result == "passed" {
		want = 0
	}
	if final.Result == "interrupted" {
		if result.ExitCode != 130 && result.ExitCode != 143 {
			return errors.New(unknown)
		}
		want = result.ExitCode
	}
	if result.ExitCode != want {
		return errors.New(unknown)
	}
	if err := writeQueryReadLine(output, finalLine); err != nil {
		return errors.New(unknown)
	}
	if want == 0 {
		return nil
	}
	return &dataExitError{code: want, message: fmt.Sprintf("query diagnosis result=%s; evidence_token=%s", final.Result, request.Token)}
}

func decodeQueryDiagnoseEvent(line []byte, request dataChildRequest) (queryDiagnoseEvent, error) {
	var value queryDiagnoseEvent
	invalid := errors.New("query diagnosis child response failed the fixed protocol")
	if len(line) > dataResponseLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
		return value, invalid
	}
	input, err := decodeQueryDiagnoseRequest(request)
	if err != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(line, &fields) != nil || json.Unmarshal(line, &value) != nil {
		return value, invalid
	}
	keys := []string{"schema_version", "event", "command", "evidence_token"}
	if value.Event == "result" {
		keys = append(keys, "result", "observation", "error_code", "cleanup_verified", "evidence")
	} else if value.Event != "allocated" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) || value.SchemaVersion != 1 || value.Command != "query diagnose" || value.EvidenceToken != request.Token {
		return value, invalid
	}
	for key, raw := range fields {
		if key != "observation" && key != "error_code" && bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return value, invalid
		}
	}
	if value.Event == "allocated" {
		return value, nil
	}
	if value.Evidence != "tmp/local-experiment/query-diagnose/"+request.Token+"/summary.json" || !dataOneOf(value.Result, "passed", "failed", "interrupted") {
		return value, invalid
	}
	if value.Result == "passed" {
		if !value.CleanupVerified || value.ErrorCode != nil || !validQueryDiagnoseSummary(value.Observation, input.ReadToken) {
			return value, invalid
		}
	} else {
		if !bytes.Equal(bytes.TrimSpace(value.Observation), []byte("null")) || value.ErrorCode == nil || !queryDiagnoseErrorCode(*value.ErrorCode) {
			return value, invalid
		}
		if value.Result == "interrupted" && *value.ErrorCode != "INTERRUPTED" {
			return value, invalid
		}
	}
	return value, nil
}

func validQueryDiagnoseSummary(raw []byte, readToken string) bool {
	var fields map[string]json.RawMessage
	var value queryDiagnoseSummary
	if json.Unmarshal(raw, &fields) != nil || !warehouseKeys(fields, "schema_version", "read_token", "query_id", "history", "findings", "next_steps", "root_cause", "performance_gain", "original_select_reexecuted", "model_calls") || json.Unmarshal(raw, &value) != nil {
		return false
	}
	for _, field := range fields {
		if bytes.Equal(bytes.TrimSpace(field), []byte("null")) {
			return false
		}
	}
	if value.SchemaVersion != 1 || value.ReadToken != readToken || !queryDiagnoseQueryID(value.QueryID) || !dataOneOf(value.History, "observed", "not-retained") || value.RootCause != "undetermined" || value.PerformanceGain != "unmeasured" || value.OriginalSelectReexecuted || value.ModelCalls != 1 {
		return false
	}
	if !validQueryDiagnoseIDs(value.Findings, false) || !validQueryDiagnoseIDs(value.NextSteps, true) {
		return false
	}
	findingSet := map[string]bool{}
	expectedSteps := map[string]bool{"MEASURE_BEFORE_OPTIMIZING": true}
	for _, finding := range value.Findings {
		findingSet[finding] = true
		expectedSteps[queryDiagnoseFindingSteps[finding]] = true
	}
	if (value.History == "not-retained") != findingSet["HISTORY_NOT_RETAINED"] || (value.History == "not-retained" && (findingSet["HISTORY_TIMING_INCOMPLETE"] || findingSet["QUEUE_TIME_OBSERVED"])) {
		return false
	}
	if len(expectedSteps) != len(value.NextSteps) {
		return false
	}
	for _, step := range value.NextSteps {
		if !expectedSteps[step] {
			return false
		}
	}
	return true
}

var queryDiagnoseIDPattern = regexp.MustCompile(`^[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]{5}$`)
var queryDiagnoseFindingSteps = map[string]string{
	"RESULT_LIMIT_REACHED":        "REVIEW_READER_LIMIT",
	"HISTORY_NOT_RETAINED":        "CAPTURE_HISTORY_PROMPTLY",
	"HISTORY_TIMING_INCOMPLETE":   "CHECK_HISTORY_TIMING",
	"QUEUE_TIME_OBSERVED":         "REVIEW_QUEUE_TIMING",
	"PLAN_ESTIMATES_INCOMPLETE":   "REVIEW_TABLE_STATISTICS",
	"TABLE_STATISTICS_INCOMPLETE": "REVIEW_TABLE_STATISTICS",
}

func queryDiagnoseQueryID(value string) bool {
	return len(value) <= 128 && queryDiagnoseIDPattern.MatchString(value)
}
func validQueryDiagnoseIDs(values []string, steps bool) bool {
	maximum := 5
	if steps {
		maximum = 6
	}
	if len(values) > maximum || !sort.StringsAreSorted(values) {
		return false
	}
	seen := map[string]bool{}
	for _, value := range values {
		if seen[value] {
			return false
		}
		seen[value] = true
		if !steps {
			if _, ok := queryDiagnoseFindingSteps[value]; !ok {
				return false
			}
		} else {
			found := value == "MEASURE_BEFORE_OPTIMIZING"
			for _, step := range queryDiagnoseFindingSteps {
				if value == step {
					found = true
				}
			}
			if !found {
				return false
			}
		}
	}
	return true
}
func queryDiagnoseErrorCode(value string) bool {
	return dataOneOf(value, "ARGUMENTS_INVALID", "REQUEST_INVALID", "SOURCE_NOT_FOUND", "SOURCE_BUSY", "SOURCE_INVALID", "SOURCE_CHANGED", "ENVIRONMENT_NOT_READY", "OBSERVATION_FAILED", "MODEL_FAILED", "MODEL_REJECTED", "AUDIT_FAILED", "DIAGNOSE_FAILED", "INTERRUPTED", "EVIDENCE_ALREADY_EXISTS")
}
