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
	"math"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const querySnapshotsCase = "snapshots-v2"
const querySnapshotsExtraCodes = `
SNAPSHOT_LIMIT SNAPSHOT_HISTORY_INVALID SNAPSHOT_SCHEMA_INVALID SNAPSHOT_NOT_RETAINED
SNAPSHOT_SCHEMA_UNSUPPORTED SNAPSHOT_WINDOW_INVALID SNAPSHOT_WINDOW_CHANGED
OBSERVATION_FAILED SNAPSHOT_READ_FAILED SNAPSHOT_READ_RETRY_FORBIDDEN
SNAPSHOT_SOURCE_CHANGED SNAPSHOT_CLEANUP_UNVERIFIED EVIDENCE_ALREADY_EXISTS
`

type querySnapshotsRequest struct {
	Command    string   `json:"command"`
	Table      string   `json:"table"`
	Columns    []string `json:"columns,omitempty"`
	Limit      int      `json:"limit,omitempty"`
	SnapshotID string   `json:"snapshot_id,omitempty"`
}

func newQuerySnapshotsCommand(child dataChildRunner, root, profile *string) *cobra.Command {
	var table string
	command := &cobra.Command{Use: "snapshots --table schema.name", Short: "List retained snapshots of one discovered Iceberg table", Args: func(_ *cobra.Command, args []string) error {
		if len(args) != 0 {
			return errors.New("query snapshots does not accept positional arguments")
		}
		return nil
	}, RunE: func(cmd *cobra.Command, _ []string) error {
		if *profile != localExperimentProfile {
			return errors.New("query snapshots requires --profile local-experiment")
		}
		return runQuerySnapshotsCLI(cmd, child, *root, querySnapshotsRequest{Command: "snapshots", Table: table})
	}}
	command.Flags().StringVar(&table, "table", "", "table identifier: schema.name")
	command.SetFlagErrorFunc(func(_ *cobra.Command, _ error) error { return errors.New("query snapshots flags are invalid") })
	return command
}

func runQuerySnapshotsCLI(cmd *cobra.Command, child dataChildRunner, root string, value querySnapshotsRequest) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return errors.New("snapshot reader input is invalid")
	}
	if _, err = decodeQuerySnapshotsRequest(raw, value.Command); err != nil {
		return err
	}
	resolved, err := resolveQuerySnapshotsRoot(root)
	if err != nil {
		return err
	}
	var token [16]byte
	if _, err = io.ReadFull(rand.Reader, token[:]); err != nil {
		return errors.New("snapshot reader evidence identity unavailable")
	}
	request := dataChildRequest{Action: value.Command, Case: querySnapshotsCase, Root: resolved, Token: hex.EncodeToString(token[:]), Request: raw}
	ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
	defer cancel()
	return runQuerySnapshotsCommand(ctx, child, request, cmd.OutOrStdout())
}

func decodeQuerySnapshotsRequest(raw []byte, action string) (querySnapshotsRequest, error) {
	var value querySnapshotsRequest
	invalid := errors.New("snapshot reader input does not match the fixed table, columns, limit and snapshot contract")
	if len(raw) == 0 || len(raw) > 4096 || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || json.Unmarshal(raw, &value) != nil || value.Command != action {
		return value, invalid
	}
	keys := []string{"command", "table"}
	if action == "select" {
		keys = append(keys, "columns", "limit", "snapshot_id")
	} else if action != "snapshots" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) {
		return value, invalid
	}
	if action == "select" {
		if !queryReadSnapshot(value.SnapshotID, false) {
			return value, invalid
		}
		delete(fields, "snapshot_id")
	} else {
		fields["command"] = json.RawMessage(`"describe"`)
	}
	base, _ := json.Marshal(fields)
	baseAction := action
	if action == "snapshots" {
		baseAction = "describe"
	}
	if _, err := decodeQueryReadRequest(base, baseAction); err != nil {
		return value, invalid
	}
	return value, nil
}

func querySnapshotsErrorCode(code string) bool {
	if queryReadErrorCode(code) {
		return true
	}
	for _, allowed := range strings.Fields(querySnapshotsExtraCodes) {
		if code == allowed {
			return true
		}
	}
	return false
}

func runQuerySnapshotsCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if request.Case != querySnapshotsCase || !warehouseSessionPattern.MatchString(request.Token) {
		return errors.New("invalid fixed snapshot reader invocation")
	}
	if _, err := decodeQuerySnapshotsRequest(request.Request, request.Action); err != nil {
		return err
	}
	allocated := false
	var final *queryReadEvent
	var lineFinal []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeQuerySnapshotsEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("snapshot reader event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			e = writeQueryReadLine(output, line)
			return e
		}
		if event.Result == "passed" && !allocated {
			return errors.New("snapshot reader allocation missing")
		}
		final, lineFinal = &event, append([]byte(nil), line...)
		return nil // Final output is released only after the matching process exit.
	})
	if !result.Started {
		return errors.New("snapshot reader child did not start; no read was confirmed")
	}
	unknown := fmt.Sprintf("snapshot reader outcome unknown; evidence_token=%s child_pid=%d; inspect tmp/local-experiment/query-snapshots/%s/summary.json before another read (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
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
	if err := writeQueryReadLine(output, lineFinal); err != nil {
		return errors.New(unknown)
	}
	if want == 0 {
		return nil
	}
	return &dataExitError{code: want, message: fmt.Sprintf("snapshot reader result=%s; evidence_token=%s", final.Result, request.Token)}
}

func decodeQuerySnapshotsEvent(line []byte, request dataChildRequest) (queryReadEvent, error) {
	var value queryReadEvent
	invalid := errors.New("snapshot reader child response failed the fixed protocol")
	if request.Case != querySnapshotsCase || !warehouseSessionPattern.MatchString(request.Token) {
		return value, invalid
	}
	if len(line) > querySnapshotsResponseLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(line, &fields) != nil || json.Unmarshal(line, &value) != nil {
		return value, invalid
	}
	keys := []string{"schema_version", "event", "command", "evidence_token"}
	if value.Event == "result" {
		keys = append(keys, "result", "observation", "error_code", "evidence", "cleanup_verified")
	} else if value.Event != "allocated" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) || value.SchemaVersion != 2 || value.Command != "query "+request.Action || value.EvidenceToken != request.Token {
		return value, invalid
	}
	for key, raw := range fields {
		if key != "error_code" && key != "observation" && bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return value, invalid
		}
	}
	if value.Event == "allocated" {
		return value, nil
	}
	if value.Evidence != "tmp/local-experiment/query-snapshots/"+request.Token+"/summary.json" || !dataOneOf(value.Result, "passed", "failed", "interrupted") {
		return value, invalid
	}
	if value.Result == "passed" {
		input, err := decodeQuerySnapshotsRequest(request.Request, request.Action)
		if err != nil || !value.CleanupVerified || value.ErrorCode != nil || !querySnapshotsObservation(value.Observation, input) {
			return value, invalid
		}
	} else {
		if !bytes.Equal(bytes.TrimSpace(value.Observation), []byte("null")) || value.ErrorCode == nil || !querySnapshotsErrorCode(*value.ErrorCode) {
			return value, invalid
		}
		if value.Result == "interrupted" && *value.ErrorCode != "INTERRUPTED" {
			return value, invalid
		}
	}
	return value, nil
}

