package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

func readsRoot(t *testing.T) string {
	t.Helper()
	root := queryReadTestRoot(t)
	path := filepath.Join(root, queryReadsScript)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func readsRequest(token *string) dataChildRequest {
	raw, _ := json.Marshal(queryReadsRequest{Command: "reads", Token: token})
	return dataChildRequest{Action: "reads", Case: queryReadsCase, Request: raw}
}

func readsString(value string) *string { return &value }
func readsBool(value bool) *bool       { return &value }

func readsEntry(version int, state string) queryReadsEntry {
	row := queryReadsEntry{ReaderVersion: version, Token: testWarehouseSession, InspectionState: state,
		Evidence: "tmp/local-experiment/" + queryReadsFolders[version-1] + "/" + testWarehouseSession + "/summary.json"}
	if state == "invalid" || state == "missing" {
		row.ErrorCode = readsString("READS_RECEIPT_INVALID")
		return row
	}
	if state == "active" {
		return row
	}
	row.Action, row.Table = readsString("select"), readsString("iceberg.smoke.fixture_v1")
	if version == 4 {
		row.Action = readsString("count")
	}
	row.StartedAt = readsString("2026-09-18T00:00:00.123456Z")
	row.RecordedResult, row.CleanupVerified = readsString("passed"), readsBool(true)
	row.SnapshotID, row.CurrentSnapshotID = readsString("9223372036854775807"), readsString("9223372036854775807")
	if state == "incomplete" {
		row.RecordedResult, row.CleanupVerified = readsString("running"), readsBool(false)
		row.SnapshotID, row.CurrentSnapshotID = nil, nil
		row.ErrorCode = readsString("READS_UNFINISHED")
	}
	return row
}

func readsReport(token *string, entries ...queryReadsEntry) queryReadsReport {
	r := queryReadsReport{SchemaVersion: 1, Command: "query reads", CommandResult: "complete", RequestedToken: token,
		Entries: append([]queryReadsEntry{}, entries...), Limitations: queryReadsLimitations}
	for version := 1; version <= 4; version++ {
		inv := queryReadsInventory{ReaderVersion: version, Root: "absent", Complete: true}
		for _, row := range entries {
			if row.ReaderVersion == version {
				inv.Root = "present"
				inv.InspectedEntries++
				if !dataOneOf(row.InspectionState, "saved", "active") {
					r.CommandResult = "incomplete"
				}
			}
		}
		if token != nil && len(entries) == 0 {
			inv.Complete, inv.ErrorCode, r.CommandResult = false, readsString("READS_TOKEN_MISSING"), "incomplete"
		}
		r.Inventory = append(r.Inventory, inv)
	}
	return r
}

func readsRaw(value any) []byte { b, _ := json.Marshal(value); return b }

func readsExecute(t *testing.T, child dataChildRunner, args ...string) (string, error) {
	t.Helper()
	root, profile := "", localExperimentProfile
	p := &cobra.Command{Use: "query", SilenceErrors: true, SilenceUsage: true}
	p.PersistentFlags().StringVar(&root, "repo-root", "", "")
	p.PersistentFlags().StringVar(&profile, "profile", profile, "")
	p.AddCommand(newQueryReadsCommand(child, &root, &profile))
	var out bytes.Buffer
	p.SetOut(&out)
	p.SetErr(io.Discard)
	p.SetArgs(args)
	err := p.Execute()
	return out.String(), err
}

func TestQueryReadsCommandFixedRequestAndRegistration(t *testing.T) {
	root := readsRoot(t)
	for _, single := range []bool{false, true} {
		calls := 0
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			calls++
			input, err := decodeQueryReadsRequest(r)
			if err != nil || r.Root != root || (input.Token != nil) != single {
				t.Fatal("request binding differs")
			}
			if err := emit(readsRaw(readsReport(input.Token, readsEntry(1, "saved")))); err != nil {
				return dataChildResult{Started: true}, err
			}
			return dataChildResult{Started: true, ExitCode: 0}, nil
		})
		args := []string{"reads", "--repo-root", root}
		if single {
			args = append(args, "--token", testWarehouseSession)
		}
		out, err := readsExecute(t, child, args...)
		if err != nil || calls != 1 || strings.Count(out, "\n") != 1 || strings.Contains(out, "allocated") {
			t.Fatalf("%v %s", err, out)
		}
	}
	p := newQueryCommand(filterNever(t))
	c, _, err := p.Find([]string{"reads"})
	if err != nil || c.Name() != "reads" {
		t.Fatal("reads not registered")
	}
}

