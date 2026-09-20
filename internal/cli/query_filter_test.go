package cli

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

func filterRoot(t *testing.T) string {
	t.Helper()
	root := snapshotRoot(t)
	path := filepath.Join(root, queryFilterScript)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}
func filterRequest(historical bool) dataChildRequest {
	value := queryFilterRequest{Command: "select", Table: "smoke.fixture_v1", Columns: []string{"id", "name"}, Limit: 2, Where: queryFilterWhere{Column: "id", Literal: "1"}}
	if historical {
		id := snapshotOld
		value.SnapshotID = &id
	}
	raw, _ := json.Marshal(value)
	return dataChildRequest{Action: "select", Case: queryFilterCase, Token: testWarehouseSession, Request: raw}
}
func filterObservation(request dataChildRequest) map[string]any {
	input, _ := decodeQueryFilterRequest(request.Request, "select")
	value := snapshotObservation("select")
	value["schema_version"] = 3
	if input.SnapshotID == nil {
		value["snapshot_id"] = snapshotHead
		value["selected_snapshot"] = snapshotRecord(snapshotHead, snapshotOld, "2", true, true)
	}
	value["where"] = map[string]any{"column": input.Where.Column, "type": "bigint", "operator": "eq"}
	rows := value["rows"].([]any)
	for _, row := range rows {
		row.([]any)[0].(map[string]any)["value"] = "1"
	}
	sum := sha256.Sum256(queryReadCanonical(rows))
	value["rows_sha256"] = hex.EncodeToString(sum[:])
	return value
}
func filterLines(request dataChildRequest, outcome string) [][]byte {
	a, _ := json.Marshal(map[string]any{"schema_version": 3, "event": "allocated", "command": "query select", "evidence_token": request.Token})
	v := map[string]any{"schema_version": 3, "event": "result", "command": "query select", "evidence_token": request.Token, "result": outcome, "observation": nil, "error_code": "FILTER_INVALID", "cleanup_verified": false, "evidence": "tmp/local-experiment/query-filter/" + request.Token + "/summary.json"}
	if outcome == "passed" {
		v["observation"] = filterObservation(request)
		v["error_code"] = nil
		v["cleanup_verified"] = true
	}
	if outcome == "interrupted" {
		v["error_code"] = "INTERRUPTED"
	}
	b, _ := json.Marshal(v)
	return [][]byte{a, b}
}
func filterExecute(t *testing.T, old, snap, filtered dataChildRunner, args ...string) (string, error) {
	t.Helper()
	root, profile := "", localExperimentProfile
	p := &cobra.Command{Use: "query", SilenceErrors: true, SilenceUsage: true}
	p.PersistentFlags().StringVar(&root, "repo-root", "", "")
	p.PersistentFlags().StringVar(&profile, "profile", profile, "")
	for _, action := range []string{"tables", "describe", "select"} {
		p.AddCommand(newQueryReadCommandWithProfiles(action, old, snap, filtered, &root, &profile))
	}
	var out bytes.Buffer
	p.SetOut(&out)
	p.SetErr(io.Discard)
	p.SetArgs(args)
	err := p.Execute()
	return out.String(), err
}
func filterNever(t *testing.T) dataChildRunner {
	t.Helper()
	return dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("unexpected child invocation")
		return dataChildResult{}, nil
	})
}
func filterSuccess(t *testing.T) dataChildRunner {
	t.Helper()
	return dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		if r.Case != queryFilterCase || r.Action != "select" {
			t.Fatal("filter route differs")
		}
		for _, line := range filterLines(r, "passed") {
			if err := emit(line); err != nil {
				return dataChildResult{Started: true}, err
			}
		}
		return dataChildResult{Started: true, ExitCode: 0, PID: 42}, nil
	})
}
func TestQueryFilterOnlyPairedFlagsUseNewEndpoint(t *testing.T) {
	root := filterRoot(t)
	never := filterNever(t)
	for _, historical := range []bool{false, true} {
		args := []string{"select", "--table", "smoke.fixture_v1", "--columns", "id,name", "--limit", "2", "--where-column", "id", "--equals", "1", "--repo-root", root}
		calls := 0
		child := dataChildFunc(func(ctx context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			calls++
			value, e := decodeQueryFilterRequest(r.Request, "select")
			if e != nil || (value.SnapshotID != nil) != historical {
				t.Fatal("nullable snapshot differs")
			}
			return filterSuccess(t).Run(ctx, r, emit)
		})
		if historical {
			args = append(args, "--snapshot-id", snapshotOld)
		}
		out, e := filterExecute(t, never, never, child, args...)
		if e != nil || calls != 1 || !strings.Contains(out, `"schema_version":3`) {
			t.Fatalf("%v %s", e, out)
		}
	}
}
func TestQueryFilterNoFlagsRetainOldBytesAndEnvironment(t *testing.T) {
	for _, historical := range []bool{false, true} {
		root := queryReadTestRoot(t)
		if historical {
			root = snapshotRoot(t)
		} // No filter script exists.
		never := filterNever(t)
		calls := 0
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			calls++
			var want []byte
			var lines [][]byte
			if historical {
				want, _ = json.Marshal(querySnapshotsRequest{Command: "select", Table: "smoke.fixture_v1", Columns: []string{"id", "name"}, Limit: 2, SnapshotID: snapshotOld})
				lines = snapshotLines(r, "passed")
				if r.Case != querySnapshotsCase {
					t.Fatal("old snapshot case changed")
				}
			} else {
				want, _ = json.Marshal(queryReadRequest{Command: "select", Table: "smoke.fixture_v1", Columns: []string{"id", "name"}, Limit: 2})
				lines = queryReadTestLines(r, "passed")
				if r.Case != "" {
					t.Fatal("old reader case changed")
				}
			}
			if !bytes.Equal(want, r.Request) {
				t.Fatal("old request bytes changed")
			}
			for _, line := range lines {
				if e := emit(line); e != nil {
					return dataChildResult{Started: true}, e
				}
			}
			return dataChildResult{Started: true, ExitCode: 0}, nil
		})
		args := []string{"select", "--table", "smoke.fixture_v1", "--columns", "id,name", "--limit", "2", "--repo-root", root}
		var old, snap dataChildRunner = child, never
		if historical {
			old, snap = never, child
			args = append(args, "--snapshot-id", snapshotOld)
		}
		if _, e := filterExecute(t, old, snap, never, args...); e != nil || calls != 1 {
			t.Fatal(e)
		}
	}
}
func TestQueryFilterInvalidFlagsNeverStartChild(t *testing.T) {
	never := filterNever(t)
	root := filterRoot(t)
	base := []string{"select", "--table", "smoke.fixture_v1", "--columns", "id", "--limit", "2", "--repo-root", root}
	variants := [][]string{{"--where-column", "id"}, {"--equals", "1"}, {"--where-column", "id", "--equals", ""}, {"--where-column", "", "--equals", "1"}, {"--where-column", "id;PRIVATE", "--equals", "1"}, {"--where-column", "id", "--equals", "PRIVATE' OR 1=1"}, {"--where-column", "id", "--equals", "1", "--snapshot-id", ""}}
	for _, flags := range variants {
		out, e := filterExecute(t, never, never, never, append(append([]string{}, base...), flags...)...)
		if e == nil || out != "" || strings.Contains(e.Error(), "PRIVATE") {
			t.Fatalf("bad input exposed or ran: %v", e)
		}
	}
	for _, action := range []string{"tables", "describe"} {
		out, e := filterExecute(t, never, never, never, action, "--where-column", "id", "--equals", "1", "--repo-root", root)
		if e == nil || out != "" {
			t.Fatal("non-select accepted filter")
		}
	}
}
func TestQueryFilterLiteralSyntaxAndTypedBounds(t *testing.T) {
	for _, s := range []string{"true", "false", "0", "-0", "-1", "1.2300", "2024-02-29", strings.Repeat("9", 128)} {
		if !queryFilterLiteral(s) {
			t.Fatalf("valid syntax %q", s)
		}
	}
	for _, s := range []string{"", "NULL", "True", "+1", "01", "1.", ".1", "1e2", "NaN", " 1", "1 ", "１", "'1'", "2023-02-29", "0000-01-01", strings.Repeat("1", 129)} {
		if queryFilterLiteral(s) {
			t.Fatalf("invalid syntax %q", s)
		}
	}
	for _, tc := range []struct {
		literal, kind string
		valid         bool
		value         any
	}{
		{"-0", "integer", true, "0"}, {"2147483647", "integer", true, "2147483647"}, {"2147483648", "integer", false, nil}, {"9223372036854775807", "bigint", true, "9223372036854775807"}, {"-9223372036854775808", "bigint", true, "-9223372036854775808"}, {"9223372036854775808", "bigint", false, nil}, {"1.0", "integer", false, nil}, {"true", "boolean", true, true}, {"1", "boolean", false, nil}, {"false", "bigint", false, nil}, {"2024-02-29", "date", true, "2024-02-29"}, {"1.2300", "decimal(3,2)", true, "1.23"}, {"1.234", "decimal(3,2)", false, nil}, {"10", "decimal(3,2)", false, nil}, {"-0.000", "decimal(3,2)", true, "0.00"}, {"99999999999999999999999999999999.999999", "decimal(38,6)", true, "99999999999999999999999999999999.999999"}, {"1", "double", false, nil}, {"1", "varchar", false, nil},
	} {
		if queryFilterTypedLiteral(tc.literal, tc.kind) != tc.valid {
			t.Fatalf("type validity %s %s", tc.literal, tc.kind)
		}
		if tc.valid && queryFilterValue(tc.literal, tc.kind) != tc.value {
			t.Fatal("normalization differs")
		}
	}
}
func TestQueryFilterRequestExactShapeAndPrivacy(t *testing.T) {
	r := filterRequest(false)
	for _, change := range []func(map[string]any){func(v map[string]any) { delete(v, "snapshot_id") }, func(v map[string]any) { v["snapshot_id"] = 1 }, func(v map[string]any) { v["snapshot_id"] = "01" }, func(v map[string]any) { v["where"] = nil }, func(v map[string]any) { v["where"].(map[string]any)["operator"] = "eq" }, func(v map[string]any) { v["where"].(map[string]any)["literal"] = true }, func(v map[string]any) { v["command"] = "describe" }, func(v map[string]any) { v["SQL"] = "PRIVATE" }} {
		var v map[string]any
		_ = json.Unmarshal(r.Request, &v)
		change(v)
		raw, _ := json.Marshal(v)
		if _, e := decodeQueryFilterRequest(raw, "select"); e == nil || strings.Contains(e.Error(), "PRIVATE") {
			t.Fatal("invalid accepted or exposed")
		}
	}
	for _, raw := range [][]byte{append(r.Request, []byte(" {}")...), []byte(`{"where":{"literal":"1","literal":"2"}}`), bytes.Repeat([]byte(" "), 4097), {0xff}} {
		if _, e := decodeQueryFilterRequest(raw, "select"); e == nil {
			t.Fatal("bad bytes accepted")
		}
	}
}
func TestQueryFilterPublicBindsHeadPredicateAndRows(t *testing.T) {
	for _, historical := range []bool{false, true} {
		r := filterRequest(historical)
		input, _ := decodeQueryFilterRequest(r.Request, "select")
		v := filterObservation(r)
		raw, _ := json.Marshal(v)
		if !queryFilterObservation(raw, input) {
			t.Fatal("valid public rejected")
		}
		for _, change := range []func(map[string]any){func(v map[string]any) { v["schema_version"] = 2 }, func(v map[string]any) { v["where"].(map[string]any)["literal"] = "PRIVATE" }, func(v map[string]any) { v["where"].(map[string]any)["column"] = "name" }, func(v map[string]any) { v["where"].(map[string]any)["operator"] = "ne" }, func(v map[string]any) { v["where"].(map[string]any)["type"] = "double" }, func(v map[string]any) { v["where"].(map[string]any)["type"] = "integer" }, func(v map[string]any) { v["current_snapshot_id"] = snapshotOld }, func(v map[string]any) { v["snapshot_id"] = "1" }, func(v map[string]any) {
			v["rows"].([]any)[0].([]any)[0].(map[string]any)["value"] = "2"
			sum := sha256.Sum256(queryReadCanonical(v["rows"]))
			v["rows_sha256"] = hex.EncodeToString(sum[:])
		}} {
			bad := filterObservation(r)
			change(bad)
			raw, _ = json.Marshal(bad)
			if queryFilterObservation(raw, input) {
				t.Fatal("mutated public accepted")
			}
		}
		// A predicate outside the projection needs no hidden extra data column.
		input.Where = queryFilterWhere{Column: "amount", Literal: "1.2300"}
		v["where"] = map[string]any{"column": "amount", "type": "decimal(38,6)", "operator": "eq"}
		raw, _ = json.Marshal(v)
		if !queryFilterObservation(raw, input) {
			t.Fatal("nonprojected filter rejected")
		}
	}
}
func TestQueryFilterEnvelopeRejectsWrongSchemaAndRawLiteral(t *testing.T) {
	r := filterRequest(false)
	wrongAction := r
	wrongAction.Action = "tables"
	if _, err := decodeQueryFilterEvent(filterLines(r, "failed")[1], wrongAction); err == nil {
		t.Fatal("foreign command accepted by fixed filter decoder")
	}
	for _, change := range []func(map[string]any){func(v map[string]any) { v["schema_version"] = 2 }, func(v map[string]any) { v["cleanup_verified"] = false }, func(v map[string]any) { v["evidence"] = "tmp/local-experiment/query-read/" + r.Token + "/summary.json" }, func(v map[string]any) { v["where_literal"] = "PRIVATE" }, func(v map[string]any) {
		v["result"] = "failed"
		v["observation"] = nil
		v["error_code"] = "UNTRUSTED_UPPERCASE_TEXT"
	}} {
		var v map[string]any
		_ = json.Unmarshal(filterLines(r, "passed")[1], &v)
		change(v)
		raw, _ := json.Marshal(v)
		if _, e := decodeQueryFilterEvent(raw, r); e == nil || strings.Contains(e.Error(), "PRIVATE") {
			t.Fatal("invalid envelope accepted")
		}
	}
}
func TestQueryFilterOnlyReleasesCompleteMatchingExit(t *testing.T) {
	r := filterRequest(false)
	lines := filterLines(r, "passed")
	for _, tc := range []struct {
		name        string
		lines       [][]byte
		exit        int
		err         error
		unconfirmed bool
	}{
		{"passed", lines, 0, nil, false}, {"wrong-exit", lines, 1, nil, false}, {"late-error", lines, 0, errors.New("PRIVATE"), false}, {"unknown", lines, 0, nil, true}, {"no-allocation", lines[1:], 0, nil, false}, {"double-final", append(append([][]byte{}, lines...), lines[1]), 0, nil, false}, {"late-line", append(append([][]byte{}, lines...), []byte("PRIVATE")), 0, nil, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var out bytes.Buffer
			child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				for _, l := range tc.lines {
					if e := emit(l); e != nil {
						return dataChildResult{Started: true}, e
					}
				}
				return dataChildResult{Started: true, ExitCode: tc.exit, Unconfirmed: tc.unconfirmed}, tc.err
			})
			e := runQueryFilterCommand(context.Background(), child, r, &out)
			passed := strings.Contains(out.String(), `"result":"passed"`)
			if (e == nil) != (tc.name == "passed") || passed != (tc.name == "passed") || strings.Contains(out.String(), "PRIVATE") {
				t.Fatalf("%v %s", e, &out)
			}
		})
	}
	for _, tc := range []struct {
		outcome string
		exit    int
	}{{"failed", 1}, {"interrupted", 130}, {"interrupted", 143}} {
		var out bytes.Buffer
		child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			for _, l := range filterLines(r, tc.outcome) {
				if e := emit(l); e != nil {
					return dataChildResult{Started: true}, e
				}
			}
			return dataChildResult{Started: true, ExitCode: tc.exit}, nil
		})
		if e := runQueryFilterCommand(context.Background(), child, r, &out); ExitCode(e) != tc.exit {
			t.Fatal(e)
		}
	}
}
func TestQueryFilterFixedChildUsesStdinAndFilteredEnvironment(t *testing.T) {
	r := filterRequest(false)
	r.Root = filterRoot(t)
	t.Setenv("PRIVATE_SENTINEL", "must-not-reach-child")
	t.Setenv("HTTP_PROXY", "must-not-reach-child")
	kubeconfig := filepath.Join(r.Root, "kubeconfig")
	t.Setenv("KUBECONFIG", kubeconfig)
	before := strings.Join(os.Environ(), "\n")
	cmd, e := queryFilterCommand(r)
	if e != nil {
		t.Fatal(e)
	}
	args := strings.Join(cmd.Args, " ")
	if !strings.Contains(args, queryFilterScript) || !strings.Contains(args, "--request-stdin") || strings.Contains(args, "fixture_v1") || strings.Contains(args, "literal") || strings.Contains(strings.Join(cmd.Env, " "), "PRIVATE_SENTINEL") || strings.Contains(strings.Join(cmd.Env, " "), "HTTP_PROXY") {
		t.Fatal("child argument/environment leakage")
	}
	if !strings.Contains("\n"+strings.Join(cmd.Env, "\n")+"\n", "\nKUBECONFIG="+kubeconfig+"\n") || strings.Join(os.Environ(), "\n") != before {
		t.Fatal("KUBECONFIG forwarding changed the parent environment or lost its fixed path")
	}
	raw, e := io.ReadAll(cmd.Stdin)
	if e != nil || !bytes.Equal(raw, r.Request) {
		t.Fatal("stdin differs")
	}
	for _, which := range []string{"case", "action", "token"} {
		bad := r
		switch which {
		case "case":
			bad.Case = ""
		case "action":
			bad.Action = "describe"
		case "token":
			bad.Token = "INVALID"
		}
		if _, e := queryFilterCommand(bad); e == nil {
			t.Fatal("bad child invocation accepted")
		}
	}
	path := filepath.Join(r.Root, queryFilterScript)
	if e := os.Remove(path); e != nil {
		t.Fatal(e)
	}
	if _, e := queryFilterCommand(r); e == nil {
		t.Fatal("missing entrypoint accepted")
	}
	if e := os.Symlink(filepath.Join(r.Root, queryReadScript), path); e != nil {
		t.Fatal(e)
	}
	if _, e := queryFilterCommand(r); e == nil {
		t.Fatal("redirected entrypoint accepted")
	}
}

