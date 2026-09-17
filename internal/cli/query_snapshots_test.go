package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"github.com/spf13/cobra"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const snapshotOld = "9211570477933275546"
const snapshotHead = "2224987170743755404"

func snapshotRoot(t *testing.T) string {
	t.Helper()
	root := queryReadTestRoot(t)
	p := filepath.Join(root, querySnapshotsScript)
	if e := os.MkdirAll(filepath.Dir(p), 0700); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(p, []byte("fixture"), 0600); e != nil {
		t.Fatal(e)
	}
	return root
}
func snapshotRequest(action string) dataChildRequest {
	v := querySnapshotsRequest{Command: action, Table: "smoke.fixture_v1"}
	if action == "select" {
		v.Columns = []string{"id", "name"}
		v.Limit = 2
		v.SnapshotID = snapshotOld
	}
	raw, _ := json.Marshal(v)
	return dataChildRequest{Action: action, Case: querySnapshotsCase, Token: testWarehouseSession, Request: raw}
}
func snapshotRecord(id string, parent any, sequence string, current, retained bool) map[string]any {
	return map[string]any{"snapshot_id": id, "parent_snapshot_id": parent, "sequence_number": sequence, "timestamp_ms": 1234, "schema_id": 0, "operation": "append", "is_current": current, "parent_retained": retained, "schema_compatibility": "compatible"}
}
func snapshotObservation(action string) map[string]any {
	old := snapshotRecord(snapshotOld, nil, "1", false, false)
	head := snapshotRecord(snapshotHead, snapshotOld, "2", true, true)
	if action == "select" {
		v := queryReadTestObservation("select")
		v["schema_version"] = 2
		v["current_snapshot_id"] = snapshotHead
		v["selected_snapshot"] = old
		return v
	}
	return map[string]any{"schema_version": 2, "table": "iceberg.smoke.fixture_v1", "table_uuid": testWarehouseRequest, "current_snapshot_id": snapshotHead, "snapshots": []any{old, head}, "complete": true, "ordering": "timestamp-asc"}
}
func snapshotLines(r dataChildRequest, outcome string) [][]byte {
	a, _ := json.Marshal(map[string]any{"schema_version": 2, "event": "allocated", "command": "query " + r.Action, "evidence_token": r.Token})
	v := map[string]any{"schema_version": 2, "event": "result", "command": "query " + r.Action, "evidence_token": r.Token, "result": outcome, "observation": nil, "error_code": "SNAPSHOT_READ_FAILED", "cleanup_verified": false, "evidence": "tmp/local-experiment/query-snapshots/" + r.Token + "/summary.json"}
	if outcome == "passed" {
		v["observation"] = snapshotObservation(r.Action)
		v["error_code"] = nil
		v["cleanup_verified"] = true
	}
	if outcome == "interrupted" {
		v["error_code"] = "INTERRUPTED"
	}
	f, _ := json.Marshal(v)
	return [][]byte{a, f}
}
func snapshotExecute(t *testing.T, old, new dataChildRunner, args ...string) (string, error) {
	t.Helper()
	root, profile := "", localExperimentProfile
	p := &cobra.Command{Use: "query", SilenceErrors: true, SilenceUsage: true}
	p.PersistentFlags().StringVar(&root, "repo-root", "", "")
	p.PersistentFlags().StringVar(&profile, "profile", profile, "")
	p.AddCommand(newQuerySnapshotsCommand(new, &root, &profile))
	p.AddCommand(newQueryReadCommandWithSnapshots("select", old, new, &root, &profile))
	var out bytes.Buffer
	p.SetOut(&out)
	p.SetErr(io.Discard)
	p.SetArgs(args)
	e := p.Execute()
	return out.String(), e
}
func snapshotSuccess(t *testing.T, expected string) dataChildRunner {
	t.Helper()
	return dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		if r.Action != expected || r.Case != querySnapshotsCase {
			t.Fatal("wrong route")
		}
		for _, line := range snapshotLines(r, "passed") {
			if e := emit(line); e != nil {
				return dataChildResult{Started: true}, e
			}
		}
		return dataChildResult{Started: true, PID: 42, ExitCode: 0}, nil
	})
}
func TestQuerySnapshotsRouteOnlyExplicitFlag(t *testing.T) {
	root := snapshotRoot(t)
	never := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("wrong child")
		return dataChildResult{}, nil
	})
	for _, a := range []string{"snapshots", "select"} {
		args := []string{a, "--table", "smoke.fixture_v1", "--repo-root", root}
		if a == "select" {
			args = append(args, "--columns", "id,name", "--limit", "2", "--snapshot-id", snapshotOld)
		}
		out, e := snapshotExecute(t, never, snapshotSuccess(t, a), args...)
		if e != nil || !strings.Contains(out, `"schema_version":2`) {
			t.Fatalf("%v %s", e, out)
		}
	}
	old := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		want, _ := json.Marshal(queryReadRequest{Command: "select", Table: "smoke.fixture_v1", Columns: []string{"id", "name"}, Limit: 2})
		if r.Case != "" || !bytes.Equal(r.Request, want) {
			t.Fatal("old v1 request bytes changed")
		}

		for _, l := range queryReadTestLines(r, "passed") {
			if e := emit(l); e != nil {
				return dataChildResult{Started: true}, e
			}
		}
		return dataChildResult{Started: true, ExitCode: 0}, nil
	})
	out, e := snapshotExecute(t, old, never, "select", "--table", "smoke.fixture_v1", "--columns", "id,name", "--limit", "2", "--repo-root", root)
	if e != nil || !strings.Contains(out, `"schema_version":1`) {
		t.Fatalf("%v %s", e, out)
	}
}
func TestQuerySnapshotsBadInputBeforeChild(t *testing.T) {
	root := snapshotRoot(t)
	never := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("invalid input reached child")
		return dataChildResult{}, nil
	})
	for _, id := range []string{"", "0", "-1", "01", "+1", "1.0", "1e2", "9223372036854775808", "PRIVATE;SQL"} {
		out, e := snapshotExecute(t, never, never, "select", "--table", "smoke.fixture_v1", "--columns", "id", "--limit", "1", "--snapshot-id", id, "--repo-root", root)
		if e == nil || out != "" || strings.Contains(e.Error(), "PRIVATE") {
			t.Fatal("invalid snapshot accepted/leaked")
		}
	}
	for _, args := range [][]string{{"snapshots"}, {"snapshots", "--table", "other.x"}, {"snapshots", "--table", "smoke.x;DROP"}, {"snapshots", "--table", "smoke.x", "extra"}, {"snapshots", "--table", "smoke.x", "--profile", "production"}, {"snapshots", "--table", "smoke.x", "--snapshot-id", "1"}} {
		if out, e := snapshotExecute(t, never, never, append(args, "--repo-root", root)...); e == nil || out != "" {
			t.Fatal("bad args accepted")
		}
	}
}
func TestQuerySnapshotsRequestStrictKeysTypes(t *testing.T) {
	r := snapshotRequest("select")
	for _, mutate := range []func(map[string]any){func(v map[string]any) { v["snapshot_id"] = float64(1) }, func(v map[string]any) { v["snapshot_id"] = true }, func(v map[string]any) { v["snapshot_id"] = nil }, func(v map[string]any) { v["limit"] = 1.5 }, func(v map[string]any) { v["columns"] = []string{"id", "id"} }, func(v map[string]any) { v["raw_sql"] = "private" }} {
		var v map[string]any
		_ = json.Unmarshal(r.Request, &v)
		mutate(v)
		raw, _ := json.Marshal(v)
		if _, e := decodeQuerySnapshotsRequest(raw, "select"); e == nil {
			t.Fatal("invalid request accepted")
		}
	}
	for _, raw := range [][]byte{append(r.Request, []byte(" {}")...), []byte(`{"command":"snapshots","command":"snapshots","table":"smoke.x"}`), bytes.Repeat([]byte(" "), 4097)} {
		if _, e := decodeQuerySnapshotsRequest(raw, "snapshots"); e == nil {
			t.Fatal("invalid JSON accepted")
		}
	}
}
func TestQuerySnapshotsFixedChildPrivacyAndDiscriminator(t *testing.T) {
	root := snapshotRoot(t)
	r := snapshotRequest("select")
	r.Root = root
	t.Setenv("PRIVATE_CREDENTIAL", "secret")
	t.Setenv("PYTHONPATH", "private")
	cmd, e := querySnapshotsCommand(r)
	if e != nil {
		t.Fatal(e)
	}
	joined := strings.Join(cmd.Args, " ")
	if !strings.Contains(joined, querySnapshotsScript) || !strings.Contains(joined, "-I -B") || strings.Contains(joined, snapshotOld) || strings.Contains(joined, "smoke.fixture_v1") {
		t.Fatal("args escaped fixed boundary")
	}
	body, _ := io.ReadAll(cmd.Stdin)
	if !bytes.Equal(body, r.Request) || cmd.Dir != root || cmd.SysProcAttr == nil || !cmd.SysProcAttr.Setpgid {
		t.Fatal("child ownership/input differs")
	}
	for _, e := range cmd.Env {
		if strings.HasPrefix(e, "PRIVATE_") || strings.HasPrefix(e, "PYTHONPATH=") {
			t.Fatal("environment leaked")
		}
	}
	for _, bad := range []string{"", "select", "snapshots-v1"} {
		r.Case = bad
		if _, e := querySnapshotsCommand(r); e == nil {
			t.Fatal("bad discriminator accepted")
		}
	}
}
func TestQuerySnapshotsMissingRedirectedEnvironmentRejected(t *testing.T) {
	root := snapshotRoot(t)
	p := filepath.Join(root, querySnapshotsScript)
	if e := os.Remove(p); e != nil {
		t.Fatal(e)
	}
	if _, e := resolveQuerySnapshotsRoot(root); e == nil {
		t.Fatal("missing script accepted")
	}
	other := filepath.Join(root, "foreign.py")
	_ = os.WriteFile(other, []byte("private"), 0600)
	_ = os.Symlink(other, p)
	if _, e := resolveQuerySnapshotsRoot(root); e == nil {
		t.Fatal("redirected script accepted")
	}
}
func TestQuerySnapshotsRecordExactTypesAndBounds(t *testing.T) {
	for _, edit := range []func(map[string]any){func(v map[string]any) { v["snapshot_id"] = float64(1) }, func(v map[string]any) { v["snapshot_id"] = "9223372036854775808" }, func(v map[string]any) { v["parent_snapshot_id"] = v["snapshot_id"] }, func(v map[string]any) { v["sequence_number"] = "01" }, func(v map[string]any) { v["sequence_number"] = "-0" }, func(v map[string]any) { v["sequence_number"] = "9223372036854775808" }, func(v map[string]any) { v["timestamp_ms"] = 1.5 }, func(v map[string]any) { v["timestamp_ms"] = 253402300800000 }, func(v map[string]any) { v["schema_id"] = nil }, func(v map[string]any) { v["schema_id"] = true }, func(v map[string]any) { v["operation"] = "PRIVATE" }, func(v map[string]any) { v["is_current"] = nil }, func(v map[string]any) { v["parent_retained"] = true }, func(v map[string]any) { v["schema_compatibility"] = "unknown" }, func(v map[string]any) { v["extra"] = false }} {
		v := snapshotObservation("snapshots")
		edit(v["snapshots"].([]any)[0].(map[string]any))
		raw, _ := json.Marshal(v)
		r, _ := decodeQuerySnapshotsRequest(snapshotRequest("snapshots").Request, "snapshots")
		if querySnapshotsObservation(raw, r) {
			t.Fatal("malformed record accepted")
		}
	}
}
func TestQuerySnapshotsMembershipOrderingCyclesAndExpiredParent(t *testing.T) {
	r, _ := decodeQuerySnapshotsRequest(snapshotRequest("snapshots").Request, "snapshots")
	for _, edit := range []func(map[string]any){func(v map[string]any) { v["current_snapshot_id"] = nil }, func(v map[string]any) { v["complete"] = false }, func(v map[string]any) { v["ordering"] = "id-asc" }, func(v map[string]any) { s := v["snapshots"].([]any); v["snapshots"] = []any{s[1], s[0]} }, func(v map[string]any) { s := v["snapshots"].([]any); s[0].(map[string]any)["is_current"] = true }, func(v map[string]any) { s := v["snapshots"].([]any); s[1].(map[string]any)["parent_retained"] = false }, func(v map[string]any) {
		s := v["snapshots"].([]any)
		s[0].(map[string]any)["parent_snapshot_id"] = snapshotHead
		s[0].(map[string]any)["parent_retained"] = true
	}, func(v map[string]any) { s := v["snapshots"].([]any); v["snapshots"] = append(s, s[1]) }} {
		v := snapshotObservation("snapshots")
		edit(v)
		raw, _ := json.Marshal(v)
		if querySnapshotsObservation(raw, r) {
			t.Fatal("bad graph accepted")
		}
	}
	v := snapshotObservation("snapshots")
	row := v["snapshots"].([]any)[0].(map[string]any)
	row["parent_snapshot_id"] = "1"
	raw, _ := json.Marshal(v)
	if !querySnapshotsObservation(raw, r) {
		t.Fatal("expired parent rejected")
	}
	v["snapshots"] = []any{}
	v["current_snapshot_id"] = nil
	raw, _ = json.Marshal(v)
	if !querySnapshotsObservation(raw, r) {
		t.Fatal("empty valid table rejected")
	}
}
func TestQuerySnapshotsHistoricalRowsKeepSelectedAndHeadDistinct(t *testing.T) {
	r, _ := decodeQuerySnapshotsRequest(snapshotRequest("select").Request, "select")
	v := snapshotObservation("select")
	raw, _ := json.Marshal(v)
	if !querySnapshotsObservation(raw, r) {
		t.Fatal("historical rows rejected")
	}
	for _, edit := range []func(map[string]any){func(v map[string]any) { v["snapshot_id"] = snapshotHead }, func(v map[string]any) { v["current_snapshot_id"] = nil }, func(v map[string]any) { v["selected_snapshot"].(map[string]any)["is_current"] = true }, func(v map[string]any) {
		v["selected_snapshot"].(map[string]any)["schema_compatibility"] = "schema-mismatch"
	}, func(v map[string]any) { v["rows_sha256"] = strings.Repeat("0", 64) }, func(v map[string]any) { v["schema_version"] = 1 }} {
		v = snapshotObservation("select")
		edit(v)
		raw, _ = json.Marshal(v)
		if querySnapshotsObservation(raw, r) {
			t.Fatal("false selected identity accepted")
		}
	}
}
func TestQuerySnapshotsSchemaCompatibilityEnums(t *testing.T) {
	r, _ := decodeQuerySnapshotsRequest(snapshotRequest("snapshots").Request, "snapshots")
	for _, compat := range []string{"compatible", "schema-missing", "schema-not-retained", "schema-mismatch", "schema-unsupported"} {
		v := snapshotObservation("snapshots")
		row := v["snapshots"].([]any)[0].(map[string]any)
		row["schema_compatibility"] = compat
		if compat == "schema-missing" {
			row["schema_id"] = nil
		}
		raw, _ := json.Marshal(v)
		if !querySnapshotsObservation(raw, r) {
			t.Fatal("valid compatibility rejected")
		}
	}
}
func TestQuerySnapshotsEnvelopeExactKeysAndFixedErrors(t *testing.T) {
	r := snapshotRequest("snapshots")
	for _, edit := range []func(map[string]any){func(v map[string]any) { v["schema_version"] = 1 }, func(v map[string]any) { v["schema_version"] = true }, func(v map[string]any) { v["command"] = "query tables" }, func(v map[string]any) { v["evidence"] = "tmp/private" }, func(v map[string]any) { v["cleanup_verified"] = false }, func(v map[string]any) { v["observation"] = nil }, func(v map[string]any) { v["raw"] = true }} {
		var v map[string]any
		_ = json.Unmarshal(snapshotLines(r, "passed")[1], &v)
		edit(v)
		raw, _ := json.Marshal(v)
		if _, e := decodeQuerySnapshotsEvent(raw, r); e == nil {
			t.Fatal("false envelope accepted")
		}
	}
	for _, code := range []string{"PRIVATE_UPPERCASE", "private-error", ""} {
		var v map[string]any
		_ = json.Unmarshal(snapshotLines(r, "failed")[1], &v)
		v["error_code"] = code
		raw, _ := json.Marshal(v)
		if _, e := decodeQuerySnapshotsEvent(raw, r); e == nil {
			t.Fatal("unknown error accepted")
		}
	}
}
func TestQuerySnapshotsFinalWithheldUntilExitAndCompleteProtocol(t *testing.T) {
	r := snapshotRequest("select")
	lines := snapshotLines(r, "passed")
	cases := []struct {
		lines       [][]byte
		exit        int
		unconfirmed bool
	}{{lines, 1, false}, {lines, 0, true}, {lines[1:], 0, false}, {[][]byte{lines[0], lines[0], lines[1]}, 0, false}, {[][]byte{lines[0], lines[1], lines[1]}, 0, false}, {[][]byte{lines[0], lines[1], []byte("PRIVATE")}, 0, false}}
	for _, c := range cases {
		var out bytes.Buffer
		child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			for _, l := range c.lines {
				if e := emit(l); e != nil {
					return dataChildResult{Started: true}, e
				}
			}
			return dataChildResult{Started: true, PID: 42, ExitCode: c.exit, Unconfirmed: c.unconfirmed}, nil
		})
		if e := runQuerySnapshotsCommand(context.Background(), child, r, &out); e == nil || strings.Contains(out.String(), `"result":"passed"`) || strings.Contains(out.String(), "PRIVATE") {
			t.Fatal("premature success/leak")
		}
	}
}
func TestQuerySnapshotsFailureAndCancellationExits(t *testing.T) {
	r := snapshotRequest("snapshots")
	for _, outcome := range []string{"failed", "interrupted"} {
		want := 1
		if outcome == "interrupted" {
			want = 143
		}
		var out bytes.Buffer
		child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			if e := emit(snapshotLines(r, outcome)[1]); e != nil {
				return dataChildResult{Started: true}, e
			}
			return dataChildResult{Started: true, ExitCode: want}, nil
		})
		if e := runQuerySnapshotsCommand(context.Background(), child, r, &out); ExitCode(e) != want {
			t.Fatal("preallocation failure exit mismatch")
		}
	}
}
func TestQuerySnapshotsTransportBoundAndOwnedCancellation(t *testing.T) {
	for _, n := range []int{querySnapshotsResponseLimit, querySnapshotsResponseLimit + 1} {
		seen := 0
		runner := execQuerySnapshotsChild{command: func(dataChildRequest) (*exec.Cmd, error) { return queryReadTransportProcess(n), nil }, grace: time.Second}
		_, e := runner.Run(context.Background(), dataChildRequest{}, func(l []byte) error { seen = len(l); return nil })
		if n == querySnapshotsResponseLimit && (e != nil || seen != n) || n > querySnapshotsResponseLimit && (e == nil || seen != 0) {
			t.Fatal("transport bound differs")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	marker := filepath.Join(t.TempDir(), "cleaned")
	runner := execQuerySnapshotsChild{command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess("cancel", marker), nil }, grace: time.Second}
	result, e := runner.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if !errors.Is(e, errDataInterrupted) || result.ExitCode != 130 {
		t.Fatal("cancellation differs")
	}
	if _, e := os.Stat(marker); e != nil {
		t.Fatal("cleanup missing")
	}
}
func TestQuerySnapshotsRegistration(t *testing.T) {
	p := newQueryCommand(execDataChild{})
	names := map[string]bool{}
	for _, c := range p.Commands() {
		names[c.Name()] = true
	}
	for _, n := range []string{"snapshots", "select", "tables", "describe", "diagnose", "run", "status"} {
		if !names[n] {
			t.Fatal("command missing")
		}
	}
}

func TestQuerySnapshotsActualPythonOwnerAndContract(t *testing.T) {
	root, e := filepath.Abs("../..")
	if e != nil {
		t.Fatal(e)
	}
	python := filepath.Join(root, dataPython)
	if _, e := os.Stat(python); e != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	harness := `
import contextlib,pathlib,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from tools.local_experiment.query_snapshots import run as R,contract as C
from test_query_snapshots_run import observed
R.STATE=pathlib.Path(sys.argv[2]).resolve()/'snapshots'
mode=sys.argv[3]
if mode=='late-failure':
    original=R.owner_lock
    @contextlib.contextmanager
    def lock(output):
        with original(output):yield
        raise OSError('PRIVATE_AFTER_RESULT')
    R.owner_lock=lock
def observer(value,token,progress):
    if mode=='not-retained':raise C.ContractError('SNAPSHOT_NOT_RETAINED')
    if mode=='schema-unsupported':raise C.ContractError('SNAPSHOT_SCHEMA_UNSUPPORTED')
    if mode=='interrupted':raise KeyboardInterrupt()
    journal,public=observed(value)
    if mode=='incomplete':journal['cleanup_verified']=False
    progress(journal)
    return journal,public
def invoke(token,value,emit):return R.invoke(token,value,emit,observer=observer)
raise SystemExit(R.main([sys.argv[5],'--evidence-token',sys.argv[4],'--request-stdin'],invoker=invoke))
`
	for _, tc := range []struct {
		mode, action string
		exit         int
		code         string
	}{{"snapshots", "snapshots", 0, ""}, {"select", "select", 0, ""}, {"not-retained", "select", 1, "SNAPSHOT_NOT_RETAINED"}, {"schema-unsupported", "select", 1, "SNAPSHOT_SCHEMA_UNSUPPORTED"}, {"incomplete", "select", 1, "SNAPSHOT_CLEANUP_UNVERIFIED"}, {"interrupted", "select", 130, "INTERRUPTED"}, {"late-failure", "select", 1, ""}} {
		t.Run(tc.mode, func(t *testing.T) {
			output := t.TempDir()
			_ = os.Chmod(output, 0700)
			r := snapshotRequest(tc.action)
			if tc.action == "select" {
				v, _ := decodeQuerySnapshotsRequest(r.Request, r.Action)
				v.SnapshotID = "9223372036854775806"
				r.Request, _ = json.Marshal(v)
			}
			child := execQuerySnapshotsChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, tc.mode, r.Token, r.Action)
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Dir = root
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQuerySnapshotsCommand(context.Background(), child, r, &out)
			if ExitCode(err) != tc.exit {
				t.Fatalf("%v %s", err, &out)
			}
			if tc.exit == 0 && !strings.Contains(out.String(), `"result":"passed"`) {
				t.Fatal("success missing")
			}
			if tc.exit != 0 && (strings.Contains(out.String(), `"result":"passed"`) || strings.Contains(out.String(), "PRIVATE_AFTER_RESULT") || tc.code != "" && !strings.Contains(out.String(), tc.code)) {
				t.Fatalf("failure protocol differs: %s", &out)
			}
			raw, e := os.ReadFile(filepath.Join(output, "snapshots", r.Token, "summary.json"))
			if e != nil {
				t.Fatal(e)
			}
			if bytes.Contains(raw, []byte("private-row-sentinel")) || bytes.Contains(raw, []byte(`"rows":`)) {
				t.Fatal("explicit row saved into receipt")
			}
			var saved map[string]any
			_ = json.Unmarshal(raw, &saved)
			if saved["model_calls"] != float64(0) || saved["cloud_calls"] != float64(0) || saved["invocations"] != float64(1) {
				t.Fatal("owner count differs")
			}
			if tc.exit == 0 && saved["cleanup_verified"] != true {
				t.Fatal("passed without cleanup")
			}
		})
	}
}

