package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

var queryReadIdentifier = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,62}$`)

const queryReadPublicCodes = `
ARGUMENTS_INVALID AUTHENTICATION_RETRY_FORBIDDEN BUILD_IDENTITY_MISMATCH CANCEL_ID_NOT_OWNED
CANCEL_PROVENANCE_INVALID CANCEL_RESPONSE_INVALID CANCEL_RETRY_FORBIDDEN CANCEL_UNCERTAIN
CATALOG_AUTHENTICATION_FAILED CATALOG_READ_FAILED CATALOG_READ_LIMIT CATALOG_READ_NOT_READY
CATALOG_RESPONSE_INVALID CATALOG_RESPONSE_LIMIT CELL_BOOLEAN_INVALID CELL_DATE_INVALID
CELL_DECIMAL_INVALID CELL_FLOAT_INVALID CELL_INTEGER_INVALID CELL_LIMIT CELL_NULL_INVALID
CELL_NUMBER_INVALID CELL_STRING_INVALID COLUMNS_INVALID COLUMN_NOT_DISCOVERED COMMAND_INVALID
COMMAND_MISMATCH CONTROL_INVALID CONTROL_SQL_INVALID DRAIN_DEADLINE DRAIN_IDENTITY_INVALID
DRAIN_INVENTORY_INVALID DRAIN_PROVENANCE_INVALID DRAIN_SCHEMA_INVALID DRAIN_TIMEOUT_INVALID
DRAIN_UNTRACKED_QUERY DRAIN_UNVERIFIED DUPLICATE_COLUMNS DUPLICATE_JSON_KEY FILE_TOO_LARGE
FORMAT_UNSUPPORTED IDENTIFIER_INVALID IDENTITY_CHANGED IDENTITY_INVALID IDENTITY_SCHEMA_DIFFER
INPUT_LIMIT INTERRUPTED INVALID_RUN_TOKEN INVENTORY_INCOMPLETE INVENTORY_INVALID
JOURNAL_AND_GUARD_REQUIRED JOURNAL_REQUIRED JSON_INVALID LIMIT_INVALID LOCAL_COMMAND_FAILED
METADATA_INVALID METADATA_LIMIT METADATA_LOCATION_INVALID NONPRIVATE_EVIDENCE NO_CURRENT_SNAPSHOT
PROCESS_OUTPUT_LIMIT PUBLIC_RESULT_INVALID PUBLIC_RESULT_LIMIT QUERY_CELL_TYPE_INVALID
QUERY_COLUMNS_CHANGED QUERY_COLUMNS_INVALID QUERY_CONTENT_TYPE_INVALID QUERY_CONTINUATION_CHANGED
QUERY_DEADLINE QUERY_DRAIN_UNVERIFIED QUERY_ERROR_INVALID QUERY_FAILED QUERY_HTTP_ORIGIN_INVALID
QUERY_ID_INVALID QUERY_INPUT_INVALID QUERY_LABEL_INVALID QUERY_NEXT_INVALID QUERY_NOT_READ_ONLY
QUERY_ORIGIN_CHANGED QUERY_OUTCOME_UNRESOLVED QUERY_PAGE_BOUND QUERY_PAGE_BYTES_BOUND
QUERY_RESPONSE_INVALID QUERY_ROWS_INVALID QUERY_ROW_BOUND QUERY_SCHEMA_MISSING QUERY_STATE_INVALID
QUERY_TERMINAL_INVALID QUERY_TOTAL_BYTES_BOUND READ_CLEANUP_UNVERIFIED READ_FAILED READ_RETRY_FORBIDDEN
READ_SOURCE_CHANGED READ_SQL_INVALID REFS_INVALID REQUEST_INVALID REQUEST_KEYS_INVALID
RESPONSE_DUPLICATE_KEY RESPONSE_JSON_INVALID RESPONSE_NONFINITE REST_TRINO_SCHEMA_DIFFER
RESULT_INPUT_LIMIT RESULT_SCHEMA_INVALID RUN_ACTIVE SCHEMA_COLUMNS_INVALID SCHEMA_COLUMN_LIMIT
SCHEMA_INVALID SCHEMA_UNSUPPORTED SNAPSHOT_INVALID SPEC_INVALID TABLE_INVALID TABLE_LOCATION_INVALID
TABLE_NOT_DISCOVERED TABLE_UUID_INVALID TIMEOUT_INVALID TOKEN_INVALID TRINO_QUERY_FAILED
TRINO_SERVICE_CHANGED TRINO_SERVICE_INVALID TYPE_UNSUPPORTED UNSAFE_FILE UNSAFE_PATH UNSAFE_RUN_LOCK
URL_INVALID
`

var queryReadInteger = regexp.MustCompile(`^-?(0|[1-9][0-9]*)$`)
var queryReadDecimal = regexp.MustCompile(`^decimal\(([1-9][0-9]?),([0-9]|[1-9][0-9])\)$`)
var queryReadDecimalValue = regexp.MustCompile(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`)