func querySnapshotsObservation(raw []byte, request querySnapshotsRequest) bool {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var decoded any
	if decoder.Decode(&decoded) != nil {
		return false
	}
	if request.Command == "select" {
		object, ok := queryReadObject(decoded, "schema_version", "table", "table_uuid", "schema_sha256", "snapshot_id", "columns", "rows", "row_count", "fetched_row_count", "truncated", "requested_limit", "ordering", "rows_sha256", "current_snapshot_id", "selected_snapshot")
		if !ok {
			return false
		}
		_, version := queryReadInt(object["schema_version"], 2, 2)
		selected, valid := querySnapshotRecord(object["selected_snapshot"])
		if !version || !valid || !queryReadSnapshot(object["current_snapshot_id"], false) || object["snapshot_id"] != request.SnapshotID || selected["snapshot_id"] != request.SnapshotID || selected["schema_compatibility"] != "compatible" || selected["is_current"] != (request.SnapshotID == object["current_snapshot_id"]) {
			return false
		}
		base := map[string]any{}
		for k, v := range object {
			if k != "current_snapshot_id" && k != "selected_snapshot" {
				base[k] = v
			}
		}
		base["schema_version"] = json.Number("1")
		return queryReadRows(base, queryReadRequest{Command: "select", Table: request.Table, Columns: request.Columns, Limit: request.Limit})
	}
	object, ok := queryReadObject(decoded, "schema_version", "table", "table_uuid", "current_snapshot_id", "snapshots", "complete", "ordering")
	if !ok {
		return false
	}
	_, version := queryReadInt(object["schema_version"], 2, 2)
	values, list := object["snapshots"].([]any)
	if !version || object["table"] != "iceberg."+request.Table || !queryReadUUID(object["table_uuid"]) || !queryReadSnapshot(object["current_snapshot_id"], true) || !list || len(values) > 64 || object["complete"] != true || object["ordering"] != "timestamp-asc" {
		return false
	}
	records := make([]map[string]any, 0, len(values))
	byID := map[string]map[string]any{}
	for _, v := range values {
		row, valid := querySnapshotRecord(v)
		if !valid {
			return false
		}
		id := row["snapshot_id"].(string)
		if _, duplicate := byID[id]; duplicate {
			return false
		}
		if len(records) > 0 && !querySnapshotLess(records[len(records)-1], row) {
			return false
		}
		records = append(records, row)
		byID[id] = row
	}
	if len(records) == 0 {
		return object["current_snapshot_id"] == nil
	}
	current, ok := object["current_snapshot_id"].(string)
	if !ok || byID[current] == nil {
		return false
	}
	for _, row := range records {
		id := row["snapshot_id"].(string)
		parent, _ := row["parent_snapshot_id"].(string)
		if row["is_current"] != (id == current) || row["parent_retained"] != (byID[parent] != nil) {
			return false
		}
		seen := map[string]bool{}
		for cursor := id; byID[cursor] != nil; {
			if seen[cursor] {
				return false
			}
			seen[cursor] = true
			cursor, _ = byID[cursor]["parent_snapshot_id"].(string)
		}
	}
	return true
}

func querySnapshotRecord(value any) (map[string]any, bool) {
	row, ok := queryReadObject(value, "snapshot_id", "parent_snapshot_id", "sequence_number", "timestamp_ms", "schema_id", "operation", "is_current", "parent_retained", "schema_compatibility")
	if !ok || !queryReadSnapshot(row["snapshot_id"], false) || !queryReadSnapshot(row["parent_snapshot_id"], true) || row["snapshot_id"] == row["parent_snapshot_id"] {
		return nil, false
	}
	seq, isString := row["sequence_number"].(string)
	if !isString || !queryReadInteger.MatchString(seq) || strings.HasPrefix(seq, "-") {
		return nil, false
	}
	if n, err := strconv.ParseInt(seq, 10, 64); err != nil || n < 0 {
		return nil, false
	}
	_, timeOK := queryReadInt(row["timestamp_ms"], 0, 253402300799999)
	schemaOK := row["schema_id"] == nil
	if !schemaOK {
		_, schemaOK = queryReadInt(row["schema_id"], 0, math.MaxInt32)
	}
	operation, opString := row["operation"].(string)
	compatibility, compatString := row["schema_compatibility"].(string)
	_, currentBool := row["is_current"].(bool)
	parentRetained, parentBool := row["parent_retained"].(bool)
	if !timeOK || !schemaOK || !opString || !dataOneOf(operation, "append", "replace", "overwrite", "delete") || !compatString || !dataOneOf(compatibility, "compatible", "schema-missing", "schema-not-retained", "schema-mismatch", "schema-unsupported") || !currentBool || !parentBool || (row["schema_id"] == nil) != (compatibility == "schema-missing") || (row["parent_snapshot_id"] == nil && parentRetained) {
		return nil, false
	}
	return row, true
}

func querySnapshotLess(a, b map[string]any) bool {
	for _, key := range []string{"timestamp_ms", "sequence_number", "snapshot_id"} {
		var x, y int64
		if key == "timestamp_ms" {
			x, _ = queryReadInt(a[key], 0, 253402300799999)
			y, _ = queryReadInt(b[key], 0, 253402300799999)
		} else {
			x, _ = strconv.ParseInt(a[key].(string), 10, 64)
			y, _ = strconv.ParseInt(b[key].(string), 10, 64)
		}
		if x != y {
			return x < y
		}
	}
	return false
}