func TestQueryFilterTransportBoundAndOwnedCancellation(t *testing.T) {
	for _, size := range []int{queryFilterResponseLimit, queryFilterResponseLimit + 1} {
		child := execQueryFilterChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return queryReadTransportProcess(size), nil }}
		seen := 0
		result, err := child.Run(context.Background(), dataChildRequest{}, func(line []byte) error { seen = len(line); return nil })
		if size == queryFilterResponseLimit && (err != nil || result.ExitCode != 0 || seen != size) || size > queryFilterResponseLimit && (err == nil || seen != 0) {
			t.Fatal("filter transport byte boundary differs")
		}
	}
	marker := filepath.Join(t.TempDir(), "cleaned")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	child := execQueryFilterChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess("cancel", marker), nil }}
	result, err := child.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if !errors.Is(err, errDataInterrupted) || !result.Interrupted || result.ExitCode != 130 {
		t.Fatalf("owned cancellation lost: %v %+v", err, result)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatal("owned child cleanup was not completed")
	}
}

func TestQueryFilterDiagnosisReportKeepsThreeProfilesDistinct(t *testing.T) {
	value := qdrTestReport()
	value.Runs[1].SourceProfile = qdrPtr("snapshot-reader-v2")
	filtered := value.Runs[0]
	filtered.EvidenceToken = strings.Repeat("d", 32)
	filtered.Evidence = qdrPtr("tmp/local-experiment/query-diagnose/" + filtered.EvidenceToken + "/summary.json")
	filtered.SourceProfile = qdrPtr(queryFilterCase)
	value.Runs = append(value.Runs, filtered)
	value.Inventory.Counts.Passed = 2
	value.Inventory.InspectedEntries = 3
	value.Groups = queryDiagnosisReportGroups(value.Runs)
	if len(value.Groups) != 3 {
		t.Fatal("source profiles merged")
	}
	if code, err := decodeQueryDiagnoseReport(qdrTestLine(t, value), qdrTestRequest()); err != nil || code != 0 {
		t.Fatalf("new source profile rejected: %v", err)
	}
	value.Runs[2].SourceProfile = qdrPtr("unknown-reader-v3")
	value.Groups = queryDiagnosisReportGroups(value.Runs)
	if _, err := decodeQueryDiagnoseReport(qdrTestLine(t, value), qdrTestRequest()); err == nil {
		t.Fatal("unknown source profile accepted")
	}
}