type queryReadRequest struct {
	Command string   `json:"command"`
	Schema  string   `json:"schema,omitempty"`
	Table   string   `json:"table,omitempty"`
	Columns []string `json:"columns,omitempty"`
	Limit   int      `json:"limit,omitempty"`
}

type queryReadEvent struct {
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

func newQueryReadCommand(action string, child dataChildRunner, root, profile *string) *cobra.Command {
	return newQueryReadCommandWithSnapshots(action, child, execQuerySnapshotsChild{}, root, profile)
}

func newQueryReadCommandWithSnapshots(action string, child, snapshots dataChildRunner, root, profile *string) *cobra.Command {
	var schema, table, columns, snapshotID string
	var limit int
	command := &cobra.Command{Use: action, Short: map[string]string{"tables": "List the fixed Iceberg schema's tables", "describe": "Inspect a discovered Iceberg table", "select": "Read bounded columns from one fixed snapshot"}[action], Args: func(_ *cobra.Command, args []string) error {
		if len(args) != 0 {
			return errors.New("query reader does not accept positional arguments")
		}
		return nil
	},
		RunE: func(cmd *cobra.Command, _ []string) error {
			if *profile != localExperimentProfile {
				return errors.New("query reader requires --profile local-experiment")
			}
			if action == "select" && cmd.Flags().Changed("snapshot-id") {
				return runQuerySnapshotsCLI(cmd, snapshots, *root, querySnapshotsRequest{Command: action, Table: table, Columns: strings.Split(columns, ","), Limit: limit, SnapshotID: snapshotID})
			}
			value := queryReadRequest{Command: action, Schema: schema, Table: table, Limit: limit}
			if action == "select" {
				value.Columns = strings.Split(columns, ",")
			}
			raw, err := json.Marshal(value)
			if err != nil {
				return errors.New("query reader input is invalid")
			}
			if _, err = decodeQueryReadRequest(raw, action); err != nil {
				return err
			}
			resolved, err := resolveQueryReadRoot(*root)
			if err != nil {
				return err
			}
			var token [16]byte
			if _, err = io.ReadFull(rand.Reader, token[:]); err != nil {
				return errors.New("query reader evidence identity unavailable")
			}
			request := dataChildRequest{Action: action, Root: resolved, Token: hex.EncodeToString(token[:]), Request: raw}
			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
			defer cancel()
			return runQueryReadCommand(ctx, child, request, cmd.OutOrStdout())
		},
	}
	command.SetFlagErrorFunc(func(_ *cobra.Command, _ error) error { return errors.New("query reader flags are invalid") })
	if action == "tables" {
		command.Flags().StringVar(&schema, "schema", "", "fixed schema: smoke or local_experiment")
	} else {
		command.Flags().StringVar(&table, "table", "", "table identifier: schema.name")
	}
	if action == "select" {
		command.Flags().StringVar(&columns, "columns", "", "1–16 distinct column names, separated by commas")
		command.Flags().IntVar(&limit, "limit", 0, "maximum rows: 1–100 (required)")
		command.Flags().StringVar(&snapshotID, "snapshot-id", "", "explicit retained positive int64 snapshot ID")
	}
	return command
}

func decodeQueryReadRequest(raw []byte, action string) (queryReadRequest, error) {
	var value queryReadRequest
	invalid := errors.New("query reader input does not match the fixed schema, table, columns and limit contract")
	if len(raw) == 0 || len(raw) > 4096 || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || json.Unmarshal(raw, &value) != nil || value.Command != action {
		return value, invalid
	}
	keys := []string{"command", "table"}
	switch action {
	case "tables":
		keys = []string{"command", "schema"}
	case "select":
		keys = append(keys, "columns", "limit")
	case "describe":
	default:
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) {
		return value, invalid
	}
	if action == "tables" {
		if !dataOneOf(value.Schema, "smoke", "local_experiment") {
			return value, invalid
		}
	} else if !queryReadTable(value.Table) {
		return value, invalid
	}
	if action == "select" {
		if value.Limit < 1 || value.Limit > 100 || len(value.Columns) < 1 || len(value.Columns) > 16 {
			return value, invalid
		}
		seen := map[string]bool{}
		for _, name := range value.Columns {
			if !queryReadIdentifier.MatchString(name) || seen[name] {
				return value, invalid
			}
			seen[name] = true
		}
	}
	return value, nil
}

func queryReadTable(table string) bool {
	parts := strings.Split(table, ".")
	return len(parts) == 2 && dataOneOf(parts[0], "smoke", "local_experiment") && queryReadIdentifier.MatchString(parts[1])
}

func runQueryReadCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if _, err := decodeQueryReadRequest(request.Request, request.Action); err != nil {
		return err
	}
	allocated := false
	var final *queryReadEvent
	var lineFinal []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeQueryReadEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("query reader event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			e = writeQueryReadLine(output, line)
			return e
		}
		if event.Result == "passed" && !allocated {
			return errors.New("query reader allocation missing")
		}
		final, lineFinal = &event, append([]byte(nil), line...)
		return nil // Final output is released only after the matching process exit.
	})
	if !result.Started {
		return errors.New("query reader child did not start; no read was confirmed")
	}
	unknown := fmt.Sprintf("query reader outcome unknown; evidence_token=%s child_pid=%d; inspect tmp/local-experiment/query-read/%s/summary.json before another read (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
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
	return &dataExitError{code: want, message: fmt.Sprintf("query reader result=%s; evidence_token=%s", final.Result, request.Token)}
}

