package cli

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

func queryReadTestRoot(t *testing.T) string {
	t.Helper()
	root := dataTestRoot(t)
	path := filepath.Join(root, queryReadScript)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}
func queryReadTestRequest(action string) dataChildRequest {
	value := map[string]any{"command": action, "table": "smoke.fixture_v1"}
	if action == "tables" {
		delete(value, "table")
		value["schema"] = "smoke"
	}
	if action == "select" {
		value["columns"] = []string{"id", "name"}
		value["limit"] = 2
	}
	raw, _ := json.Marshal(value)
	return dataChildRequest{Action: action, Token: testWarehouseSession, Request: raw}
}
func queryReadTestObservation(action string) map[string]any {
	columns := []any{map[string]any{"name": "id", "type": "bigint", "nullable": false}, map[string]any{"name": "name", "type": "varchar", "nullable": true}}
	if action == "tables" {
		return map[string]any{"schema_version": 1, "catalog": "iceberg", "schema": "smoke", "tables": []string{"smoke.fixture_v1"}, "complete": true}
	}
	if action == "describe" {
		return map[string]any{"schema_version": 1, "catalog": "iceberg", "schema": "smoke", "table": "fixture_v1", "table_uuid": testWarehouseRequest, "format_version": 2, "schema_id": 0, "schema_sha256": strings.Repeat("a", 64), "spec_sha256": strings.Repeat("b", 64), "refs_sha256": strings.Repeat("c", 64), "current_snapshot_id": "9211570477933275546", "metadata_sha256": strings.Repeat("d", 64), "metadata_location_sha256": strings.Repeat("e", 64), "table_location_sha256": strings.Repeat("f", 64), "columns": columns}
	}
	rows := []any{[]any{map[string]any{"type": "bigint", "value": "9223372036854775807"}, map[string]any{"type": "varchar", "value": "雪☃<>&\n😀"}}, []any{map[string]any{"type": "bigint", "value": "-9223372036854775808"}, map[string]any{"type": "varchar", "value": nil}}}
	sum := sha256.Sum256(queryReadCanonical(rows))
	return map[string]any{"schema_version": 1, "table": "iceberg.smoke.fixture_v1", "table_uuid": testWarehouseRequest, "schema_sha256": strings.Repeat("a", 64), "snapshot_id": "9211570477933275546", "columns": columns, "rows": rows, "row_count": 2, "fetched_row_count": 3, "truncated": true, "requested_limit": 2, "ordering": "unspecified", "rows_sha256": hex.EncodeToString(sum[:])}
}
func queryReadTestLines(request dataChildRequest, outcome string) [][]byte {
	allocated, _ := json.Marshal(map[string]any{"schema_version": 1, "event": "allocated", "command": "query " + request.Action, "evidence_token": request.Token})
	event := map[string]any{"schema_version": 1, "event": "result", "command": "query " + request.Action, "evidence_token": request.Token, "result": outcome, "observation": nil, "error_code": "READ_FAILED", "evidence": "tmp/local-experiment/query-read/" + request.Token + "/summary.json", "cleanup_verified": true}
	if outcome == "passed" {
		event["error_code"] = nil
		event["observation"] = queryReadTestObservation(request.Action)
	}
	if outcome == "interrupted" {
		event["error_code"] = "INTERRUPTED"
		event["cleanup_verified"] = false
	}
	final, _ := json.Marshal(event)
	return [][]byte{allocated, final}
}
func executeQueryRead(t *testing.T, child dataChildRunner, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	root, profile := "", localExperimentProfile
	parent := &cobra.Command{Use: "query", SilenceErrors: true, SilenceUsage: true}
	parent.PersistentFlags().StringVar(&root, "repo-root", "", "")
	parent.PersistentFlags().StringVar(&profile, "profile", profile, "")
	for _, action := range []string{"tables", "describe", "select"} {
		parent.AddCommand(newQueryReadCommand(action, child, &root, &profile))
	}
	parent.SetArgs(args)
	parent.SetOut(&out)
	parent.SetErr(io.Discard)
	err := parent.Execute()
	return out.String(), err
}

