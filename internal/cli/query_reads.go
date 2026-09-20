package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/signal"
	"reflect"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const queryReadsCase = "saved-reader-list-v1"

type queryReadsRequest struct {
	Command string  `json:"command"`
	Token   *string `json:"token"`
}

type queryReadsReport struct {
	SchemaVersion  int                   `json:"schema_version"`
	Command        string                `json:"command"`
	CommandResult  string                `json:"command_result"`
	RequestedToken *string               `json:"requested_token"`
	Inventory      []queryReadsInventory `json:"inventory"`
	Entries        []queryReadsEntry     `json:"entries"`
	Limitations    []string              `json:"limitations"`
}

type queryReadsInventory struct {
	ReaderVersion       int     `json:"reader_version"`
	Root                string  `json:"root"`
	Complete            bool    `json:"complete"`
	InspectedEntries    int     `json:"inspected_entries"`
	UnrecognizedEntries int     `json:"unrecognized_entries"`
	ErrorCode           *string `json:"error_code"`
}

type queryReadsEntry struct {
	ReaderVersion     int     `json:"reader_version"`
	Token             string  `json:"token"`
	Action            *string `json:"action"`
	Table             *string `json:"table"`
	SnapshotID        *string `json:"snapshot_id"`
	CurrentSnapshotID *string `json:"current_snapshot_id"`
	RecordedResult    *string `json:"recorded_result"`
	CleanupVerified   *bool   `json:"cleanup_verified"`
	InspectionState   string  `json:"inspection_state"`
	ErrorCode         *string `json:"error_code"`
	StartedAt         *string `json:"started_at"`
	Evidence          string  `json:"evidence"`
}

var queryReadsLimitations = []string{
	"Saved Reader metadata only; no query, row, SQL or model execution is repeated.",
	"Recorded results and cleanup are producer claims checked for local consistency, not current server or table state.",
	"Rows, counts, SQL, predicate literals and columns are omitted.",
}

var queryReadsFolders = []string{"query-read", "query-snapshots", "query-filter", "query-count"}

func newQueryReadsCommand(child dataChildRunner, root, profile *string) *cobra.Command {
	var token string
	command := &cobra.Command{Use: "reads", Short: "List saved Reader metadata without repeating queries", Args: func(_ *cobra.Command, args []string) error {
		if len(args) != 0 {
			return errors.New("query reads does not accept positional arguments")
		}
		return nil
	}, RunE: func(cmd *cobra.Command, _ []string) error {
		if *profile != localExperimentProfile {
			return errors.New("query reads requires --profile local-experiment")
		}
		input := queryReadsRequest{Command: "reads"}
		if cmd.Flags().Changed("token") {
			if !warehouseSessionPattern.MatchString(token) {
				return errors.New("query reads token must be 32 lowercase hexadecimal characters")
			}
			input.Token = &token
		}
		checkout, err := resolveQueryReadsRoot(*root)
		if err != nil {
			return err
		}
		raw, err := json.Marshal(input)
		if err != nil {
			return errors.New("query reads input is invalid")
		}
		request := dataChildRequest{Root: checkout, Action: "reads", Case: queryReadsCase, Request: raw}
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		return runQueryReadsCLI(ctx, child, request, cmd.OutOrStdout())
	}}
	command.Flags().StringVar(&token, "token", "", "inspect one saved Reader token across versions; omit to list all")
	command.SetFlagErrorFunc(func(_ *cobra.Command, _ error) error { return errors.New("query reads flags are invalid") })
	return command
}

