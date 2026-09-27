package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/spf13/cobra"
)

const warehouseSavedRequestScript = "tools/local_experiment/warehouse_lifecycle/status.py"

func validWarehouseSavedRequest(request dataChildRequest) bool {
	return request.Action == "saved-request" && warehouseSessionPattern.MatchString(request.Token) &&
		warehouseUUIDPattern.MatchString(request.Case) && request.Request == nil
}

type execWarehouseSavedRequestChild struct{}

func (execWarehouseSavedRequestChild) Run(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	return (execDataChild{command: warehouseSavedRequestCommand, responseLimit: 8192}).Run(ctx, request, emit)
}

func resolveWarehouseSavedRequestRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("warehouse saved request checkout unavailable")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("warehouse saved request checkout unavailable")
	}
	for _, relative := range []string{warehouseSavedRequestScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("warehouse saved request checkout or dedicated Python unavailable")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("warehouse saved request dedicated Python unavailable")
	}
	return root, nil
}

func warehouseSavedRequestCommand(request dataChildRequest) (*exec.Cmd, error) {
	if !validWarehouseSavedRequest(request) {
		return nil, errors.New("invalid fixed warehouse saved request")
	}
	root, err := resolveWarehouseSavedRequestRoot(request.Root)
	if err != nil || root != request.Root {
		return nil, errors.New("invalid warehouse saved request checkout")
	}
	cmd := exec.Command(filepath.Join(root, dataPython), "-I", "-B", filepath.Join(root, warehouseSavedRequestScript), request.Token, "--request-id", request.Case)
	cmd.Dir, cmd.Env = root, []string{}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd, nil
}

type warehouseSavedLookup struct {
	RequestID      string  `json:"request_id"`
	Observation    string  `json:"observation"`
	RecordedAction *string `json:"recorded_action"`
	RecordedResult *string `json:"recorded_result"`
	OperationID    *string `json:"operation_id"`
	ReceiptSHA256  *string `json:"receipt_sha256"`
}