func TestQueryReadCommandsUseNormalizedJSONAndDistinctTokens(t *testing.T) {
	root := queryReadTestRoot(t)
	seen := map[string]bool{}
	for _, args := range [][]string{{"tables", "--schema", "smoke"}, {"describe", "--table", "smoke.fixture_v1"}, {"select", "--table", "smoke.fixture_v1", "--columns", "id,name", "--limit", "2"}} {
		calls := 0
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			calls++
			if r.Root != root || r.Case != "" || !warehouseSessionPattern.MatchString(r.Token) || seen[r.Token] {
				t.Fatal("bad invocation")
			}
			seen[r.Token] = true
			if _, err := decodeQueryReadRequest(r.Request, args[0]); err != nil {
				t.Fatal(err)
			}
			for _, line := range queryReadTestLines(r, "passed") {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true}, err
				}
			}
			return dataChildResult{Started: true, PID: 42, ExitCode: 0}, nil
		})
		out, err := executeQueryRead(t, child, append(args, "--repo-root", root)...)
		if err != nil || calls != 1 || !strings.Contains(out, `"result":"passed"`) {
			t.Fatalf("%v: %v %s", args, err, out)
		}
	}
}
func TestQueryReadInvalidInputNeverStartsChild(t *testing.T) {
	root := queryReadTestRoot(t)
	cases := [][]string{{"tables"}, {"tables", "--schema", "other"}, {"describe", "--table", "iceberg.smoke.fixture"}, {"describe", "--table", "smoke.x;DROP"}, {"describe", "--table", "smoke.日本語"}, {"select", "--table", "smoke.x", "--columns", "*", "--limit", "1"}, {"select", "--table", "smoke.x", "--columns", "id,id", "--limit", "1"}, {"select", "--table", "smoke.x", "--columns", "id, name", "--limit", "1"}, {"select", "--table", "smoke.x", "--columns", "id", "--limit", "0"}, {"select", "--table", "smoke.x", "--columns", "id", "--limit", "101"}, {"select", "--table", "smoke.x", "--columns", "id", "--limit", "1.5"}, {"select", "--table", "smoke.x", "--columns", "id"}, {"tables", "--schema", "smoke", "extra-private"}, {"tables", "--schema", "smoke", "--profile", "production"}, {"tables", "--schema", "smoke", "--sql", "PRIVATE_SQL"}}
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("invalid input reached child")
		return dataChildResult{}, nil
	})
	for _, args := range cases {
		out, err := executeQueryRead(t, child, append(args, "--repo-root", root)...)
		if err == nil || out != "" || strings.Contains(err.Error(), "PRIVATE_SQL") || strings.Contains(err.Error(), "extra-private") {
			t.Fatalf("%v accepted/leaked: %v %s", args, err, out)
		}
	}
}
func TestQueryReadRequestDecoderExactKeysTypesAndAction(t *testing.T) {
	for _, raw := range []string{`{"command":"tables","schema":"smoke","extra":1}`, `{"command":"tables","schema":"smoke","schema":"smoke"}`, `{"command":"tables","schema":"smoke"} {}`, `{"command":"select","table":"smoke.x","columns":["id"],"limit":1.0}`, `{"command":"select","table":"smoke.x","columns":["id"],"limit":true}`, `{"command":"select","table":"smoke.x","columns":["id"],"limit":null}`, `{"command":"select","table":"smoke.x","columns":["id",null],"limit":1}`} {
		for _, action := range []string{"tables", "select"} {
			if _, err := decodeQueryReadRequest([]byte(raw), action); err == nil {
				t.Fatalf("accepted %s", raw)
			}
		}
	}
	request := queryReadTestRequest("tables")
	if _, err := decodeQueryReadRequest(request.Request, "describe"); err == nil {
		t.Fatal("action mismatch accepted")
	}
	if _, err := decodeQueryReadRequest(bytes.Repeat([]byte(" "), 4097), "tables"); err == nil {
		t.Fatal("oversize accepted")
	}
}
func TestQueryReadRootAndFixedChildPrivacy(t *testing.T) {
	root := queryReadTestRoot(t)
	request := queryReadTestRequest("select")
	request.Root = root
	t.Setenv("PYTHONPATH", "private-path")
	t.Setenv("PRIVATE_TOKEN", "private-secret")
	t.Setenv("HTTP_PROXY", "private-proxy")
	kubeconfig := filepath.Join(root, "kubeconfig")
	t.Setenv("KUBECONFIG", kubeconfig)
	before := strings.Join(os.Environ(), "\n")
	command, err := queryReadCommand(request)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join(root, dataPython), "-I", "-B", filepath.Join(root, queryReadScript), "select", "--evidence-token", request.Token, "--request-stdin"}
	if strings.Join(command.Args, "\n") != strings.Join(want, "\n") || command.Dir != root || !command.SysProcAttr.Setpgid {
		t.Fatal("command differs")
	}
	raw, _ := io.ReadAll(command.Stdin)
	if !bytes.Equal(raw, request.Request) {
		t.Fatal("stdin differs")
	}
	for _, entry := range command.Env {
		if strings.Contains(entry, "private-") {
			t.Fatal("ambient secret inherited")
		}
	}
	if !strings.Contains("\n"+strings.Join(command.Env, "\n")+"\n", "\nKUBECONFIG="+kubeconfig+"\n") || strings.Join(os.Environ(), "\n") != before {
		t.Fatal("KUBECONFIG forwarding changed the parent environment or lost its fixed path")
	}
	for _, mutate := range []func(*dataChildRequest){func(r *dataChildRequest) { r.Case = "retained-fixture" }, func(r *dataChildRequest) { r.Action = "run" }, func(r *dataChildRequest) { r.Token = "../escape" }, func(r *dataChildRequest) { r.Root = filepath.Dir(root) }} {
		bad := request
		mutate(&bad)
		if _, err := queryReadCommand(bad); err == nil {
			t.Fatal("invalid invocation accepted")
		}
	}
	path := filepath.Join(root, queryReadScript)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "Makefile"), path); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveQueryReadRoot(root); err == nil {
		t.Fatal("symlink script accepted")
	}
}
func TestQueryReadMissingEnvironmentIsNotInstalled(t *testing.T) {
	for _, relative := range []string{queryReadScript, dataPython, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		root := queryReadTestRoot(t)
		if err := os.Remove(filepath.Join(root, relative)); err != nil {
			t.Fatal(err)
		}
		if _, err := resolveQueryReadRoot(root); err == nil {
			t.Fatal("missing environment accepted")
		}
	}
}
func TestQueryReadRegisteredAlongsideLegacyCommands(t *testing.T) {
	parent := newQueryCommand(dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("help invoked child")
		return dataChildResult{}, nil
	}))
	for _, name := range []string{"run", "status", "tables", "describe", "select"} {
		command, _, err := parent.Find([]string{name})
		if err != nil || command == parent {
			t.Fatalf("missing %s", name)
		}
	}
	for _, action := range []string{"tables", "describe", "select"} {
		out, err := executeQueryRead(t, nil, action, "--help")
		if err != nil || out == "" {
			t.Fatal("help unavailable")
		}
	}
}
func TestQueryReadProtocolRejectsExtraFieldsAndFalseSuccess(t *testing.T) {
	request := queryReadTestRequest("select")
	good := queryReadTestLines(request, "passed")[1]
	if _, err := decodeQueryReadEvent(good, request); err != nil {
		t.Fatal(err)
	}
	var original map[string]any
	_ = json.Unmarshal(good, &original)
	for key, bad := range map[string]any{"raw_sql": "private", "command": "select", "schema_version": 1.0 + 0.5, "event": "other", "evidence_token": strings.Repeat("b", 32), "evidence": "/private/file", "result": "healthy", "cleanup_verified": false, "error_code": "ERROR", "observation": nil} {
		copy := map[string]any{}
		for k, v := range original {
			copy[k] = v
		}
		copy[key] = bad
		raw, _ := json.Marshal(copy)
		if _, err := decodeQueryReadEvent(raw, request); err == nil {
			t.Fatalf("accepted %s", key)
		}
	}
	for _, key := range []string{"schema_version", "cleanup_verified", "evidence", "error_code", "observation"} {
		copy := map[string]any{}
		for k, v := range original {
			copy[k] = v
		}
		delete(copy, key)
		raw, _ := json.Marshal(copy)
		if _, err := decodeQueryReadEvent(raw, request); err == nil {
			t.Fatalf("missing %s", key)
		}
	}
	for _, raw := range [][]byte{append(good, good...), []byte(`{"schema_version":1,"schema_version":1}`), bytes.Repeat([]byte("x"), queryReadResponseLimit+1), {0xff}} {
		if _, err := decodeQueryReadEvent(raw, request); err == nil {
			t.Fatal("malformed accepted")
		}
	}
}
func TestQueryReadObservationBindingRejectsDriftAndPrecisionLoss(t *testing.T) {
	for action, mutations := range map[string]map[string]any{
		"tables":   {"schema": "local_experiment", "tables": []string{"smoke.z", "smoke.a"}, "complete": false},
		"describe": {"schema_id": 1.5, "current_snapshot_id": int64(9211570477933275546), "format_version": 3, "schema_sha256": "bad", "table": "other"},
		"select":   {"snapshot_id": int64(9211570477933275546), "row_count": 1, "requested_limit": 3, "rows_sha256": strings.Repeat("0", 64), "fetched_row_count": 1, "truncated": false, "table": "iceberg.smoke.other"},
	} {
		request, _ := decodeQueryReadRequest(queryReadTestRequest(action).Request, action)
		original := queryReadTestObservation(action)
		for key, bad := range mutations {
			copy := map[string]any{}
			for k, v := range original {
				copy[k] = v
			}
			copy[key] = bad
			raw, _ := json.Marshal(copy)
			if queryReadObservation(raw, request) {
				t.Fatalf("%s accepted %s", action, key)
			}
		}
	}
}
func TestQueryReadTypedCellsAndNumericLimits(t *testing.T) {
	cases := []struct {
		kind      string
		good, bad any
	}{
		{"integer", "2147483647", "2147483648"}, {"bigint", "-9223372036854775808", "-9223372036854775809"}, {"bigint", "1", 1}, {"bigint", "0", "-0"}, {"boolean", true, "true"}, {"date", "2024-02-29", "2023-02-29"}, {"varchar", "雪", strings.Repeat("a", 4097)}, {"decimal(5,2)", "123.45", "1234.56"}, {"decimal(5,2)", "0.00", "-0.00"}, {"decimal(5,2)", "1.20", "1.2"}, {"double", "1.0", "1"}, {"double", "1e+16", "NaN"}, {"double", "0.0001", "1e-04"}, {"real", "1e+38", "1e+39"},
	}
	for _, c := range cases {
		column := map[string]any{"name": "x", "type": c.kind, "nullable": false}
		if !queryReadCell(map[string]any{"type": c.kind, "value": c.good}, column) {
			t.Fatalf("valid %s %v", c.kind, c.good)
		}
		if queryReadCell(map[string]any{"type": c.kind, "value": c.bad}, column) {
			t.Fatalf("bad %s %v", c.kind, c.bad)
		}
		if queryReadCell(map[string]any{"type": c.kind, "value": nil}, column) {
			t.Fatal("nonnullable null")
		}
		column["nullable"] = true
		if !queryReadCell(map[string]any{"type": c.kind, "value": nil}, column) {
			t.Fatal("nullable null rejected")
		}
	}
	for _, bad := range []any{true, 1.0, json.Number("1.0"), json.Number("1e0")} {
		if _, ok := queryReadInt(bad, 0, 100); ok {
			t.Fatal("noninteger accepted")
		}
	}
	for _, bad := range []any{true, 1.0, "01", "9223372036854775808", "-1"} {
		if queryReadSnapshot(bad, false) {
			t.Fatal("bad snapshot accepted")
		}
	}
}
func TestQueryReadFinalWaitsForMatchingExitAndCompleteStream(t *testing.T) {
	request := queryReadTestRequest("tables")
	for _, mode := range []string{"exit", "missing-allocated", "missing-final", "extra", "double-allocated", "unconfirmed", "protocol", "output"} {
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			lines := queryReadTestLines(r, "passed")
			result := dataChildResult{Started: true, PID: 42, ExitCode: 0}
			switch mode {
			case "exit":
				result.ExitCode = 1
			case "missing-allocated":
				lines = lines[1:]
			case "missing-final":
				lines = lines[:1]
			case "extra":
				lines = append(lines, lines[1])
			case "double-allocated":
				lines = append([][]byte{lines[0]}, lines...)
			case "unconfirmed":
				result.Unconfirmed = true
			case "protocol":
				lines = append(lines, []byte("private raw"))
			}
			for _, line := range lines {
				if err := emit(line); err != nil {
					return result, err
				}
			}
			return result, nil
		})
		var out bytes.Buffer
		var writer io.Writer = &out
		if mode == "output" {
			writer = queryReadBrokenWriter{}
		}
		err := runQueryReadCommand(context.Background(), child, request, writer)
		if err == nil || strings.Contains(out.String(), `"result":"passed"`) || strings.Contains(err.Error(), "private raw") {
			t.Fatalf("%s %v %s", mode, err, &out)
		}
	}
}