func decodeQueryReadsRequest(request dataChildRequest) (queryReadsRequest, error) {
	var input queryReadsRequest
	invalid := errors.New("invalid fixed query reads invocation")
	if request.Action != "reads" || request.Case != queryReadsCase || request.Token != "" || len(request.Request) == 0 || len(request.Request) > 1024 || !utf8.Valid(request.Request) || uniqueQueryReadJSON(request.Request) != nil {
		return input, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(request.Request, &fields) != nil || !warehouseKeys(fields, "command", "token") || json.Unmarshal(request.Request, &input) != nil || input.Command != "reads" || input.Token != nil && !warehouseSessionPattern.MatchString(*input.Token) {
		return input, invalid
	}
	return input, nil
}

func runQueryReadsCLI(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if _, err := decodeQueryReadsRequest(request); err != nil {
		return err
	}
	var final []byte
	want := -1
	result, err := child.Run(ctx, request, func(line []byte) error {
		if final != nil {
			return errors.New("query reads returned multiple results")
		}
		code, e := decodeQueryReadsReport(line, request)
		if e != nil {
			return e
		}
		final, want = bytes.Clone(line), code
		return nil
	})
	if result.Interrupted && errors.Is(err, errDataInterrupted) && (result.ExitCode == 130 || result.ExitCode == 143) {
		return &dataExitError{code: result.ExitCode, message: "query reads interrupted; inspection was not confirmed"}
	}
	if err != nil || !result.Started || result.Unconfirmed || final == nil || result.ExitCode != want {
		return errors.New("query reads inspection was not confirmed; saved reads were not repeated")
	}
	if writeQueryReadLine(output, final) != nil {
		return errors.New("query reads output could not be delivered")
	}
	if want == 1 {
		return &dataExitError{code: 1, message: "query reads is incomplete; inspect the reported evidence states"}
	}
	return nil
}

func queryReadsErrorCode(code *string) bool {
	return code == nil || dataOneOf(*code, "READS_REQUEST_INVALID", "READS_ROOT_INVALID", "READS_ROOT_CHANGED", "READS_ENTRY_LIMIT", "READS_READ_LIMIT", "READS_PUBLIC_LIMIT", "READS_UNRECOGNIZED_ENTRIES", "READS_TOKEN_MISSING", "READS_RECEIPT_MISSING", "READS_RECEIPT_INVALID", "READS_RECEIPT_CHANGED", "READS_LOCK_MISSING", "READS_UNFINISHED")
}

func decodeQueryReadsReport(line []byte, request dataChildRequest) (int, error) {
	invalid := errors.New("query reads failed the fixed output contract")
	input, err := decodeQueryReadsRequest(request)
	if err != nil || len(line)+1 > queryReadsResponseLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
		return 1, invalid
	}
	var report queryReadsReport
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&report) != nil {
		return 1, invalid
	}
	// Typed round-trip equality also rejects missing fields and scalar nulls.
	encoded, err := json.Marshal(report)
	var before, after any
	if err != nil || json.Unmarshal(line, &before) != nil || json.Unmarshal(encoded, &after) != nil || !reflect.DeepEqual(before, after) {
		return 1, invalid
	}
	if report.SchemaVersion != 1 || report.Command != "query reads" || !reflect.DeepEqual(report.RequestedToken, input.Token) || !reflect.DeepEqual(report.Limitations, queryReadsLimitations) || len(report.Inventory) != 4 || report.Entries == nil || len(report.Entries) > 128 {
		return 1, invalid
	}
	complete := true
	counts := [4]int{}
	for i, inv := range report.Inventory {
		if inv.ReaderVersion != i+1 || !dataOneOf(inv.Root, "present", "absent", "not-observed") || inv.InspectedEntries < 0 || inv.InspectedEntries > 128 || inv.UnrecognizedEntries < 0 || inv.UnrecognizedEntries > 1024 || !queryReadsErrorCode(inv.ErrorCode) {
			return 1, invalid
		}
		if !inv.Complete || inv.ErrorCode != nil {
			complete = false
		}
		if inv.Complete && (inv.ErrorCode != nil || inv.Root == "not-observed" || inv.UnrecognizedEntries != 0) ||
			!inv.Complete && inv.ErrorCode == nil || inv.Root != "present" && (inv.InspectedEntries != 0 || inv.UnrecognizedEntries != 0) {
			return 1, invalid
		}
	}
	lastVersion, lastToken := 0, ""
	for _, entry := range report.Entries {
		if !validQueryReadsEntry(entry) || entry.ReaderVersion < lastVersion || entry.ReaderVersion == lastVersion && entry.Token <= lastToken || input.Token != nil && entry.Token != *input.Token {
			return 1, invalid
		}
		lastVersion, lastToken = entry.ReaderVersion, entry.Token
		counts[entry.ReaderVersion-1]++
		if !dataOneOf(entry.InspectionState, "saved", "active") {
			complete = false
		}
	}
	for i, inv := range report.Inventory {
		if inv.InspectedEntries != counts[i] {
			return 1, invalid
		}
	}
	if input.Token != nil && len(report.Entries) == 0 {
		complete = false
	}
	if complete && report.CommandResult == "complete" {
		return 0, nil
	}
	if !complete && report.CommandResult == "incomplete" {
		return 1, nil
	}
	return 1, invalid
}