func TestQueryReadsInvalidArgumentsRejectBeforeChildAndHideRaw(t *testing.T) {
	root := readsRoot(t)
	for _, args := range [][]string{{"reads", "PRIVATE"}, {"reads", "--token", "PRIVATE"},
		{"reads", "--token", ""}, {"reads", "--token", strings.Repeat("A", 32)},
		{"reads", "--profile", "PRIVATE"}, {"reads", "--table", "PRIVATE"}, {"reads", "--root", "PRIVATE"}} {
		args = append(args, "--repo-root", root)
		out, err := readsExecute(t, filterNever(t), args...)
		if err == nil || out != "" || strings.Contains(err.Error(), "PRIVATE") {
			t.Fatalf("bad argument accepted/exposed: %v", err)
		}
	}
}

func TestQueryReadsRequestExactPrivateStdin(t *testing.T) {
	r := readsRequest(nil)
	for _, raw := range []string{`{"command":"reads"}`, `{"command":null,"token":null}`, `{"command":"reads","token":0}`,
		`{"command":"reads","token":null,"sql":"PRIVATE"}`, `{"command":"reads","command":"reads","token":null}`, `[]`, `null`} {
		r.Request = []byte(raw)
		if _, err := decodeQueryReadsRequest(r); err == nil {
			t.Fatal("invalid request accepted")
		}
	}
	r = readsRequest(readsString(testWarehouseSession))
	r.Root = readsRoot(t)
	t.Setenv("PYTHONPATH", "PRIVATE")
	t.Setenv("HTTP_PROXY", "PRIVATE")
	t.Setenv("MTPLX_API_KEY", "PRIVATE")
	cmd, err := queryReadsCommand(r)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join(r.Root, dataPython), "-I", "-B", filepath.Join(r.Root, queryReadsScript), "reads", "--request-stdin"}
	raw, _ := io.ReadAll(cmd.Stdin)
	if !reflect.DeepEqual(cmd.Args, want) || !bytes.Equal(raw, r.Request) || cmd.Dir != r.Root || cmd.SysProcAttr == nil || !cmd.SysProcAttr.Setpgid {
		t.Fatal("child boundary differs")
	}
	for _, value := range cmd.Env {
		if strings.Contains(value, "PRIVATE") {
			t.Fatal("ambient configuration inherited")
		}
	}
	if strings.Contains(strings.Join(cmd.Args, " "), testWarehouseSession) {
		t.Fatal("private request in argv")
	}
}

func TestQueryReadsCompleteIncludesSavedFailuresAndActive(t *testing.T) {
	for _, outcome := range []string{"passed", "failed", "interrupted"} {
		row := readsEntry(1, "saved")
		row.RecordedResult = readsString(outcome)
		if outcome != "passed" {
			row.CleanupVerified = readsBool(false)
			row.SnapshotID, row.CurrentSnapshotID = nil, nil
		}
		if code, err := decodeQueryReadsReport(readsRaw(readsReport(nil, row, readsEntry(2, "active"))), readsRequest(nil)); err != nil || code != 0 {
			t.Fatalf("valid outcome refused: %v", err)
		}
	}
	for _, r := range []queryReadsReport{readsReport(nil), readsReport(readsString(testWarehouseSession)),
		readsReport(nil, readsEntry(1, "incomplete")), readsReport(nil, readsEntry(2, "invalid")), readsReport(nil, readsEntry(3, "missing"))} {
		code, err := decodeQueryReadsReport(readsRaw(r), readsRequest(r.RequestedToken))
		want := 0
		if r.CommandResult == "incomplete" {
			want = 1
		}
		if err != nil || code != want {
			t.Fatalf("state refused: %v", err)
		}
	}
}

