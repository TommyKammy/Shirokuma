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
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

func queryDiagnoseTestRoot(t *testing.T) string {
	t.Helper()
	root := dataTestRoot(t)
	path := filepath.Join(root, queryDiagnoseScript)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}
func queryDiagnoseTestRequest() dataChildRequest {
	raw, _ := json.Marshal(queryDiagnoseRequest{ReadToken: strings.Repeat("b", 32)})
	return dataChildRequest{Action: "diagnose", Token: testWarehouseSession, Request: raw}
}
func queryDiagnoseTestSummary(readToken string) map[string]any {
	return map[string]any{"schema_version": 1, "read_token": readToken, "query_id": "20260918_010203_01234_abcde", "history": "observed", "findings": []string{"PLAN_ESTIMATES_INCOMPLETE", "RESULT_LIMIT_REACHED"}, "next_steps": []string{"MEASURE_BEFORE_OPTIMIZING", "REVIEW_READER_LIMIT", "REVIEW_TABLE_STATISTICS"}, "root_cause": "undetermined", "performance_gain": "unmeasured", "original_select_reexecuted": false, "model_calls": 1}
}
func queryDiagnoseTestLines(request dataChildRequest, outcome string) [][]byte {
	input, _ := decodeQueryDiagnoseRequest(request)
	allocated, _ := json.Marshal(map[string]any{"schema_version": 1, "event": "allocated", "command": "query diagnose", "evidence_token": request.Token})
	final := map[string]any{"schema_version": 1, "event": "result", "command": "query diagnose", "evidence_token": request.Token, "result": outcome, "observation": nil, "error_code": "DIAGNOSE_FAILED", "cleanup_verified": false, "evidence": "tmp/local-experiment/query-diagnose/" + request.Token + "/summary.json"}
	if outcome == "passed" {
		final["observation"] = queryDiagnoseTestSummary(input.ReadToken)
		final["error_code"] = nil
		final["cleanup_verified"] = true
	}
	if outcome == "interrupted" {
		final["error_code"] = "INTERRUPTED"
	}
	raw, _ := json.Marshal(final)
	return [][]byte{allocated, raw}
}
func executeQueryDiagnose(t *testing.T, child dataChildRunner, args ...string) (string, error) {
	t.Helper()
	root, profile := "", localExperimentProfile
	parent := &cobra.Command{Use: "query", SilenceErrors: true, SilenceUsage: true}
	parent.PersistentFlags().StringVar(&root, "repo-root", "", "")
	parent.PersistentFlags().StringVar(&profile, "profile", profile, "")
	parent.AddCommand(newQueryDiagnoseCommand(child, &root, &profile))
	var out bytes.Buffer
	parent.SetOut(&out)
	parent.SetErr(io.Discard)
	parent.SetArgs(args)
	err := parent.Execute()
	return out.String(), err
}