type queryReadBrokenWriter struct{}

func (queryReadBrokenWriter) Write([]byte) (int, error) {
	return 0, errors.New("private write failure")
}
func TestQueryReadFailureAndCancellationBeforeAllocation(t *testing.T) {
	request := queryReadTestRequest("tables")
	for _, c := range []struct {
		outcome string
		exit    int
	}{{"failed", 1}, {"interrupted", 130}, {"interrupted", 143}} {
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			err := emit(queryReadTestLines(r, c.outcome)[1])
			return dataChildResult{Started: true, PID: 42, ExitCode: c.exit}, err
		})
		var out bytes.Buffer
		err := runQueryReadCommand(context.Background(), child, request, &out)
		if ExitCode(err) != c.exit || !strings.Contains(out.String(), `"result":"`+c.outcome+`"`) {
			t.Fatalf("%+v: %v %s", c, err, &out)
		}
	}
}
func TestQueryReadDeadlineAndUserCancelRemainDistinct(t *testing.T) {
	for _, cancelled := range []bool{false, true} {
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			err := errors.New("deadline")
			if cancelled {
				err = errDataInterrupted
			}
			return dataChildResult{Started: true, PID: 42, ExitCode: 130, Unconfirmed: true, Interrupted: cancelled}, err
		})
		var out bytes.Buffer
		err := runQueryReadCommand(context.Background(), child, queryReadTestRequest("tables"), &out)
		want := 1
		if cancelled {
			want = 130
		}
		if ExitCode(err) != want || out.Len() != 0 {
			t.Fatal("cancellation classification differs")
		}
	}
}

