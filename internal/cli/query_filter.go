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
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const queryFilterCase = "filtered-snapshot-reader-v3"
const queryFilterExtraCodes = `FILTER_INVALID FILTER_COLUMN_INVALID FILTER_TYPE_UNSUPPORTED FILTER_VALUE_INVALID FILTER_RESULT_INVALID`

var queryFilterDate = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`)

type queryFilterWhere struct {
	Column  string `json:"column"`
	Literal string `json:"literal"`
}

type queryFilterRequest struct {
	Command    string           `json:"command"`
	Table      string           `json:"table"`
	Columns    []string         `json:"columns"`
	Limit      int              `json:"limit"`
	SnapshotID *string          `json:"snapshot_id"`
	Where      queryFilterWhere `json:"where"`
}

func runQueryFilterCLI(cmd *cobra.Command, child dataChildRunner, root string, value queryFilterRequest) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return errors.New("filtered reader input is invalid")
	}
	if _, err = decodeQueryFilterRequest(raw, value.Command); err != nil {
		return err
	}
	resolved, err := resolveQueryFilterRoot(root)
	if err != nil {
		return err
	}
	var token [16]byte
	if _, err = io.ReadFull(rand.Reader, token[:]); err != nil {
		return errors.New("filtered reader evidence identity unavailable")
	}
	request := dataChildRequest{Action: value.Command, Case: queryFilterCase, Root: resolved, Token: hex.EncodeToString(token[:]), Request: raw}
	ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
	defer cancel()
	return runQueryFilterCommand(ctx, child, request, cmd.OutOrStdout())
}

func queryFilterLiteral(literal string) bool {
	if len(literal) == 0 || len(literal) > 128 {
		return false
	}
	if dataOneOf(literal, "true", "false") || queryReadDecimalValue.MatchString(literal) {
		return true
	}
	if !queryFilterDate.MatchString(literal) {
		return false
	}
	d, e := time.Parse("2006-01-02", literal)
	return e == nil && d.Year() >= 1 && d.Format("2006-01-02") == literal
}

func decodeQueryFilterRequest(raw []byte, action string) (queryFilterRequest, error) {
	var value queryFilterRequest
	invalid := errors.New("filtered reader input does not match the fixed equality contract")
	if action != "select" || len(raw) == 0 || len(raw) > 4096 || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || json.Unmarshal(raw, &value) != nil || value.Command != action || !warehouseKeys(fields, "command", "table", "columns", "limit", "snapshot_id", "where") {
		return value, invalid
	}
	var where map[string]json.RawMessage
	if json.Unmarshal(fields["where"], &where) != nil || !warehouseKeys(where, "column", "literal") || !queryReadIdentifier.MatchString(value.Where.Column) || !queryFilterLiteral(value.Where.Literal) {
		return value, invalid
	}
	if value.SnapshotID != nil && !queryReadSnapshot(*value.SnapshotID, false) {
		return value, invalid
	}
	delete(fields, "snapshot_id")
	delete(fields, "where")
	base, _ := json.Marshal(fields)
	if _, err := decodeQueryReadRequest(base, action); err != nil {
		return value, invalid
	}
	return value, nil
}

func queryFilterErrorCode(code string) bool {
	if querySnapshotsErrorCode(code) {
		return true
	}
	for _, allowed := range strings.Fields(queryFilterExtraCodes) {
		if code == allowed {
			return true
		}
	}
	return false
}

// Check only exact representability; the host discovers the column type.
// No floating-point conversion or rounding is permitted for filter literals.
func queryFilterTypedLiteral(literal, kind string) bool {
	if !queryFilterLiteral(literal) {
		return false
	}
	switch kind {
	case "integer", "bigint":
		if !queryReadInteger.MatchString(literal) {
			return false
		}
		bits := 64
		if kind == "integer" {
			bits = 32
		}
		_, err := strconv.ParseInt(literal, 10, bits)
		return err == nil
	case "boolean":
		return dataOneOf(literal, "true", "false")
	case "date":
		return queryFilterDate.MatchString(literal)
	default:
		precision, scale, ok := queryReadType(kind)
		if !ok || precision == 0 || !queryReadDecimalValue.MatchString(literal) {
			return false
		}
		parts := strings.Split(strings.TrimPrefix(literal, "-"), ".")
		fraction := ""
		if len(parts) == 2 {
			fraction = strings.TrimRight(parts[1], "0")
		}
		if len(fraction) > scale {
			return false
		}
		coefficient := strings.TrimLeft(parts[0]+fraction+strings.Repeat("0", scale-len(fraction)), "0")
		return len(coefficient) <= precision
	}
}

func queryFilterValue(literal, kind string) any {
	switch kind {
	case "integer", "bigint":
		value, _ := strconv.ParseInt(literal, 10, 64)
		return strconv.FormatInt(value, 10)
	case "boolean":
		return literal == "true"
	case "date":
		return literal
	default:
		_, scale, _ := queryReadType(kind)
		parts := strings.Split(strings.TrimPrefix(literal, "-"), ".")
		fraction := ""
		if len(parts) == 2 {
			fraction = strings.TrimRight(parts[1], "0")
		}
		fraction += strings.Repeat("0", scale-len(fraction))
		value := parts[0]
		if scale > 0 {
			value += "." + fraction
		}
		if strings.HasPrefix(literal, "-") && strings.TrimLeft(parts[0]+fraction, "0") != "" {
			value = "-" + value
		}
		return value
	}
}

func runQueryFilterCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if request.Case != queryFilterCase || !warehouseSessionPattern.MatchString(request.Token) {
		return errors.New("invalid fixed filtered reader invocation")
	}
	if _, err := decodeQueryFilterRequest(request.Request, request.Action); err != nil {
		return err
	}
	allocated := false
	var final *queryReadEvent
	var lineFinal []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeQueryFilterEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("filtered reader event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			e = writeQueryReadLine(output, line)
			return e
		}
		if event.Result == "passed" && !allocated {
			return errors.New("filtered reader allocation missing")
		}
		final, lineFinal = &event, append([]byte(nil), line...)
		return nil // Final output is released only after the matching process exit.
	})
	if !result.Started {
		return errors.New("filtered reader child did not start; no read was confirmed")
	}
	unknown := fmt.Sprintf("filtered reader outcome unknown; evidence_token=%s child_pid=%d; inspect tmp/local-experiment/query-filter/%s/summary.json before another read (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
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
	return &dataExitError{code: want, message: fmt.Sprintf("filtered reader result=%s; evidence_token=%s", final.Result, request.Token)}
}

func decodeQueryFilterEvent(line []byte, request dataChildRequest) (queryReadEvent, error) {
	var value queryReadEvent
	invalid := errors.New("filtered reader child response failed the fixed protocol")
	if request.Action != "select" || request.Case != queryFilterCase || !warehouseSessionPattern.MatchString(request.Token) {
		return value, invalid
	}
	if len(line) > queryFilterResponseLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
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
	if !warehouseKeys(fields, keys...) || value.SchemaVersion != 3 || value.Command != "query "+request.Action || value.EvidenceToken != request.Token {
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
	if value.Evidence != "tmp/local-experiment/query-filter/"+request.Token+"/summary.json" || !dataOneOf(value.Result, "passed", "failed", "interrupted") {
		return value, invalid
	}
	if value.Result == "passed" {
		input, err := decodeQueryFilterRequest(request.Request, request.Action)
		if err != nil || !value.CleanupVerified || value.ErrorCode != nil || !queryFilterObservation(value.Observation, input) {
			return value, invalid
		}
	} else {
		if !bytes.Equal(bytes.TrimSpace(value.Observation), []byte("null")) || value.ErrorCode == nil || !queryFilterErrorCode(*value.ErrorCode) {
			return value, invalid
		}
		if value.Result == "interrupted" && *value.ErrorCode != "INTERRUPTED" {
			return value, invalid
		}
	}
	return value, nil
}

func queryFilterObservation(raw []byte, request queryFilterRequest) bool {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var decoded any
	if decoder.Decode(&decoded) != nil {
		return false
	}
	object, ok := queryReadObject(decoded, "schema_version", "table", "table_uuid", "schema_sha256", "snapshot_id", "columns", "rows", "row_count", "fetched_row_count", "truncated", "requested_limit", "ordering", "rows_sha256", "current_snapshot_id", "selected_snapshot", "where")
	if !ok {
		return false
	}
	_, version := queryReadInt(object["schema_version"], 3, 3)
	where, valid := queryReadObject(object["where"], "column", "type", "operator")
	if !version || !valid || where["column"] != request.Where.Column || where["operator"] != "eq" {
		return false
	}
	kind, isType := where["type"].(string)
	precision, _, supported := queryReadType(kind)
	if !isType || !supported || !(precision > 0 || dataOneOf(kind, "integer", "bigint", "date", "boolean")) || !queryFilterTypedLiteral(request.Where.Literal, kind) {
		return false
	}
	columns, valid := queryReadColumns(object["columns"], 16)
	if !valid {
		return false
	}
	for _, column := range columns {
		if column["name"] == request.Where.Column && column["type"] != kind {
			return false
		}
	}
	selected, valid := object["snapshot_id"].(string)
	if !valid || request.SnapshotID != nil && selected != *request.SnapshotID || request.SnapshotID == nil && selected != object["current_snapshot_id"] {
		return false
	}
	delete(object, "where")
	object["schema_version"] = json.Number("2")
	base, err := json.Marshal(object)
	if err != nil || !querySnapshotsObservation(base, querySnapshotsRequest{Command: "select", Table: request.Table, Columns: request.Columns, Limit: request.Limit, SnapshotID: selected}) {
		return false
	}
	for index, column := range columns {
		if column["name"] == request.Where.Column {
			want := queryFilterValue(request.Where.Literal, kind)
			for _, row := range object["rows"].([]any) {
				if row.([]any)[index].(map[string]any)["value"] != want {
					return false
				}
			}
		}
	}
	return true
}