func TestQueryDiagnoseUsesDistinctEvidenceAndReadIdentities(t *testing.T) {
	root := queryDiagnoseTestRoot(t)
	readToken := strings.Repeat("b", 32)
	calls := 0
	child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		calls++
		input, err := decodeQueryDiagnoseRequest(r)
		if err != nil || input.ReadToken != readToken || r.Token == readToken || r.Root != root || r.Case != "" {
			t.Fatal("identity/input mismatch")
		}
		for _, line := range queryDiagnoseTestLines(r, "passed") {
			if err := emit(line); err != nil {
				return dataChildResult{Started: true}, err
			}
		}
		return dataChildResult{Started: true, PID: 42, ExitCode: 0}, nil
	})
	out, err := executeQueryDiagnose(t, child, "diagnose", "--read-token", readToken, "--repo-root", root)
	if err != nil || calls != 1 || !strings.Contains(out, `"result":"passed"`) {
		t.Fatalf("%v %s", err, out)
	}
}
func TestQueryDiagnoseInvalidArgumentsNeverReachChild(t *testing.T) {
	root := queryDiagnoseTestRoot(t)
	token := strings.Repeat("b", 32)
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("invalid input reached child")
		return dataChildResult{}, nil
	})
	for _, args := range [][]string{{"diagnose"}, {"diagnose", "--read-token", "../escape"}, {"diagnose", "--read-token", strings.Repeat("B", 32)}, {"diagnose", "--read-token", token, "private-extra"}, {"diagnose", "--read-token", token, "--profile", "production"}, {"diagnose", "--read-token", token, "--sql", "PRIVATE_SQL"}, {"diagnose", "--read-token", token, "--request-stdin"}, {"diagnose", "--read-token", token, "--limit", "1"}} {
		out, err := executeQueryDiagnose(t, child, append(args, "--repo-root", root)...)
		if err == nil || out != "" || strings.Contains(err.Error(), "PRIVATE_SQL") || strings.Contains(err.Error(), "private-extra") {
			t.Fatalf("%v: %v %s", args, err, out)
		}
	}
}
func TestQueryDiagnoseInputExactShapeAndNoIdentityReuse(t *testing.T) {
	request := queryDiagnoseTestRequest()
	for _, raw := range []string{`{"read_token":null}`, `{"read_token":1}`, `{"read_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","other":true}`, `{"read_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","read_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}`, `{"read_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"} {}`, `["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]`, strings.Repeat(" ", 4097)} {
		bad := request
		bad.Request = []byte(raw)
		if _, err := decodeQueryDiagnoseRequest(bad); err == nil {
			t.Fatalf("invalid request %s", raw)
		}
	}
	for _, mutate := range []func(*dataChildRequest){func(r *dataChildRequest) { r.Action = "select" }, func(r *dataChildRequest) { r.Case = "case" }, func(r *dataChildRequest) { r.Token = strings.Repeat("b", 32) }, func(r *dataChildRequest) { r.Token = "../bad" }, func(r *dataChildRequest) { r.Request = []byte{0xff} }} {
		bad := request
		mutate(&bad)
		if _, err := decodeQueryDiagnoseRequest(bad); err == nil {
			t.Fatal("invalid invocation accepted")
		}
	}
}
func TestQueryDiagnoseFixedCommandStdinAndFilteredEnvironment(t *testing.T) {
	root := queryDiagnoseTestRoot(t)
	request := queryDiagnoseTestRequest()
	request.Root = root
	t.Setenv("PYTHONPATH", "PRIVATE_PATH")
	t.Setenv("TOKEN", "PRIVATE_TOKEN")
	t.Setenv("HTTP_PROXY", "PRIVATE_PROXY")
	command, err := queryDiagnoseCommand(request)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join(root, dataPython), "-I", "-B", filepath.Join(root, queryDiagnoseScript), "diagnose", "--evidence-token", request.Token, "--request-stdin"}
	if strings.Join(command.Args, "\n") != strings.Join(want, "\n") || command.Dir != root || !command.SysProcAttr.Setpgid {
		t.Fatal("fixed command differs")
	}
	raw, _ := io.ReadAll(command.Stdin)
	if !bytes.Equal(raw, request.Request) {
		t.Fatal("stdin differs")
	}
	for _, entry := range command.Env {
		if strings.Contains(entry, "PRIVATE_") {
			t.Fatal("ambient configuration leaked")
		}
	}
	if strings.Contains(strings.Join(command.Args, " "), strings.Repeat("b", 32)) {
		t.Fatal("source token put in argv")
	}
}
func TestQueryDiagnoseMissingOrRedirectedEnvironmentNeverInstalled(t *testing.T) {
	for _, rel := range []string{queryDiagnoseScript, dataPython, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		root := queryDiagnoseTestRoot(t)
		if err := os.Remove(filepath.Join(root, rel)); err != nil {
			t.Fatal(err)
		}
		if _, err := resolveQueryDiagnoseRoot(root); err == nil {
			t.Fatal("missing environment accepted")
		}
	}
	root := queryDiagnoseTestRoot(t)
	path := filepath.Join(root, queryDiagnoseScript)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "Makefile"), path); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveQueryDiagnoseRoot(root); err == nil {
		t.Fatal("redirected script accepted")
	}
}
func TestQueryDiagnoseRegistrationKeepsLegacyAndReaderCommands(t *testing.T) {
	parent := newQueryCommand(nil)
	for _, name := range []string{"run", "status", "tables", "describe", "select", "diagnose"} {
		command, _, err := parent.Find([]string{name})
		if err != nil || command == parent {
			t.Fatalf("missing %s", name)
		}
	}
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("help invoked child")
		return dataChildResult{}, nil
	})
	if out, err := executeQueryDiagnose(t, child, "diagnose", "--help"); err != nil || out == "" {
		t.Fatal("help unavailable")
	}
}
func TestQueryDiagnoseStrictEnvelopeRejectsFalseSuccessAndLeaks(t *testing.T) {
	request := queryDiagnoseTestRequest()
	good := queryDiagnoseTestLines(request, "passed")[1]
	if _, err := decodeQueryDiagnoseEvent(good, request); err != nil {
		t.Fatal(err)
	}
	var original map[string]any
	_ = json.Unmarshal(good, &original)
	for key, bad := range map[string]any{"raw_sql": "PRIVATE_SQL", "schema_version": 1.1, "event": "other", "command": "diagnose", "evidence_token": strings.Repeat("c", 32), "result": "healthy", "observation": nil, "error_code": "DIAGNOSE_FAILED", "cleanup_verified": false, "evidence": "/private/file"} {
		altered := map[string]any{}
		for k, v := range original {
			altered[k] = v
		}
		altered[key] = bad
		raw, _ := json.Marshal(altered)
		if _, err := decodeQueryDiagnoseEvent(raw, request); err == nil {
			t.Fatalf("accepted %s", key)
		}
	}
	for _, key := range []string{"schema_version", "cleanup_verified", "observation", "error_code", "evidence"} {
		altered := map[string]any{}
		for k, v := range original {
			altered[k] = v
		}
		delete(altered, key)
		raw, _ := json.Marshal(altered)
		if _, err := decodeQueryDiagnoseEvent(raw, request); err == nil {
			t.Fatalf("missing %s", key)
		}
	}
	for _, raw := range [][]byte{append(good, good...), bytes.Repeat([]byte("x"), 4097), {0xff}, []byte(`{"event":"result","event":"result"}`)} {
		if _, err := decodeQueryDiagnoseEvent(raw, request); err == nil {
			t.Fatal("malformed envelope accepted")
		}
	}
}
func TestQueryDiagnoseSummaryStrictTypesAndSourceBinding(t *testing.T) {
	token := strings.Repeat("b", 32)
	original := queryDiagnoseTestSummary(token)
	for key, bad := range map[string]any{"schema_version": true, "read_token": strings.Repeat("c", 32), "query_id": "sql-text", "history": "healthy", "findings": nil, "next_steps": nil, "root_cause": "determined", "performance_gain": "improved", "original_select_reexecuted": true, "model_calls": 0, "model_calls_bad": 1, "table": "private-table"} {
		altered := map[string]any{}
		for k, v := range original {
			altered[k] = v
		}
		altered[key] = bad
		raw, _ := json.Marshal(altered)
		if validQueryDiagnoseSummary(raw, token) {
			t.Fatalf("accepted %s", key)
		}
	}
	for _, key := range []string{"schema_version", "read_token", "query_id", "history", "findings", "next_steps", "root_cause", "performance_gain", "original_select_reexecuted", "model_calls"} {
		altered := map[string]any{}
		for k, v := range original {
			altered[k] = v
		}
		delete(altered, key)
		raw, _ := json.Marshal(altered)
		if validQueryDiagnoseSummary(raw, token) {
			t.Fatalf("missing %s", key)
		}
	}
}
func TestQueryDiagnoseFindingStepAndHistoryRelationships(t *testing.T) {
	token := strings.Repeat("b", 32)
	good := []struct {
		history         string
		findings, steps []string
	}{
		{"observed", []string{}, []string{"MEASURE_BEFORE_OPTIMIZING"}},
		{"not-retained", []string{"HISTORY_NOT_RETAINED"}, []string{"CAPTURE_HISTORY_PROMPTLY", "MEASURE_BEFORE_OPTIMIZING"}},
		{"observed", []string{"PLAN_ESTIMATES_INCOMPLETE", "TABLE_STATISTICS_INCOMPLETE"}, []string{"MEASURE_BEFORE_OPTIMIZING", "REVIEW_TABLE_STATISTICS"}},
		{"observed", []string{"HISTORY_TIMING_INCOMPLETE", "PLAN_ESTIMATES_INCOMPLETE", "QUEUE_TIME_OBSERVED", "RESULT_LIMIT_REACHED", "TABLE_STATISTICS_INCOMPLETE"}, []string{"CHECK_HISTORY_TIMING", "MEASURE_BEFORE_OPTIMIZING", "REVIEW_QUEUE_TIMING", "REVIEW_READER_LIMIT", "REVIEW_TABLE_STATISTICS"}},
	}
	for _, c := range good {
		value := queryDiagnoseTestSummary(token)
		value["history"] = c.history
		value["findings"] = c.findings
		value["next_steps"] = c.steps
		raw, _ := json.Marshal(value)
		if !validQueryDiagnoseSummary(raw, token) {
			t.Fatalf("valid relationship rejected: %s", raw)
		}
	}
	for _, c := range []struct {
		history         string
		findings, steps []string
	}{
		{"observed", []string{"HISTORY_NOT_RETAINED"}, []string{"CAPTURE_HISTORY_PROMPTLY", "MEASURE_BEFORE_OPTIMIZING"}},
		{"not-retained", []string{}, []string{"MEASURE_BEFORE_OPTIMIZING"}},
		{"not-retained", []string{"HISTORY_NOT_RETAINED", "QUEUE_TIME_OBSERVED"}, []string{"CAPTURE_HISTORY_PROMPTLY", "MEASURE_BEFORE_OPTIMIZING", "REVIEW_QUEUE_TIMING"}},
		{"observed", []string{"PLAN_ESTIMATES_INCOMPLETE"}, []string{"MEASURE_BEFORE_OPTIMIZING"}},
		{"observed", []string{}, []string{}},
		{"observed", []string{}, []string{"MEASURE_BEFORE_OPTIMIZING", "REVIEW_QUEUE_TIMING"}},
		{"observed", []string{"RESULT_LIMIT_REACHED", "PLAN_ESTIMATES_INCOMPLETE"}, []string{"MEASURE_BEFORE_OPTIMIZING", "REVIEW_READER_LIMIT", "REVIEW_TABLE_STATISTICS"}},
		{"observed", []string{"PLAN_ESTIMATES_INCOMPLETE", "PLAN_ESTIMATES_INCOMPLETE"}, []string{"MEASURE_BEFORE_OPTIMIZING", "REVIEW_TABLE_STATISTICS"}},
		{"observed", []string{"ARBITRARY_FINDING"}, []string{"MEASURE_BEFORE_OPTIMIZING"}},
	} {
		value := queryDiagnoseTestSummary(token)
		value["history"] = c.history
		value["findings"] = c.findings
		value["next_steps"] = c.steps
		raw, _ := json.Marshal(value)
		if validQueryDiagnoseSummary(raw, token) {
			t.Fatalf("invalid relationship accepted: %s", raw)
		}
	}
}
func TestQueryDiagnoseFailureErrorAllowlistAndPreallocationCancellation(t *testing.T) {
	request := queryDiagnoseTestRequest()
	for _, c := range []struct {
		result string
		exit   int
	}{{"failed", 1}, {"interrupted", 130}, {"interrupted", 143}} {
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			err := emit(queryDiagnoseTestLines(r, c.result)[1])
			return dataChildResult{Started: true, PID: 42, ExitCode: c.exit}, err
		})
		var out bytes.Buffer
		err := runQueryDiagnoseCommand(context.Background(), child, request, &out)
		if ExitCode(err) != c.exit || !strings.Contains(out.String(), `"result":"`+c.result+`"`) {
			t.Fatalf("%+v: %v %s", c, err, &out)
		}
	}
	var value map[string]any
	_ = json.Unmarshal(queryDiagnoseTestLines(request, "failed")[1], &value)
	for _, bad := range []string{"PRIVATE_SECRET", "UNKNOWN_CODE", "source raw body", ""} {
		value["error_code"] = bad
		raw, _ := json.Marshal(value)
		if _, err := decodeQueryDiagnoseEvent(raw, request); err == nil {
			t.Fatal("untrusted error exposed")
		}
	}
}
func TestQueryDiagnoseSuccessWaitsForMatchingExitAndCompleteStream(t *testing.T) {
	request := queryDiagnoseTestRequest()
	for _, mode := range []string{"exit", "unconfirmed", "missing-allocated", "missing-final", "duplicate-allocated", "extra", "protocol"} {
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			result := dataChildResult{Started: true, PID: 42, ExitCode: 0}
			lines := queryDiagnoseTestLines(r, "passed")
			switch mode {
			case "exit":
				result.ExitCode = 1
			case "unconfirmed":
				result.Unconfirmed = true
			case "missing-allocated":
				lines = lines[1:]
			case "missing-final":
				lines = lines[:1]
			case "duplicate-allocated":
				lines = append([][]byte{lines[0]}, lines...)
			case "extra":
				lines = append(lines, lines[1])
			case "protocol":
				lines = append(lines, []byte("PRIVATE RAW ERROR"))
			}
			for _, line := range lines {
				if err := emit(line); err != nil {
					return result, err
				}
			}
			return result, nil
		})
		var out bytes.Buffer
		err := runQueryDiagnoseCommand(context.Background(), child, request, &out)
		if err == nil || strings.Contains(out.String(), `"result":"passed"`) || strings.Contains(err.Error(), "PRIVATE RAW ERROR") {
			t.Fatalf("%s: %v %s", mode, err, &out)
		}
	}
}
func TestQueryDiagnoseOutputFailureAndDeadlineNeverReleaseSuccess(t *testing.T) {
	request := queryDiagnoseTestRequest()
	child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		for _, line := range queryDiagnoseTestLines(r, "passed") {
			if err := emit(line); err != nil {
				return dataChildResult{Started: true, PID: 42, ExitCode: 0}, err
			}
		}
		return dataChildResult{Started: true, PID: 42, ExitCode: 0}, nil
	})
	for _, writer := range []io.Writer{queryReadShortWriter{}, queryReadBrokenWriter{}} {
		if err := runQueryDiagnoseCommand(context.Background(), child, request, writer); err == nil {
			t.Fatal("delivery failure accepted")
		}
	}
	for _, cancelled := range []bool{false, true} {
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			err := errors.New("deadline")
			if cancelled {
				err = errDataInterrupted
			}
			return dataChildResult{Started: true, PID: 42, ExitCode: 130, Unconfirmed: true, Interrupted: cancelled}, err
		})
		var out bytes.Buffer
		err := runQueryDiagnoseCommand(context.Background(), child, request, &out)
		want := 1
		if cancelled {
			want = 130
		}
		if ExitCode(err) != want || out.Len() != 0 {
			t.Fatal("deadline classification differs")
		}
	}
}
func TestQueryDiagnoseTransportKeeps4096Limit(t *testing.T) {
	for _, size := range []int{4096, 4097} {
		runner := execQueryDiagnoseChild{command: func(dataChildRequest) (*exec.Cmd, error) { return queryReadTransportProcess(size), nil }, grace: time.Second}
		seen := 0
		result, err := runner.Run(context.Background(), dataChildRequest{}, func(line []byte) error { seen = len(line); return nil })
		if size == 4096 && (err != nil || seen != 4096 || result.ExitCode != 0) {
			t.Fatal("valid transport bound rejected")
		}
		if size == 4097 && (err == nil || seen != 0) {
			t.Fatal("oversize output exposed")
		}
	}
}
func TestQueryDiagnoseTransportOwnerCancellationDrainsWithoutKill(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "cleaned")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	runner := execQueryDiagnoseChild{command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess("cancel", marker), nil }, grace: time.Second}
	result, err := runner.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if !errors.Is(err, errDataInterrupted) || !result.Interrupted || result.ExitCode != 130 {
		t.Fatalf("%v %+v", err, result)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatal("owner cleanup absent")
	}
}