func TestQueryReadTransportHelper(t *testing.T) {
	if os.Getenv("QUERY_READ_HELPER") != "1" {
		return
	}
	n, _ := strconv.Atoi(os.Getenv("QUERY_READ_HELPER_SIZE"))
	fmt.Fprint(os.Stdout, strings.Repeat("a", n)+"\n")
	fmt.Fprint(os.Stderr, "PRIVATE STDERR")
	os.Exit(0)
}
func queryReadTransportProcess(size int) *exec.Cmd {
	cmd := exec.Command(os.Args[0], "-test.run=^TestQueryReadTransportHelper$")
	cmd.Env = append(os.Environ(), "QUERY_READ_HELPER=1", "QUERY_READ_HELPER_SIZE="+strconv.Itoa(size))
	return cmd
}
func TestQueryReadTransportBoundIsLargerWithoutChangingDataDefault(t *testing.T) {
	for _, c := range []struct {
		size         int
		reader, pass bool
	}{{4096, false, true}, {4097, false, false}, {4097, true, true}, {queryReadResponseLimit, true, true}, {queryReadResponseLimit + 1, true, false}} {
		build := func(dataChildRequest) (*exec.Cmd, error) { return queryReadTransportProcess(c.size), nil }
		var runner dataChildRunner = execDataChild{command: build, grace: time.Second}
		if c.reader {
			runner = execQueryReadChild{command: build, grace: time.Second}
		}
		seen := 0
		result, err := runner.Run(context.Background(), dataChildRequest{}, func(line []byte) error { seen = len(line); return nil })
		if c.pass && (err != nil || seen != c.size || result.ExitCode != 0) {
			t.Fatalf("%+v: %v %+v %d", c, err, result, seen)
		}
		if !c.pass && (err == nil || seen != 0) {
			t.Fatalf("oversize exposed: %+v %v %d", c, err, seen)
		}
	}
}
func TestQueryReadTransportPreservesOwnedCleanupWithoutKill(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "cleaned")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	runner := execQueryReadChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess("cancel", marker), nil }}
	result, err := runner.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if !errors.Is(err, errDataInterrupted) || !result.Interrupted || result.ExitCode != 130 {
		t.Fatalf("cleanup failed %v %+v", err, result)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatal("owned cleanup marker missing")
	}
}