func validQueryReadsEntry(row queryReadsEntry) bool {
	if row.ReaderVersion < 1 || row.ReaderVersion > 4 || !warehouseSessionPattern.MatchString(row.Token) || !dataOneOf(row.InspectionState, "saved", "active", "incomplete", "invalid", "missing") || !queryReadsErrorCode(row.ErrorCode) || !qdrStamp(row.StartedAt) {
		return false
	}
	if row.Evidence != "tmp/local-experiment/"+queryReadsFolders[row.ReaderVersion-1]+"/"+row.Token+"/summary.json" {
		return false
	}
	if row.Table != nil && (!strings.HasPrefix(*row.Table, "iceberg.") || !queryReadTable(strings.TrimPrefix(*row.Table, "iceberg."))) || row.SnapshotID != nil && !queryReadSnapshot(*row.SnapshotID, false) || row.CurrentSnapshotID != nil && !queryReadSnapshot(*row.CurrentSnapshotID, false) {
		return false
	}
	if row.InspectionState == "active" || row.InspectionState == "invalid" || row.InspectionState == "missing" || row.InspectionState == "incomplete" && row.Action == nil {
		return row.Action == nil && row.Table == nil && row.SnapshotID == nil && row.CurrentSnapshotID == nil && row.RecordedResult == nil && row.CleanupVerified == nil && row.StartedAt == nil && (row.ErrorCode == nil) == (row.InspectionState == "active")
	}
	if row.Action == nil || row.RecordedResult == nil || row.CleanupVerified == nil || row.StartedAt == nil {
		return false
	}
	actions := [][]string{{"tables", "describe", "select"}, {"snapshots", "select"}, {"select"}, {"count"}}
	if !dataOneOf(*row.Action, actions[row.ReaderVersion-1]...) || (*row.Action == "tables") != (row.Table == nil) {
		return false
	}
	if row.InspectionState == "incomplete" {
		return dataOneOf(*row.RecordedResult, "running", "passed", "failed", "interrupted") && row.ErrorCode != nil && *row.ErrorCode == "READS_UNFINISHED" && row.SnapshotID == nil && row.CurrentSnapshotID == nil
	}
	if !dataOneOf(*row.RecordedResult, "passed", "failed", "interrupted") || row.ErrorCode != nil {
		return false
	}
	if *row.RecordedResult == "passed" && !*row.CleanupVerified {
		return false
	}
	if *row.RecordedResult != "passed" || *row.Action == "tables" {
		return row.SnapshotID == nil && row.CurrentSnapshotID == nil
	}
	if dataOneOf(*row.Action, "describe", "snapshots") {
		return row.SnapshotID == nil
	}
	return row.SnapshotID != nil && row.CurrentSnapshotID != nil && (row.ReaderVersion != 1 || *row.CurrentSnapshotID == *row.SnapshotID)
}