func TestQueryReadsRejectsUnknownFieldsTypesAndDisclosure(t *testing.T) {
	base := readsRaw(readsReport(nil, readsEntry(1, "saved")))
	mutations := map[string]func(map[string]any){
		"top-extra":        func(v map[string]any) { v["rows"] = []any{} },
		"top-missing":      func(v map[string]any) { delete(v, "requested_token") },
		"entries-null":     func(v map[string]any) { v["entries"] = nil },
		"version-bool":     func(v map[string]any) { v["schema_version"] = true },
		"changed-limit":    func(v map[string]any) { v["limitations"].([]any)[0] = "PRIVATE" },
		"entry-extra":      func(v map[string]any) { v["entries"].([]any)[0].(map[string]any)["count"] = "1" },
		"entry-type":       func(v map[string]any) { v["entries"].([]any)[0] = []any{} },
		"entry-null":       func(v map[string]any) { v["entries"].([]any)[0] = nil },
		"inventory-null":   func(v map[string]any) { v["inventory"].([]any)[0] = nil },
		"inventory-count":  func(v map[string]any) { v["inventory"].([]any)[0].(map[string]any)["inspected_entries"] = 0 },
		"inventory-hidden": func(v map[string]any) { v["inventory"].([]any)[0].(map[string]any)["unrecognized_entries"] = 1 },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			var v map[string]any
			_ = json.Unmarshal(base, &v)
			mutate(v)
			if _, err := decodeQueryReadsReport(readsRaw(v), readsRequest(nil)); err == nil {
				t.Fatal("invalid report accepted")
			}
		})
	}
	for _, raw := range [][]byte{[]byte(`{"schema_version":1,"schema_version":1}`), []byte("\xff"), bytes.Repeat([]byte(" "), queryReadsResponseLimit+1)} {
		if _, err := decodeQueryReadsReport(raw, readsRequest(nil)); err == nil {
			t.Fatal("invalid bytes accepted")
		}
	}
}

func TestQueryReadsStateSnapshotAndIdentityCoherence(t *testing.T) {
	for name, mutate := range map[string]func(*queryReadsEntry){
		"unsafe-action":        func(r *queryReadsEntry) { r.Action = readsString("PRIVATE") },
		"unsafe-table":         func(r *queryReadsEntry) { r.Table = readsString("iceberg.smoke.x --sql PRIVATE") },
		"wrong-version":        func(r *queryReadsEntry) { r.ReaderVersion = 0 },
		"wrong-evidence":       func(r *queryReadsEntry) { r.Evidence = "/PRIVATE" },
		"source-error":         func(r *queryReadsEntry) { r.ErrorCode = readsString("READ_FAILED") },
		"invalid-stamp":        func(r *queryReadsEntry) { r.StartedAt = readsString("2026-09-18T00:00:00Z") },
		"snapshot-zero":        func(r *queryReadsEntry) { r.SnapshotID = readsString("0") },
		"snapshot-overflow":    func(r *queryReadsEntry) { r.SnapshotID = readsString("9223372036854775808") },
		"snapshot-leadingzero": func(r *queryReadsEntry) { r.SnapshotID = readsString("01") },
		"passed-no-cleanup":    func(r *queryReadsEntry) { r.CleanupVerified = readsBool(false) },
		"passed-no-snapshot":   func(r *queryReadsEntry) { r.SnapshotID = nil },
		"v1-head-differs":      func(r *queryReadsEntry) { r.CurrentSnapshotID = readsString("1") },
		"failed-has-snapshot":  func(r *queryReadsEntry) { r.RecordedResult = readsString("failed") },
		"active-has-payload":   func(r *queryReadsEntry) { r.InspectionState = "active" },
	} {
		t.Run(name, func(t *testing.T) {
			row := readsEntry(1, "saved")
			mutate(&row)
			if _, err := decodeQueryReadsReport(readsRaw(readsReport(nil, row)), readsRequest(nil)); err == nil {
				t.Fatal("state contradiction accepted")
			}
		})
	}
	for _, version := range []int{2, 3, 4} {
		row := readsEntry(version, "saved")
		row.SnapshotID = readsString("1")
		row.CurrentSnapshotID = nil
		if _, err := decodeQueryReadsReport(readsRaw(readsReport(nil, row)), readsRequest(nil)); err == nil {
			t.Fatal("successful historical data read missing current head accepted")
		}
		row.CurrentSnapshotID = readsString("2")
		if _, err := decodeQueryReadsReport(readsRaw(readsReport(nil, row)), readsRequest(nil)); err != nil {
			t.Fatal("historical selected and current IDs refused")
		}
	}
	for _, version := range []int{1, 2} {
		row := readsEntry(version, "saved")
		row.Action = readsString("describe")
		if version == 2 {
			row.Action = readsString("snapshots")
		}
		row.SnapshotID, row.CurrentSnapshotID = nil, nil
		if _, err := decodeQueryReadsReport(readsRaw(readsReport(nil, row)), readsRequest(nil)); err != nil {
			t.Fatal("metadata-only empty head refused")
		}
	}
	r := readsReport(nil, readsEntry(1, "saved"), readsEntry(1, "saved"))
	if _, err := decodeQueryReadsReport(readsRaw(r), readsRequest(nil)); err == nil {
		t.Fatal("duplicate token accepted")
	}
	r = readsReport(nil, readsEntry(2, "saved"), readsEntry(1, "saved"))
	if _, err := decodeQueryReadsReport(readsRaw(r), readsRequest(nil)); err == nil {
		t.Fatal("unsorted entries accepted")
	}
}