func TestQueryReadRealPythonOwnerProtocolWithFakeRuntime(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	harness := `
import contextlib, pathlib, sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
import test_query_read_run as F
R=F.R
R.STATE=pathlib.Path(sys.argv[2]).resolve()/'evidence'
state={'order':[]}
if sys.argv[4]=='drift': state['head_drift']=True
if sys.argv[4]=='interrupt': state['query_error']=KeyboardInterrupt()
worker,control,catalog=F.Client(state),F.Client(state),F.Catalog(state)
@contextlib.contextmanager
def runtime(token,progress):
    progress({'state':'running','cleanup':{'verified':False}})
    try: yield worker,control,catalog
    finally: progress({'state':'verified','cleanup':{'verified':True}})
def invoke(token,request,emit):
    return R.invoke(token,request,emit,runtime_factory=runtime,drain=lambda *_:{'verified':True,'active':[]})
raise SystemExit(R.main([sys.argv[3],'--evidence-token',sys.argv[5],'--request-stdin'],invoker=invoke))
`
	for _, c := range []struct {
		action, mode string
		exit         int
	}{{"tables", "passed", 0}, {"describe", "passed", 0}, {"select", "passed", 0}, {"select", "drift", 1}, {"select", "interrupt", 130}} {
		t.Run(c.action+"-"+c.mode, func(t *testing.T) {
			output := t.TempDir()
			if err := os.Chmod(output, 0700); err != nil {
				t.Fatal(err)
			}
			request := queryReadTestRequest(c.action)
			runner := execQueryReadChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, c.action, c.mode, r.Token)
				cmd.Dir = root
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQueryReadCommand(context.Background(), runner, request, &out)
			if ExitCode(err) != c.exit {
				t.Fatalf("%+v: %v %s", c, err, &out)
			}
			if c.exit == 0 && !strings.Contains(out.String(), `"result":"passed"`) {
				t.Fatal("passed missing")
			}
			if c.exit != 0 && strings.Contains(out.String(), `"result":"passed"`) {
				t.Fatal("failure became passed")
			}
			summary := filepath.Join(output, "evidence", request.Token, "summary.json")
			raw, e := os.ReadFile(summary)
			if e != nil {
				t.Fatal(e)
			}
			for _, sentinel := range []string{"private-row-sentinel", "second-row", "never-persist-credential", "SELECT "} {
				if bytes.Contains(raw, []byte(sentinel)) {
					t.Fatalf("saved raw %s", sentinel)
				}
			}
			info, e := os.Stat(summary)
			if e != nil || info.Mode().Perm() != 0600 {
				t.Fatal("receipt not private")
			}
			if c.action == "select" && c.mode == "passed" && !strings.Contains(out.String(), "private-row-sentinel") {
				t.Fatal("explicit result rows unavailable")
			}
		})
	}
}

