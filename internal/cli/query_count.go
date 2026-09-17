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
	"strconv"
	"strings"
	"syscall"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const queryCountCase = "snapshot-count-reader-v4"

type queryCountRequest struct {
	Command    string            `json:"command"`
	Table      string            `json:"table"`
	SnapshotID *string           `json:"snapshot_id"`
	Where      *queryFilterWhere `json:"where"`
}

func newQueryCountCommand(child dataChildRunner, root, profile *string) *cobra.Command {
	var table, snapshot, column, literal string
	command := &cobra.Command{Use: "count --table schema.name", Short: "Count matching rows in one fixed Iceberg snapshot", Args: func(_ *cobra.Command, args []string) error {
		if len(args) != 0 {
			return errors.New("query count does not accept positional arguments")
		}
		return nil
	}, RunE: func(cmd *cobra.Command, _ []string) error {
		if *profile != localExperimentProfile {
			return errors.New("query count requires --profile local-experiment")
		}
		value := queryCountRequest{Command: "count", Table: table}
		if cmd.Flags().Changed("snapshot-id") {
			value.SnapshotID = &snapshot
		}
		if cmd.Flags().Changed("where-column") != cmd.Flags().Changed("equals") {
			return errors.New("query count requires both --where-column and --equals")
		}
		if cmd.Flags().Changed("where-column") {
			value.Where = &queryFilterWhere{Column: column, Literal: literal}
		}
		return runQueryCountCLI(cmd, child, *root, value)
	}}
	command.Flags().StringVar(&table, "table", "", "table identifier: schema.name")
	command.Flags().StringVar(&snapshot, "snapshot-id", "", "positive retained snapshot ID (current head when omitted)")
	command.Flags().StringVar(&column, "where-column", "", "column for one typed non-NULL equality")
	command.Flags().StringVar(&literal, "equals", "", "bounded integer, decimal, date or boolean literal")
	command.SetFlagErrorFunc(func(_ *cobra.Command, _ error) error { return errors.New("query count flags are invalid") })
	return command
}

func runQueryCountCLI(cmd *cobra.Command, child dataChildRunner, root string, value queryCountRequest) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return errors.New("count reader input is invalid")
	}
	if _, err = decodeQueryCountRequest(raw, value.Command); err != nil {
		return err
	}
	resolved, err := resolveQueryCountRoot(root)
	if err != nil {
		return err
	}
	var token [16]byte
	if _, err = io.ReadFull(rand.Reader, token[:]); err != nil {
		return errors.New("count reader evidence identity unavailable")
	}
	request := dataChildRequest{Action: value.Command, Case: queryCountCase, Root: resolved, Token: hex.EncodeToString(token[:]), Request: raw}
	ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
	defer cancel()
	return runQueryCountCommand(ctx, child, request, cmd.OutOrStdout())
}

func decodeQueryCountRequest(raw []byte, action string) (queryCountRequest, error) {
	var value queryCountRequest
	invalid := errors.New("count reader input does not match the fixed count contract")
	if action != "count" || len(raw) == 0 || len(raw) > 4096 || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || json.Unmarshal(raw, &value) != nil || value.Command != action || !warehouseKeys(fields, "command", "table", "snapshot_id", "where") || !queryReadTable(value.Table) {
		return value, invalid
	}
	if value.SnapshotID != nil && !queryReadSnapshot(*value.SnapshotID, false) {
		return value, invalid
	}
	if value.Where != nil {
		var where map[string]json.RawMessage
		if json.Unmarshal(fields["where"], &where) != nil || !warehouseKeys(where, "column", "literal") || !queryReadIdentifier.MatchString(value.Where.Column) || !queryFilterLiteral(value.Where.Literal) {
			return value, invalid
		}
	}
	return value, nil
}

func queryCountErrorCode(code string) bool {
	return queryFilterErrorCode(code) || dataOneOf(code, "COUNT_RESULT_INVALID", "COUNT_VALUE_INVALID")
}

func runQueryCountCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if request.Case != queryCountCase || !warehouseSessionPattern.MatchString(request.Token) {
		return errors.New("invalid fixed count reader invocation")
	}
	if _, err := decodeQueryCountRequest(request.Request, request.Action); err != nil {
		return err
	}
	allocated := false
	var final *queryReadEvent
	var lineFinal []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeQueryCountEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("count reader event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			e = writeQueryReadLine(output, line)
			return e
		}
		if event.Result == "passed" && !allocated {
			return errors.New("count reader allocation missing")
		}
		final, lineFinal = &event, append([]byte(nil), line...)
		return nil // Final output is released only after the matching process exit.
	})
	if !result.Started {
		return errors.New("count reader child did not start; no read was confirmed")
	}
	unknown := fmt.Sprintf("count reader outcome unknown; evidence_token=%s child_pid=%d; inspect tmp/local-experiment/query-count/%s/summary.json before another read (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
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
	return &dataExitError{code: want, message: fmt.Sprintf("count reader result=%s; evidence_token=%s", final.Result, request.Token)}
}

func decodeQueryCountEvent(line []byte, request dataChildRequest) (queryReadEvent, error) {
	var value queryReadEvent
	invalid := errors.New("count reader child response failed the fixed protocol")
	if request.Action != "count" || request.Case != queryCountCase || !warehouseSessionPattern.MatchString(request.Token) {
		return value, invalid
	}
	if len(line) > queryCountResponseLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
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
	if !warehouseKeys(fields, keys...) || value.SchemaVersion != 4 || value.Command != "query "+request.Action || value.EvidenceToken != request.Token {
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
	if value.Evidence != "tmp/local-experiment/query-count/"+request.Token+"/summary.json" || !dataOneOf(value.Result, "passed", "failed", "interrupted") {
		return value, invalid
	}
	if value.Result == "passed" {
		input, err := decodeQueryCountRequest(request.Request, request.Action)
		if err != nil || !value.CleanupVerified || value.ErrorCode != nil || !queryCountObservation(value.Observation, input) {
			return value, invalid
		}
	} else {
		if !bytes.Equal(bytes.TrimSpace(value.Observation), []byte("null")) || value.ErrorCode == nil || !queryCountErrorCode(*value.ErrorCode) {
			return value, invalid
		}
		if value.Result == "interrupted" && *value.ErrorCode != "INTERRUPTED" {
			return value, invalid
		}
	}
	return value, nil
}

func queryCountObservation(raw []byte, request queryCountRequest) bool {
	if len(raw) == 0 || len(raw) > queryCountResponseLimit || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var decoded any
	if decoder.Decode(&decoded) != nil {
		return false
	}
	object, ok := queryReadObject(decoded, "schema_version", "table", "table_uuid", "schema_sha256", "snapshot_id", "current_snapshot_id", "selected_snapshot", "count", "where", "complete")
	if !ok {
		return false
	}
	_, version := queryReadInt(object["schema_version"], 4, 4)
	if !version || object["table"] != "iceberg."+request.Table || !queryReadUUID(object["table_uuid"]) || !queryReadHash(object["schema_sha256"]) || object["complete"] != true || !queryReadSnapshot(object["current_snapshot_id"], false) || !queryReadSnapshot(object["snapshot_id"], false) {
		return false
	}
	count, text := object["count"].(string)
	if !text || !queryReadInteger.MatchString(count) || strings.HasPrefix(count, "-") {
		return false
	}
	if n, err := strconv.ParseInt(count, 10, 64); err != nil || n < 0 {
		return false
	}
	selected := object["snapshot_id"].(string)
	record, valid := querySnapshotRecord(object["selected_snapshot"])
	if !valid || record["snapshot_id"] != selected || record["schema_compatibility"] != "compatible" || record["is_current"] != (selected == object["current_snapshot_id"]) {
		return false
	}
	if request.SnapshotID != nil && selected != *request.SnapshotID || request.SnapshotID == nil && selected != object["current_snapshot_id"] {
		return false
	}
	if request.Where == nil {
		return object["where"] == nil
	}
	where, valid := queryReadObject(object["where"], "column", "type", "operator")
	if !valid || where["column"] != request.Where.Column || where["operator"] != "eq" {
		return false
	}
	kind, isType := where["type"].(string)
	precision, _, supported := queryReadType(kind)
	return isType && supported && (precision > 0 || dataOneOf(kind, "integer", "bigint", "date", "boolean")) && queryFilterTypedLiteral(request.Where.Literal, kind)
}