func TestQueryReadsPublishesOnlyAfterMatchingChildExit(t *testing.T) {
	for _, mode := range []string{"complete", "incomplete", "mismatch", "extra", "error", "unconfirmed", "not-started", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			request := readsRequest(nil)
			report := readsReport(nil, readsEntry(1, "saved"))
			code := 0
			if mode == "incomplete" {
				report = readsReport(nil, readsEntry(1, "invalid"))
				code = 1
			}
			var out bytes.Buffer
			child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				if err := emit(readsRaw(report)); err != nil {
					return dataChildResult{Started: true}, err
				}
				if out.Len() != 0 {
					t.Fatal("final exposed before child exit")
				}
				result := dataChildResult{Started: true, ExitCode: code}
				switch mode {
				case "mismatch":
					result.ExitCode = 1
				case "extra":
					return result, emit(readsRaw(report))
				case "error":
					return result, errors.New("PRIVATE")
				case "unconfirmed":
					result.Unconfirmed = true
				case "not-started":
					result.Started = false
				case "cancel":
					result.ExitCode = 130
					result.Interrupted = true
					return result, errDataInterrupted
				}
				return result, nil
			})
			err := runQueryReadsCLI(context.Background(), child, request, &out)
			if mode == "complete" {
				if err != nil || out.Len() == 0 {
					t.Fatal("complete lost")
				}
			} else if mode == "incomplete" {
				var exit *dataExitError
				if !errors.As(err, &exit) || exit.code != 1 || out.Len() == 0 {
					t.Fatal("incomplete lost")
				}
			} else {
				if err == nil || out.Len() != 0 || strings.Contains(err.Error(), "PRIVATE") {
					t.Fatalf("unknown published: %v", err)
				}
			}
			if mode == "cancel" {
				var exit *dataExitError
				if !errors.As(err, &exit) || exit.code != 130 {
					t.Fatal("cancel code lost")
				}
			}
		})
	}
}

func TestQueryReadsIncompleteMetadataAndBoundedInventory(t *testing.T) {
	for _, outcome := range []string{"running", "passed", "failed", "interrupted"} {
		row := readsEntry(1, "incomplete")
		row.RecordedResult = readsString(outcome)
		if code, err := decodeQueryReadsReport(readsRaw(readsReport(nil, row)), readsRequest(nil)); err != nil || code != 1 {
			t.Fatal("unfinished timestamp metadata rejected")
		}
	}
	row := readsEntry(1, "active")
	row.InspectionState, row.ErrorCode = "incomplete", readsString("READS_READ_LIMIT")
	if code, err := decodeQueryReadsReport(readsRaw(readsReport(nil, row)), readsRequest(nil)); err != nil || code != 1 {
		t.Fatal("payload-free incomplete refused")
	}
	for _, size := range []int{128, 129} {
		entries := make([]queryReadsEntry, size)
		for i := range entries {
			entries[i] = readsEntry(1, "active")
			entries[i].Token = strings.Repeat("0", 29) + string("0123456789abcdef"[i/256]) + string("0123456789abcdef"[(i/16)%16]) + string("0123456789abcdef"[i%16])
			entries[i].Evidence = "tmp/local-experiment/query-read/" + entries[i].Token + "/summary.json"
		}
		_, err := decodeQueryReadsReport(readsRaw(readsReport(nil, entries...)), readsRequest(nil))
		if (err == nil) != (size == 128) {
			t.Fatal("global entry bound differs")
		}
	}
}