func TestQuerySnapshotsPythonPublicEnumsAndErrorsMatch(t *testing.T) {
	root, e := filepath.Abs("../..")
	if e != nil {
		t.Fatal(e)
	}
	python := filepath.Join(root, dataPython)
	if _, e := os.Stat(python); e != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	script := `import json,sys;sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests'];from tools.local_experiment.query_snapshots import contract as C,run as R;from test_query_snapshots_contract import request,listing_request,window,result,schema;print(json.dumps({'codes':sorted(R.codes()),'listing':C.project_snapshots(window(),listing_request()),'selection':C.project_rows(result(),request(),schema(),window()),'list_request':listing_request(),'select_request':request()}))`
	cmd := exec.Command(python, "-I", "-B", "-c", script, root)
	cmd.Env = []string{"PATH=/usr/bin:/bin"}
	raw, e := cmd.Output()
	if e != nil {
		t.Fatal(e)
	}
	var v map[string]json.RawMessage
	if e = json.Unmarshal(raw, &v); e != nil {
		t.Fatal(e)
	}
	var codes []string
	_ = json.Unmarshal(v["codes"], &codes)
	for _, c := range codes {
		if !querySnapshotsErrorCode(c) {
			t.Fatalf("missing Python code: %s", c)
		}
	}
	for _, pair := range [][3]string{{"list_request", "listing", "snapshots"}, {"select_request", "selection", "select"}} {
		r, e := decodeQuerySnapshotsRequest(v[pair[0]], pair[2])
		if e != nil || !querySnapshotsObservation(v[pair[1]], r) {
			t.Fatalf("actual Python public rejected for %s", pair[2])
		}
	}
}

func TestQuerySnapshotsDefaultReaderDoesNotRequireNewProfileFiles(t *testing.T) {
	root := queryReadTestRoot(t)
	never := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("snapshot child reached without flag")
		return dataChildResult{}, nil
	})
	old := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		for _, l := range queryReadTestLines(r, "passed") {
			if e := emit(l); e != nil {
				return dataChildResult{Started: true}, e
			}
		}
		return dataChildResult{Started: true, ExitCode: 0}, nil
	})
	if _, e := snapshotExecute(t, old, never, "select", "--table", "smoke.fixture_v1", "--columns", "id,name", "--limit", "2", "--repo-root", root); e != nil {
		t.Fatal(e)
	}
}