func validateWarehouseSavedOutput(raw []byte, request dataChildRequest) error {
	invalid := errors.New("warehouse saved request response invalid")
	if len(raw) == 0 || len(raw) > 8192 || uniqueWarehouseSavedJSON(raw) != nil {
		return invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || !warehouseKeys(fields, "schema_version", "session_id", "journal_sha256", "observation", "runtime_state", "recorded_actions", "reply_count", "recorded_error_count", "request_lookup") {
		return invalid
	}
	var value struct {
		SchemaVersion   int    `json:"schema_version"`
		SessionID       string `json:"session_id"`
		JournalSHA256   string `json:"journal_sha256"`
		Observation     string `json:"observation"`
		RuntimeState    string `json:"runtime_state"`
		RecordedActions []struct {
			Action string `json:"action"`
			Result string `json:"recorded_result"`
		} `json:"recorded_actions"`
		ReplyCount         int                  `json:"reply_count"`
		RecordedErrorCount int                  `json:"recorded_error_count"`
		Lookup             warehouseSavedLookup `json:"request_lookup"`
	}
	var lookupFields map[string]json.RawMessage
	if json.Unmarshal(raw, &value) != nil || value.SchemaVersion != 1 || value.SessionID != request.Token ||
		!warehouseHashPattern.MatchString(value.JournalSHA256) || value.Observation != "saved-journal-only" || value.RuntimeState != "unknown" ||
		value.Lookup.RequestID != request.Case || json.Unmarshal(fields["request_lookup"], &lookupFields) != nil ||
		!warehouseKeys(lookupFields, "request_id", "observation", "recorded_action", "recorded_result", "operation_id", "receipt_sha256") ||
		value.ReplyCount < 0 || value.ReplyCount > 16 || value.RecordedErrorCount < 0 || value.RecordedErrorCount > 16 || len(value.RecordedActions) > 16 {
		return invalid
	}
	for _, action := range value.RecordedActions {
		if action.Action != "query" && action.Action != "chill" && action.Action != "wake" {
			return invalid
		}
		switch action.Result {
		case "pending", "verified", "noop", "unknown", "failed", "busy":
		default:
			return invalid
		}
	}
	lookup := value.Lookup
	if lookup.Observation == "not-recorded-in-snapshot" {
		if lookup.RecordedAction == nil && lookup.RecordedResult == nil && lookup.OperationID == nil && lookup.ReceiptSHA256 == nil {
			return nil
		}
		return invalid
	}
	if lookup.Observation != "recorded-in-snapshot" || lookup.RecordedAction == nil || lookup.RecordedResult == nil {
		return invalid
	}
	if *lookup.RecordedAction != "query" && *lookup.RecordedAction != "chill" && *lookup.RecordedAction != "wake" {
		return invalid
	}
	found := false
	for _, action := range value.RecordedActions {
		found = found || action.Action == *lookup.RecordedAction && action.Result == *lookup.RecordedResult
	}
	if !found {
		return invalid
	}
	switch *lookup.RecordedResult {
	case "pending", "verified", "noop", "unknown", "failed", "busy":
	default:
		return invalid
	}
	if (lookup.OperationID == nil) != (lookup.ReceiptSHA256 == nil) || lookup.OperationID != nil &&
		(!warehouseUUIDPattern.MatchString(*lookup.OperationID) || !warehouseHashPattern.MatchString(*lookup.ReceiptSHA256)) {
		return invalid
	}
	return nil
}

// Unlike transition replies, archived reports contain a bounded actions array.
func uniqueWarehouseSavedJSON(raw []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	var consume func(int) error
	consume = func(depth int) error {
		if depth > 4 {
			return errors.New("saved response nesting invalid")
		}
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		switch token {
		case json.Delim('{'):
			seen := make(map[string]bool)
			for decoder.More() {
				key, e := decoder.Token()
				if e != nil {
					return e
				}
				name, ok := key.(string)
				if !ok || seen[name] {
					return errors.New("saved response duplicate key")
				}
				seen[name] = true
				if e = consume(depth + 1); e != nil {
					return e
				}
			}
			_, err = decoder.Token()
		case json.Delim('['):
			count := 0
			for decoder.More() {
				count++
				if count > 16 {
					return errors.New("saved response array exceeds bound")
				}
				if e := consume(depth + 1); e != nil {
					return e
				}
			}
			_, err = decoder.Token()
		}
		return err
	}
	if err := consume(0); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return errors.New("saved response trailing data")
	}
	return nil
}

func runWarehouseSavedRequest(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if !validWarehouseSavedRequest(request) {
		return errors.New("invalid fixed warehouse saved request")
	}
	var line []byte
	result, err := child.Run(ctx, request, func(raw []byte) error {
		if line != nil || validateWarehouseSavedOutput(raw, request) != nil {
			return errors.New("warehouse saved request protocol invalid")
		}
		line = bytes.Clone(raw)
		return nil
	})
	if err != nil || !result.Started || result.Unconfirmed || result.ExitCode != 0 || line == nil {
		return errors.New("warehouse saved request not confirmed; no owner request was sent")
	}
	if _, err := output.Write(append(line, '\n')); err != nil {
		return errors.New("warehouse saved request output could not be delivered")
	}
	return nil
}

func newWarehouseSavedRequestCommand(child dataChildRunner) *cobra.Command {
	var sessionID, requestID, profile, root string
	command := &cobra.Command{Use: "saved-request", Short: "Inspect one saved warehouse request without replay", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if profile != localExperimentProfile || !warehouseSessionPattern.MatchString(sessionID) || !warehouseUUIDPattern.MatchString(requestID) {
				return errors.New("warehouse saved-request requires --profile local-experiment, --session and --request-id")
			}
			checkout, err := resolveWarehouseSavedRequestRoot(root)
			if err != nil {
				return err
			}
			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
			defer cancel()
			return runWarehouseSavedRequest(ctx, child, dataChildRequest{Root: checkout, Action: "saved-request", Token: sessionID, Case: requestID}, cmd.OutOrStdout())
		}}
	command.Flags().StringVar(&sessionID, "session", "", "archived session ID")
	command.Flags().StringVar(&requestID, "request-id", "", "original canonical request UUID")
	command.Flags().StringVar(&profile, "profile", localExperimentProfile, "warehouse profile: local-experiment")
	command.Flags().StringVar(&root, "repo-root", "", "checkout root (auto-detected from the current directory)")
	return command
}