func TestQueryReadPurePythonScalarProjectionAgreement(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	script := `
import json,sys
sys.path.insert(0,sys.argv[1])
from tools.local_experiment.query_read import contract as C
values=[('bigint',-(2**63)),('bigint',2**63-1),('decimal(38,6)','12345678901234567890123456789012.123456'),('decimal(5,2)','-0'),('boolean',True),('varchar','雪☃<>&\n😀'),('varchar','\x7f'),('date','2024-02-29'),('varchar',None)]
values += [('double',v) for v in (0.0,-0.0,1.0,1e-5,1e-4,1e15,1e16,1e20,1e-307,1.2345678901234567)]
for kind,value in values:
 spec={'name':'x','type':kind,'nullable':True}
 cell=C._cell(value,spec)
 print(json.dumps({'column':spec,'cell':cell,'canonical':C.canonical([[cell]]).decode()},ensure_ascii=True))
`
	command := exec.Command(python, "-I", "-B", "-c", script, root)
	command.Env = []string{"PATH=/usr/bin:/bin"}
	raw, err := command.Output()
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range bytes.Split(bytes.TrimSpace(raw), []byte{'\n'}) {
		var value struct {
			Column    map[string]any `json:"column"`
			Cell      map[string]any `json:"cell"`
			Canonical string         `json:"canonical"`
		}
		if err := json.Unmarshal(line, &value); err != nil {
			t.Fatal(err)
		}
		if !queryReadCell(value.Cell, value.Column) {
			t.Fatalf("Python scalar rejected: %s", line)
		}
		if string(queryReadCanonical([]any{[]any{value.Cell}})) != value.Canonical {
			t.Fatalf("canonical row differs: %s", line)
		}
	}
}