func decodeQueryReadEvent(line []byte, request dataChildRequest) (queryReadEvent, error) {
	var value queryReadEvent
	invalid := errors.New("query reader child response failed the fixed protocol")
	if len(line) > queryReadResponseLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
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
	if !warehouseKeys(fields, keys...) || value.SchemaVersion != 1 || value.Command != "query "+request.Action || value.EvidenceToken != request.Token {
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
	if value.Evidence != "tmp/local-experiment/query-read/"+request.Token+"/summary.json" || !dataOneOf(value.Result, "passed", "failed", "interrupted") {
		return value, invalid
	}
	if value.Result == "passed" {
		input, err := decodeQueryReadRequest(request.Request, request.Action)
		if err != nil || !value.CleanupVerified || value.ErrorCode != nil || !queryReadObservation(value.Observation, input) {
			return value, invalid
		}
	} else {
		if !bytes.Equal(bytes.TrimSpace(value.Observation), []byte("null")) || value.ErrorCode == nil || !queryReadErrorCode(*value.ErrorCode) {
			return value, invalid
		}
		if value.Result == "interrupted" && *value.ErrorCode != "INTERRUPTED" {
			return value, invalid
		}
	}
	return value, nil
}

// Keep the public values exact: no float64 decoding for IDs or numeric cells.
func queryReadObject(value any, keys ...string) (map[string]any, bool) {
	object, ok := value.(map[string]any)
	if !ok || len(object) != len(keys) {
		return nil, false
	}
	for _, key := range keys {
		if _, ok := object[key]; !ok {
			return nil, false
		}
	}
	return object, true
}
func queryReadInt(value any, minimum, maximum int64) (int64, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	n, err := strconv.ParseInt(string(number), 10, 64)
	return n, err == nil && n >= minimum && n <= maximum
}
func queryReadHash(value any) bool {
	s, ok := value.(string)
	return ok && warehouseHashPattern.MatchString(s)
}
func queryReadUUID(value any) bool {
	s, ok := value.(string)
	return ok && warehouseUUIDPattern.MatchString(s)
}
func queryReadSnapshot(value any, nullable bool) bool {
	if value == nil {
		return nullable
	}
	s, ok := value.(string)
	if !ok || len(s) == 0 || s[0] == '0' || !queryReadInteger.MatchString(s) {
		return false
	}
	n, err := strconv.ParseInt(s, 10, 64)
	return err == nil && n > 0
}
func queryReadType(kind string) (int, int, bool) {
	if dataOneOf(kind, "integer", "bigint", "boolean", "varchar", "real", "double", "date") {
		return 0, 0, true
	}
	parts := queryReadDecimal.FindStringSubmatch(kind)
	if parts == nil {
		return 0, 0, false
	}
	p, _ := strconv.Atoi(parts[1])
	s, _ := strconv.Atoi(parts[2])
	return p, s, p <= 38 && s <= p
}
func queryReadColumns(value any, maximum int) ([]map[string]any, bool) {
	items, ok := value.([]any)
	if !ok || len(items) < 1 || len(items) > maximum {
		return nil, false
	}
	result := make([]map[string]any, 0, len(items))
	seen := map[string]bool{}
	for _, item := range items {
		column, ok := queryReadObject(item, "name", "type", "nullable")
		if !ok {
			return nil, false
		}
		name, n := column["name"].(string)
		kind, k := column["type"].(string)
		_, nullable := column["nullable"].(bool)
		_, _, supported := queryReadType(kind)
		if !n || !queryReadIdentifier.MatchString(name) || seen[name] || !k || !supported || !nullable {
			return nil, false
		}
		seen[name] = true
		result = append(result, column)
	}
	return result, true
}

func queryReadObservation(raw []byte, request queryReadRequest) bool {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var decoded any
	if decoder.Decode(&decoded) != nil {
		return false
	}
	switch request.Command {
	case "tables":
		object, ok := queryReadObject(decoded, "schema_version", "catalog", "schema", "tables", "complete")
		if !ok {
			return false
		}
		_, version := queryReadInt(object["schema_version"], 1, 1)
		names, list := object["tables"].([]any)
		complete, flag := object["complete"].(bool)
		if !version || object["catalog"] != "iceberg" || object["schema"] != request.Schema || !list || len(names) > 100 || !flag || (!complete && len(names) != 100) {
			return false
		}
		previous := ""
		for _, name := range names {
			s, ok := name.(string)
			if !ok || !queryReadTable(s) || !strings.HasPrefix(s, request.Schema+".") || s <= previous {
				return false
			}
			previous = s
		}
		return true
	case "describe":
		object, ok := queryReadObject(decoded, "schema_version", "catalog", "schema", "table", "table_uuid", "format_version", "schema_id", "schema_sha256", "spec_sha256", "refs_sha256", "current_snapshot_id", "metadata_sha256", "metadata_location_sha256", "table_location_sha256", "columns")
		if !ok {
			return false
		}
		parts := strings.Split(request.Table, ".")
		_, version := queryReadInt(object["schema_version"], 1, 1)
		_, format := queryReadInt(object["format_version"], 2, 2)
		_, id := queryReadInt(object["schema_id"], 0, math.MaxInt32)
		_, columns := queryReadColumns(object["columns"], 32)
		if !version || !format || !id || !columns || object["catalog"] != "iceberg" || object["schema"] != parts[0] || object["table"] != parts[1] || !queryReadUUID(object["table_uuid"]) || !queryReadSnapshot(object["current_snapshot_id"], true) {
			return false
		}
		for _, key := range []string{"schema_sha256", "spec_sha256", "refs_sha256", "metadata_sha256", "metadata_location_sha256", "table_location_sha256"} {
			if !queryReadHash(object[key]) {
				return false
			}
		}
		return true
	case "select":
		return queryReadRows(decoded, request)
	}
	return false
}

func queryReadRows(decoded any, request queryReadRequest) bool {
	object, ok := queryReadObject(decoded, "schema_version", "table", "table_uuid", "schema_sha256", "snapshot_id", "columns", "rows", "row_count", "fetched_row_count", "truncated", "requested_limit", "ordering", "rows_sha256")
	if !ok {
		return false
	}
	_, version := queryReadInt(object["schema_version"], 1, 1)
	_, limit := queryReadInt(object["requested_limit"], int64(request.Limit), int64(request.Limit))
	count, validCount := queryReadInt(object["row_count"], 0, int64(request.Limit))
	fetched, validFetched := queryReadInt(object["fetched_row_count"], 0, int64(request.Limit+1))
	truncated, flag := object["truncated"].(bool)
	columns, validColumns := queryReadColumns(object["columns"], 16)
	rows, validRows := object["rows"].([]any)
	if !version || !limit || !validCount || !validFetched || !flag || !validColumns || !validRows || int64(len(rows)) != count || len(columns) != len(request.Columns) || object["table"] != "iceberg."+request.Table || object["ordering"] != "unspecified" || !queryReadUUID(object["table_uuid"]) || !queryReadHash(object["schema_sha256"]) || !queryReadSnapshot(object["snapshot_id"], false) || truncated != (fetched > int64(request.Limit)) || (fetched != count && fetched != int64(request.Limit+1)) || (truncated && count != int64(request.Limit)) {
		return false
	}
	for i, column := range columns {
		if column["name"] != request.Columns[i] {
			return false
		}
	}
	for _, row := range rows {
		cells, ok := row.([]any)
		if !ok || len(cells) != len(columns) {
			return false
		}
		for i, cell := range cells {
			if !queryReadCell(cell, columns[i]) {
				return false
			}
		}
	}
	encoded := queryReadCanonical(rows)
	sum := sha256.Sum256(encoded)
	return object["rows_sha256"] == hex.EncodeToString(sum[:])
}

func queryReadCell(value any, column map[string]any) bool {
	cell, ok := queryReadObject(value, "type", "value")
	if !ok || cell["type"] != column["type"] {
		return false
	}
	value = cell["value"]
	if value == nil {
		return column["nullable"] == true
	}
	kind := column["type"].(string)
	if kind == "boolean" {
		_, ok := value.(bool)
		return ok
	}
	s, ok := value.(string)
	if !ok {
		return false
	}
	switch kind {
	case "varchar":
		return len(s) <= 4096
	case "date":
		d, err := time.Parse("2006-01-02", s)
		return err == nil && d.Year() >= 1 && d.Format("2006-01-02") == s
	case "integer", "bigint":
		bits := 64
		if kind == "integer" {
			bits = 32
		}
		n, err := strconv.ParseInt(s, 10, bits)
		return err == nil && strconv.FormatInt(n, 10) == s
	case "real", "double":
		if len(s) > 32 || strings.TrimSpace(s) != s {
			return false
		}
		f, err := strconv.ParseFloat(s, 64)
		return err == nil && !math.IsInf(f, 0) && !math.IsNaN(f) && (kind != "real" || math.Abs(f) <= 3.4028234663852886e38) && queryReadFloatText(f) == s
	default:
		precision, scale, supported := queryReadType(kind)
		if !supported || !queryReadDecimalValue.MatchString(s) {
			return false
		}
		parts := strings.Split(strings.TrimPrefix(s, "-"), ".")
		if (scale == 0 && len(parts) != 1) || (scale > 0 && (len(parts) != 2 || len(parts[1]) != scale)) {
			return false
		}
		digits := strings.TrimLeft(strings.Join(parts, ""), "0")
		return len(digits) <= precision && !(strings.HasPrefix(s, "-") && digits == "")
	}
}

// Python's canonical projection is sorted JSON with ensure_ascii=True. Values
// here are only strings, booleans and null; exact numeric text is never parsed.
func queryReadCanonical(value any) []byte {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(value)
	raw := bytes.TrimSuffix(buffer.Bytes(), []byte{'\n'})
	var result strings.Builder
	for _, r := range string(raw) {
		if r < 127 {
			result.WriteRune(r)
		} else if r <= 0xffff {
			fmt.Fprintf(&result, `\u%04x`, r)
		} else {
			a, b := utf16.EncodeRune(r)
			fmt.Fprintf(&result, `\u%04x\u%04x`, a, b)
		}
	}
	return []byte(result.String())
}

// Python repr uses fixed notation for decimal exponents -4 through 15.
func queryReadFloatText(value float64) string {
	scientific := strconv.FormatFloat(value, 'e', -1, 64)
	parts := strings.Split(scientific, "e")
	exponent, err := strconv.Atoi(parts[len(parts)-1])
	if err == nil && exponent >= -4 && exponent < 16 {
		fixed := strconv.FormatFloat(value, 'f', -1, 64)
		if !strings.Contains(fixed, ".") {
			fixed += ".0"
		}
		return fixed
	}
	return scientific
}

// Reader rows need bounded arrays; the older warehouse object-only protocol
// remains unchanged. Duplicate keys are rejected at every nesting level.
func uniqueQueryReadJSON(raw []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	invalid := errors.New("query reader JSON is invalid")
	var consume func(int) error
	consume = func(depth int) error {
		if depth > 6 {
			return invalid
		}
		token, err := decoder.Token()
		if err != nil {
			return invalid
		}
		delimiter, container := token.(json.Delim)
		if !container {
			return nil
		}
		if delimiter != '{' && delimiter != '[' {
			return invalid
		}
		seen := map[string]bool{}
		for decoder.More() {
			if delimiter == '{' {
				key, err := decoder.Token()
				if err != nil {
					return invalid
				}
				name, ok := key.(string)
				if !ok || seen[name] {
					return invalid
				}
				seen[name] = true
			}
			if err := consume(depth + 1); err != nil {
				return err
			}
		}
		end, err := decoder.Token()
		expected := json.Delim(']')
		if delimiter == '{' {
			expected = '}'
		}
		if err != nil || end != expected {
			return invalid
		}
		return nil
	}
	if err := consume(0); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return invalid
	}
	return nil
}

func writeQueryReadLine(output io.Writer, line []byte) error {
	frame := append(append([]byte(nil), line...), '\n')
	n, err := output.Write(frame)
	if err == nil && n != len(frame) {
		return io.ErrShortWrite
	}
	return err
}

func queryReadErrorCode(value string) bool {
	for _, code := range strings.Fields(queryReadPublicCodes) {
		if value == code {
			return true
		}
	}
	return false
}