func TestQueryReadsPythonPublicContractRoundTrip(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	reports := []queryReadsReport{readsReport(nil), readsReport(readsString(testWarehouseSession))}
	for version := 1; version <= 4; version++ {
		reports = append(reports, readsReport(nil, readsEntry(version, "saved")))
	}
	for _, state := range []string{"active", "invalid", "missing", "incomplete"} {
		reports = append(reports, readsReport(nil, readsEntry(1, state)))
	}
	for _, outcome := range []string{"failed", "interrupted"} {
		row := readsEntry(1, "saved")
		row.RecordedResult = readsString(outcome)
		row.CleanupVerified = readsBool(false)
		row.SnapshotID, row.CurrentSnapshotID = nil, nil
		reports = append(reports, readsReport(nil, row))
	}
	row := readsEntry(1, "active")
	row.InspectionState, row.ErrorCode = "incomplete", readsString("READS_READ_LIMIT")
	reports = append(reports, readsReport(nil, row))
	row = readsEntry(1, "incomplete")
	row.RecordedResult = readsString("passed")
	reports = append(reports, readsReport(nil, row))
	r := readsReport(nil)
	for i := range r.Inventory {
		r.Inventory[i].Root = "not-observed"
		r.Inventory[i].Complete = false
		r.Inventory[i].ErrorCode = readsString("READS_REQUEST_INVALID")
	}
	r.CommandResult = "incomplete"
	reports = append(reports, r)
	r = readsReport(readsString(testWarehouseSession), readsEntry(1, "saved"), readsEntry(2, "active"), readsEntry(3, "saved"), readsEntry(4, "saved"))
	reports = append(reports, r)
	script := `import json,socket,sys
sys.path.insert(0,sys.argv[1])
from tools.local_experiment.query_reads import contract as C
def forbidden(*args,**kwargs):raise AssertionError('external access forbidden')
socket.create_connection=forbidden;socket.socket.connect=forbidden
values=json.load(sys.stdin)
print(json.dumps({'reports':[C.validate_public(v) for v in values],'codes':sorted(C.ERROR_CODES)}))`
	cmd := exec.Command(python, "-I", "-B", "-c", script, root)
	cmd.Env = []string{"PATH=/usr/bin:/bin"}
	cmd.Stdin = bytes.NewReader(readsRaw(reports))
	raw, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("Python public contract differs: %v %s", err, raw)
	}
	var decoded struct {
		Reports []json.RawMessage `json:"reports"`
		Codes   []string          `json:"codes"`
	}
	if json.Unmarshal(raw, &decoded) != nil || len(decoded.Reports) != len(reports) {
		t.Fatal("Python roundtrip invalid")
	}
	for i, report := range decoded.Reports {
		code, err := decodeQueryReadsReport(report, readsRequest(reports[i].RequestedToken))
		want := 0
		if reports[i].CommandResult == "incomplete" {
			want = 1
		}
		if err != nil || code != want {
			t.Fatalf("Python report %d refused: %v", i, err)
		}
	}
	if len(decoded.Codes) != 13 {
		t.Fatal("Python fixed codes changed")
	}
	for _, code := range decoded.Codes {
		if !queryReadsErrorCode(&code) {
			t.Fatal("Python fixed code refused")
		}
	}
	unknown := "PRIVATE"
	if queryReadsErrorCode(&unknown) {
		t.Fatal("unknown error accepted")
	}
}