func TestQueryDiagnoseRealPythonOwnerWithFakeDependencies(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	harness := `
import pathlib,sys
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from tools.local_experiment.query_diagnose import run as R,contract as D
from test_query_diagnose_contract import source,projections
R.STATE=pathlib.Path(sys.argv[2]).resolve()/'diagnose'
mode=sys.argv[3]
class Lease:
    def __init__(self,token):
        self.source=source()
        assert token==self.source['read_token']
    def __enter__(self):
        if mode=='source-invalid':raise R.DiagnoseError('SOURCE_INVALID')
        return self
    def unchanged(self):pass
    def __exit__(self,*args):return False
class Model:
    def complete(self,task,evidence):
        assert task=='query_diagnose'
        if mode=='model-rejected':return {'content':{'raw':'PRIVATE_RAW_MODEL'}}
        return {'content':D.expected_diagnosis(evidence)}
class Audit:
    def __init__(self,token,output,profile,progress):self.progress=progress
    def start(self):self.progress({'verified':False})
    def phase(self,action,call,*,inference=False,validate=None):
        result=call()
        return validate(result) if validate else result
    def finish(self):self.progress({'verified':mode!='audit-failed'})
    def close(self,original):pass
def observe(source,token,progress):
    if mode=='interrupt':raise KeyboardInterrupt()
    value={'state':'verified','cleanup_verified':True,**projections(source,missing=mode=='not-retained')}
    progress(value)
    return value
def invoke(identity,read_token,emit):
    return R.invoke(identity,read_token,emit,lease_factory=Lease,environment_factory=lambda:({},Model()),observer=observe,audit_factory=Audit)
raise SystemExit(R.main(['diagnose','--evidence-token',sys.argv[4],'--request-stdin'],invoker=invoke))
`
	for _, c := range []struct {
		mode string
		exit int
		code string
	}{{"passed", 0, ""}, {"not-retained", 0, ""}, {"source-invalid", 1, "SOURCE_INVALID"}, {"model-rejected", 1, "MODEL_REJECTED"}, {"audit-failed", 1, "AUDIT_FAILED"}, {"interrupt", 130, "INTERRUPTED"}} {
		t.Run(c.mode, func(t *testing.T) {
			output := t.TempDir()
			if err := os.Chmod(output, 0700); err != nil {
				t.Fatal(err)
			}
			request := queryDiagnoseTestRequest()
			request.Request = []byte(`{"read_token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`)
			runner := execQueryDiagnoseChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, output, c.mode, r.Token)
				cmd.Env = []string{"PATH=/usr/bin:/bin"}
				cmd.Dir = root
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runQueryDiagnoseCommand(context.Background(), runner, request, &out)
			if ExitCode(err) != c.exit {
				t.Fatalf("%s: %v %s", c.mode, err, &out)
			}
			if c.exit == 0 && !strings.Contains(out.String(), `"result":"passed"`) {
				t.Fatal("success missing")
			}
			if c.exit != 0 && (strings.Contains(out.String(), `"result":"passed"`) || !strings.Contains(out.String(), c.code)) {
				t.Fatalf("failure differs: %s", &out)
			}
			raw, e := os.ReadFile(filepath.Join(output, "diagnose", request.Token, "summary.json"))
			if e != nil {
				t.Fatal(e)
			}
			if bytes.Contains(raw, []byte("PRIVATE_RAW_MODEL")) || strings.Contains(out.String(), "PRIVATE_RAW_MODEL") {
				t.Fatal("model body leaked")
			}
			var receipt map[string]any
			if err := json.Unmarshal(raw, &receipt); err != nil {
				t.Fatal(err)
			}
			if receipt["original_select_reexecuted"] != false || receipt["invocations"] != float64(1) {
				t.Fatal("owner replay/count differs")
			}
			if c.exit == 0 {
				audit, ok := receipt["audit"].(map[string]any)
				if !ok || audit["verified"] != true || receipt["cleanup_verified"] != true || receipt["model_calls"] != float64(1) {
					t.Fatal("success missing owner audit/cleanup")
				}
			}
		})
	}
}

func TestQueryDiagnosePythonErrorAndFindingContractsMatch(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	script := `import json,sys;sys.path.insert(0,sys.argv[1]);from tools.local_experiment.query_diagnose import run as R,contract as D;print(json.dumps({'codes':sorted(R.ERROR_CODES),'steps':D.FINDING_STEPS}))`
	command := exec.Command(python, "-I", "-B", "-c", script, root)
	command.Env = []string{"PATH=/usr/bin:/bin"}
	raw, err := command.Output()
	if err != nil {
		t.Fatal(err)
	}
	var value struct {
		Codes []string          `json:"codes"`
		Steps map[string]string `json:"steps"`
	}
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	if len(value.Codes) != 14 || len(value.Steps) != len(queryDiagnoseFindingSteps) {
		t.Fatal("Python contract count differs")
	}
	for _, code := range value.Codes {
		if !queryDiagnoseErrorCode(code) {
			t.Fatalf("missing error %s", code)
		}
	}
	for finding, step := range value.Steps {
		if queryDiagnoseFindingSteps[finding] != step {
			t.Fatalf("finding mapping differs: %s", finding)
		}
	}
}