func TestQueryReadNestedJSONRejectsDuplicatesDepthAndShortDelivery(t *testing.T) {
	for _, raw := range []string{`{"rows":[[{"type":"bigint","value":"1","value":"2"}]]}`, `{"a":[[[[[[[0]]]]]]]}`, `{"value":NaN}`, `{} {}`} {
		if uniqueQueryReadJSON([]byte(raw)) == nil {
			t.Fatal("invalid nested JSON accepted")
		}
	}
	request := queryReadTestRequest("tables")
	child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		for _, line := range queryReadTestLines(r, "passed") {
			if err := emit(line); err != nil {
				return dataChildResult{Started: true, PID: 42, ExitCode: 0}, err
			}
		}
		return dataChildResult{Started: true, PID: 42, ExitCode: 0}, nil
	})
	if err := runQueryReadCommand(context.Background(), child, request, queryReadShortWriter{}); err == nil {
		t.Fatal("short delivery accepted")
	}
}

type queryReadShortWriter struct{}

func (queryReadShortWriter) Write(raw []byte) (int, error) { return len(raw) - 1, nil }

func TestQueryReadErrorCodesMatchPythonAllowlist(t *testing.T) {
	request := queryReadTestRequest("tables")
	var event map[string]any
	_ = json.Unmarshal(queryReadTestLines(request, "failed")[1], &event)
	for _, bad := range []string{"UNTRUSTED_UPPERCASE_TEXT", "private body", "", strings.Repeat("X", 101)} {
		event["error_code"] = bad
		raw, _ := json.Marshal(event)
		if _, err := decodeQueryReadEvent(raw, request); err == nil {
			t.Fatal("untrusted error code accepted")
		}
	}
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	script := `import json,sys;sys.path.insert(0,sys.argv[1]);from tools.local_experiment.query_read import run as R;print(json.dumps(sorted(R.PUBLIC_CODES)))`
	command := exec.Command(python, "-I", "-B", "-c", script, root)
	command.Env = []string{"PATH=/usr/bin:/bin"}
	raw, err := command.Output()
	if err != nil {
		t.Fatal(err)
	}
	var codes []string
	if err := json.Unmarshal(raw, &codes); err != nil {
		t.Fatal(err)
	}
	if len(codes) != len(strings.Fields(queryReadPublicCodes)) {
		t.Fatal("code count differs")
	}
	for _, code := range codes {
		if !queryReadErrorCode(code) {
			t.Fatalf("Python code missing: %s", code)
		}
	}
}
