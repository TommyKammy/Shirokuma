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
)

func TestPolicyRealPythonProtocolWithFakeExternalBoundaries(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	// Keep the actual bridge, durable intent, workflow, OTel and evidence validator.
	// Only model/Kubernetes/AH/Loki transports are fake; all files are disposable.
	harness := `
import asyncio, hashlib, io, json, pathlib, sys, unittest
from types import SimpleNamespace
sys.path[:0]=[sys.argv[1]+'/tools/local_experiment/bears',sys.argv[1]+'/tests']
import policy_cli as C
import run_policy_candidate as R
from test_policy_candidate_bear import CandidateRuntimeFixture
from test_policy_candidate_observer import pod, TOKEN
from test_local_sre_llm import local_config
state=pathlib.Path(sys.argv[2]).resolve()
outcome=sys.argv[3]
class Harness(CandidateRuntimeFixture, unittest.TestCase): pass
h=Harness();h.setUp()
C.STATE=state/'intents'; C.EVIDENCE=state/'evidence'
configured_calls=0; invocations=0
async def workflow(config, output, raw):
    global invocations
    invocations+=1
    h.raw=raw
    print('PRIVATE CHILD OUTPUT')
    print('PRIVATE CHILD ERROR',file=sys.stderr)
    return await R.run(config,output,raw)
def configured():
    global configured_calls
    configured_calls+=1
    return SimpleNamespace(run=workflow),local_config()
C.configured_runner=configured
if outcome in ('denied','denied_status'):
    h.kube.respond=lambda body:h.kube.denial(body,['P01'])
if outcome=='failed': h.mismatch=True
if outcome=='cancel': h.model_error=asyncio.CancelledError('PRIVATE CANCELLATION')
def inventory():
    return {str(p.relative_to(state)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mode,p.stat().st_mtime_ns)
            for p in state.rglob('*') if p.is_file()}
if outcome not in ('not_found','invalid'): state.mkdir(mode=0o700)
try:
    if outcome=='denied_status':
        assert C.main(['check','--evidence-token',TOKEN],stdout=io.StringIO(),stdin=io.BytesIO(json.dumps(pod(privileged=True)).encode()))==2
    before=inventory()
    class NoRead:
        def read(self,*args): raise AssertionError('status read stdin')
    code=C.main(sys.argv[4:],stdin=NoRead() if outcome in ('not_found','denied_status') else None)
    if outcome in ('not_found','denied_status'): assert inventory()==before
    expected_calls=0 if outcome in ('invalid','not_found') else 1
    assert configured_calls==invocations==len(h.calls)==expected_calls
    assert len(h.kube.requests)==(1 if outcome in ('allowed','denied','denied_status') else 0)
    if outcome in ('invalid','not_found'): assert not state.exists()
    for p in state.rglob('*'):
        if p.is_file():
            text=p.read_text()
            for value in ('PRIVATE','private-pod','private-container','private.example/workload'):
                assert value not in text
finally: h.doCleanups()
raise SystemExit(code)
`
	for _, outcome := range []string{"allowed", "denied", "failed", "invalid", "cancel", "not_found", "denied_status"} {
		t.Run(outcome, func(t *testing.T) {
			action := "check"
			if outcome == "not_found" || outcome == "denied_status" {
				action = "status"
			}
			request := dataChildRequest{Root: root, Action: action, Token: strings.Repeat("a", 32)}
			if action == "check" {
				privileged := "false"
				if outcome == "denied" {
					privileged = "true"
				}
				request.Request = []byte(` {"apiVersion":"v1","kind":"Pod","metadata":{"name":"private-pod"},"spec":{"containers":[{"name":"private-container","image":"private.example/workload@sha256:` + strings.Repeat("b", 64) + `","securityContext":{"privileged":` + privileged + `},"resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"100m","memory":"32Mi"}}}]}} `)
				if outcome == "invalid" {
					request.Request = []byte(`{"secret":"PRIVATE RAW POD"}`)
				}
			}
			state := filepath.Join(t.TempDir(), "policy-test-state")
			child := execPolicyChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
				cmd := exec.Command(python, "-I", "-B", "-c", harness, root, state, outcome, r.Action, "--evidence-token", r.Token)
				cmd.Dir = t.TempDir()
				cmd.Stdin = bytes.NewReader(r.Request)
				return cmd, nil
			}}
			var out bytes.Buffer
			err := runPolicyCommand(context.Background(), child, request, &out)
			wantCode, wantResult, wantAdmission := 1, "failed", "not-observed"
			switch outcome {
			case "allowed":
				wantCode, wantResult, wantAdmission = 0, "passed", "allowed"
			case "denied":
				wantCode, wantResult, wantAdmission = 2, "passed", "denied"
			case "denied_status":
				wantCode, wantResult, wantAdmission = 0, "passed", "denied"
			case "invalid":
				wantResult = "not_started"
			case "cancel":
				wantCode, wantResult = 130, "unknown"
			case "not_found":
				wantResult = "not_found"
			}
			lines := bytes.Split(bytes.TrimSpace(out.Bytes()), []byte("\n"))
			if ExitCode(err) != wantCode || len(lines) != 2 {
				t.Fatalf("exit=%d err=%v output=%s", ExitCode(err), err, &out)
			}
			var final policyEvent
			if e := json.Unmarshal(lines[1], &final); e != nil {
				t.Fatal(e)
			}
			if final.Result != wantResult || final.Admission != wantAdmission || final.AuthorityGranted || final.PersistedWorkloads != 0 {
				t.Fatalf("incorrect result: %+v", final)
			}
			wantCoverage := "unknown"
			if wantResult == "passed" {
				wantCoverage = "producer-checked"
				if wantAdmission == "denied" {
					wantCoverage = "not-applicable"
				}
			}
			if final.ResponseContentCheck != wantCoverage {
				t.Fatalf("incorrect response coverage: %+v", final)
			}
			for _, private := range []string{"PRIVATE", "private-pod", "private-container", "private.example/workload", state} {
				if strings.Contains(out.String(), private) || err != nil && strings.Contains(err.Error(), private) {
					t.Fatal("private input or child error was exposed")
				}
			}
			if outcome == "invalid" || outcome == "not_found" {
				if _, e := os.Stat(state); !errors.Is(e, os.ErrNotExist) {
					t.Fatal("pre-invocation refusal or status created state")
				}
				return
			}
			raw, e := os.ReadFile(filepath.Join(state, "intents", request.Token, "intent.json"))
			if e != nil {
				t.Fatal("durable invocation intent missing")
			}
			var journal struct {
				State       string `json:"state"`
				Invocations int    `json:"invocations"`
			}
			if e = json.Unmarshal(raw, &journal); e != nil {
				t.Fatal(e)
			}
			wantState := "returned"
			if outcome == "cancel" {
				wantState = "interrupted"
			}
			if journal.State != wantState || journal.Invocations != 1 {
				t.Fatalf("incorrect durable invocation: %+v", journal)
			}
		})
	}
}

func policyTestLines(request dataChildRequest, admission string) [][]byte {
	allocated, _ := json.Marshal(map[string]any{"schema_version": 1, "event": "allocated", "command": request.Action, "evidence_token": request.Token})
	value := policyEvent{SchemaVersion: 1, Event: "result", Command: request.Action, EvidenceToken: request.Token,
		WorkflowToken: warehouseString(strings.Repeat("b", 32)), Result: "passed", Execution: "invoked", Verification: "saved-evidence",
		Admission: admission, ResponseContentCheck: "producer-checked", ExpectedViolationIDs: []string{}, ReportedViolationIDs: []string{},
		Evidence: warehouseString("tmp/local-experiment/policy-candidate-bear/" + request.Token)}
	if request.Action == "status" {
		value.Execution = "read-only"
	}
	if admission == "denied" {
		value.ResponseContentCheck = "not-applicable"
		value.ExpectedViolationIDs, value.ReportedViolationIDs = []string{"P01", "P05"}, []string{"P01"}
	}
	final, _ := json.Marshal(value)
	return [][]byte{allocated, final}
}

func policyTestRoot(t *testing.T) string {
	t.Helper()
	root := dataTestRoot(t)
	if err := os.WriteFile(filepath.Join(root, policyScript), []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func executePolicy(t *testing.T, child dataChildRunner, input io.Reader, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	command := newPolicyCommand(child)
	command.SetArgs(args)
	command.SetIn(input)
	command.SetOut(&out)
	command.SetErr(io.Discard)
	err := command.Execute()
	return out.String(), err
}

type policyForbiddenReader struct{ t *testing.T }

func (r policyForbiddenReader) Read([]byte) (int, error) {
	r.t.Fatal("stdin must not be read")
	return 0, io.EOF
}

type policyErrorReader struct{}

func (policyErrorReader) Read([]byte) (int, error) { return 0, errors.New("PRIVATE INPUT ERROR") }

func TestPolicyRejectsArgumentsBeforeInputAndChild(t *testing.T) {
	root := policyTestRoot(t)
	for _, args := range [][]string{{"check"}, {"check", "--pod-stdin=false"}, {"check", "--pod-stdin", "extra"},
		{"check", "--file", "/private/pod.json"}, {"check", "--pod-stdin", "--case", "allowed"},
		{"check", "--pod-stdin", "--profile", "production"}, {"status"}, {"status", "../private"},
		{"status", testWarehouseSession, "extra"}, {"status", testWarehouseSession, "--pod-stdin"}, {"resume", testWarehouseSession}} {
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			t.Fatal("invalid arguments invoked a child")
			return dataChildResult{}, nil
		})
		out, err := executePolicy(t, child, policyForbiddenReader{t}, append(args, "--repo-root", root)...)
		if err == nil || out != "" {
			t.Fatalf("accepted %v", args)
		}
	}
}

func TestPolicyStdinSizeEncodingAndReadErrorsPreventChild(t *testing.T) {
	root := policyTestRoot(t)
	for _, input := range []io.Reader{strings.NewReader(""), strings.NewReader(" \r\n\t"),
		bytes.NewReader([]byte{0xff}), strings.NewReader(strings.Repeat("x", policyInputLimit+1)), policyErrorReader{}} {
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			t.Fatal("invalid input invoked a child")
			return dataChildResult{}, nil
		})
		out, err := executePolicy(t, child, input, "check", "--pod-stdin", "--repo-root", root)
		if err == nil || out != "" || strings.Contains(err.Error(), "PRIVATE") {
			t.Fatalf("invalid input exposed or accepted: %v output=%s", err, out)
		}
	}
	input := bytes.NewReader(bytes.Repeat([]byte("x"), policyInputLimit*2))
	if _, err := readPolicyPod(input); err == nil || input.Len() != policyInputLimit-1 {
		t.Fatal("stdin was not bounded to exactly limit+1 bytes")
	}
}

func TestPolicyPreservesExactStdinBytesAndStartsOnlyOneChild(t *testing.T) {
	root := policyTestRoot(t)
	raw := []byte(" {\"private\":\"入力\"}\n")
	raw = append(raw, bytes.Repeat([]byte(" "), policyInputLimit-len(raw))...)
	calls := 0
	child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		calls++
		if !bytes.Equal(r.Request, raw) || r.Case != "" || r.Root != root || r.Action != "check" || !warehouseSessionPattern.MatchString(r.Token) {
			t.Fatal("input or fixed invocation was changed")
		}
		for _, line := range policyTestLines(r, "allowed") {
			if err := emit(line); err != nil {
				return dataChildResult{Started: true}, err
			}
		}
		return dataChildResult{Started: true, ExitCode: 0}, nil
	})
	out, err := executePolicy(t, child, bytes.NewReader(raw), "check", "--pod-stdin", "--repo-root", root)
	if err != nil || calls != 1 || strings.Contains(out, "private") || strings.Contains(out, "入力") {
		t.Fatalf("check: %v calls=%d output=%s", err, calls, out)
	}
}

func TestPolicyStatusAndHelpDoNotConsumeStdin(t *testing.T) {
	root := policyTestRoot(t)
	calls := 0
	child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		calls++
		if r.Action != "status" || r.Token != testWarehouseSession || r.Request != nil || r.Case != "" {
			t.Fatal("status supplied input or changed identity")
		}
		for _, line := range policyTestLines(r, "denied") {
			if err := emit(line); err != nil {
				return dataChildResult{Started: true}, err
			}
		}
		return dataChildResult{Started: true, ExitCode: 0}, nil
	})
	out, err := executePolicy(t, child, policyForbiddenReader{t}, "status", testWarehouseSession, "--repo-root", root)
	if err != nil || calls != 1 || !strings.Contains(out, `"admission":"denied"`) {
		t.Fatalf("status: %v output=%s", err, out)
	}
	for _, args := range [][]string{{}, {"--help"}, {"check", "--help"}, {"status", "--help"}} {
		if _, err := executePolicy(t, child, policyForbiddenReader{t}, args...); err != nil || calls != 1 {
			t.Fatal("help invoked a child")
		}
	}
}

func TestPolicyFixedCommandUsesStdinAndExcludesCandidateFromArgvAndEnvironment(t *testing.T) {
	t.Setenv("PYTHONPATH", "/private/import")
	t.Setenv("PRIVATE_TOKEN", "PRIVATE")
	raw := []byte("{\"name\":\"PRIVATE CANDIDATE\"}\n")
	request := dataChildRequest{Root: policyTestRoot(t), Action: "check", Token: testWarehouseSession, Request: raw}
	cmd, err := policyCommand(request)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join(request.Root, dataPython), "-I", "-B", filepath.Join(request.Root, policyScript), "check", "--evidence-token", request.Token}
	if strings.Join(cmd.Args, "\n") != strings.Join(want, "\n") || cmd.Dir != request.Root || !cmd.SysProcAttr.Setpgid {
		t.Fatal("unexpected fixed invocation")
	}
	for _, entry := range append(append([]string{}, cmd.Args...), cmd.Env...) {
		if strings.Contains(entry, "PRIVATE") || strings.Contains(entry, "PYTHONPATH") {
			t.Fatal("candidate or private environment leaked")
		}
	}
	if stdin, err := io.ReadAll(cmd.Stdin); err != nil || !bytes.Equal(stdin, raw) {
		t.Fatal("stdin changed")
	}
	request.Action, request.Request = "status", nil
	cmd, err = policyCommand(request)
	if err != nil {
		t.Fatal(err)
	}
	if stdin, err := io.ReadAll(cmd.Stdin); err != nil || len(stdin) != 0 {
		t.Fatal("status inherited stdin")
	}
	for _, bad := range []dataChildRequest{{Action: "check", Token: request.Token}, {Action: "check", Token: request.Token, Request: []byte{0xff}},
		{Action: "status", Token: request.Token, Request: []byte{}}, {Action: "run", Token: request.Token},
		{Action: "check", Token: request.Token, Case: "allowed", Request: raw}, {Action: "check", Token: "../private", Request: raw}} {
		if _, err := policyCommand(bad); err == nil {
			t.Fatal("nonfixed child invocation accepted")
		}
	}
}

func TestPolicyMissingEnvironmentOrRedirectedScriptPreventsChild(t *testing.T) {
	for _, file := range []string{policyScript, dataPython, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		root := policyTestRoot(t)
		if err := os.Remove(filepath.Join(root, file)); err != nil {
			t.Fatal(err)
		}
		if _, err := resolvePolicyRoot(root); err == nil {
			t.Fatal("missing environment accepted")
		}
	}
	root := policyTestRoot(t)
	if err := os.Remove(filepath.Join(root, policyScript)); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "Makefile"), filepath.Join(root, policyScript)); err != nil {
		t.Fatal(err)
	}
	if _, err := resolvePolicyRoot(root); err == nil {
		t.Fatal("redirected script accepted")
	}
}

func TestPolicyStrictProtocolRejectsLeaksAuthorityAndFalseAdmission(t *testing.T) {
	request := dataChildRequest{Action: "check", Token: testWarehouseSession}
	good := policyTestLines(request, "allowed")[1]
	var base map[string]any
	_ = json.Unmarshal(good, &base)
	legacy := make(map[string]any)
	for key, value := range base {
		legacy[key] = value
	}
	legacy["response_content_check"] = "legacy-unrecorded"
	legacyRaw, _ := json.Marshal(legacy)
	if event, err := decodePolicyEvent(legacyRaw, request); err != nil || event.ResponseContentCheck != "legacy-unrecorded" {
		t.Fatal("historical saved allow was not distinguishable")
	}
	for key, values := range map[string][]any{
		"raw_pod": {"PRIVATE"}, "schema_version": {nil, "1", true}, "command": {"status"},
		"evidence_token": {strings.Repeat("c", 32)}, "workflow_token": {nil, "workflow-" + strings.Repeat("b", 32)},
		"authority_granted": {true, nil, "false", 0}, "persisted_workloads": {1, -1, nil, false, "0", 0.5},
		"admission": {nil, "approved", "unknown", "not-observed"}, "execution": {"not-started"},
		"response_content_check": {nil, "matched", "unknown", "not-applicable", true},
		"verification":           {"journal-only"}, "expected_violation_ids": {nil, []any{"P01"}, []any{true}},
		"reported_violation_ids": {nil, []any{"P01"}, []any{nil}}, "error_code": {"PRIVATE"}, "evidence": {nil, "/private/path"},
	} {
		for _, bad := range values {
			altered := make(map[string]any)
			for k, v := range base {
				altered[k] = v
			}
			altered[key] = bad
			raw, _ := json.Marshal(altered)
			if _, err := decodePolicyEvent(raw, request); err == nil {
				t.Errorf("accepted invalid %s", key)
			}
		}
	}
	for key := range base {
		altered := make(map[string]any)
		for k, v := range base {
			if k != key {
				altered[k] = v
			}
		}
		raw, _ := json.Marshal(altered)
		if _, err := decodePolicyEvent(raw, request); err == nil {
			t.Errorf("accepted missing %s", key)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"event":"allocated","event":"result"}`), append(good, good...), {0xff}, bytes.Repeat([]byte("x"), dataResponseLimit+1)} {
		if _, err := decodePolicyEvent(raw, request); err == nil {
			t.Fatal("malformed protocol accepted")
		}
	}
}

func TestPolicyDeniedEvidenceRequiresNonemptySortedUniqueReportedSubset(t *testing.T) {
	request := dataChildRequest{Action: "check", Token: testWarehouseSession}
	var original policyEvent
	_ = json.Unmarshal(policyTestLines(request, "denied")[1], &original)
	for _, pair := range []struct{ expected, reported []string }{
		{nil, []string{"P01"}}, {[]string{}, []string{"P01"}}, {[]string{"P01"}, nil}, {[]string{"P01"}, []string{}},
		{[]string{"P01"}, []string{"P02"}}, {[]string{"P05", "P01"}, []string{"P01"}},
		{[]string{"P01", "P01"}, []string{"P01"}}, {[]string{"P01", "P05"}, []string{"P05", "P01"}},
		{[]string{"P01"}, []string{"P01", "P01"}}, {[]string{"P07"}, []string{"P07"}},
	} {
		value := original
		value.ExpectedViolationIDs, value.ReportedViolationIDs = pair.expected, pair.reported
		raw, _ := json.Marshal(value)
		if _, err := decodePolicyEvent(raw, request); err == nil {
			t.Fatalf("invalid rule subset accepted: %+v", pair)
		}
	}
	if value, err := decodePolicyEvent(policyTestLines(request, "denied")[1], request); err != nil || len(value.ReportedViolationIDs) != 1 || len(value.ExpectedViolationIDs) != 2 {
		t.Fatal("valid partial native report was rejected or overstated")
	}
}

func TestPolicyAllowedDeniedAndStatusHaveDistinctCoherentExitCodes(t *testing.T) {
	for _, test := range []struct {
		action, admission string
		exit              int
	}{{"check", "allowed", 0}, {"check", "denied", 2}, {"status", "allowed", 0}, {"status", "denied", 0}} {
		for _, mismatch := range []bool{false, true} {
			request := dataChildRequest{Action: test.action, Token: testWarehouseSession}
			var out bytes.Buffer
			calls := 0
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				calls++
				for _, line := range policyTestLines(r, test.admission) {
					if err := emit(line); err != nil {
						return dataChildResult{Started: true}, err
					}
				}
				if strings.Contains(out.String(), `"event":"result"`) {
					t.Fatal("final delivered before child exit")
				}
				exit := test.exit
				if mismatch {
					exit = 2 - exit
				}
				return dataChildResult{Started: true, ExitCode: exit}, nil
			})
			err := runPolicyCommand(context.Background(), child, request, &out)
			if mismatch {
				if ExitCode(err) != 1 || strings.Contains(out.String(), `"event":"result"`) {
					t.Fatal("exit mismatch released a final verdict")
				}
			} else if ExitCode(err) != test.exit || !strings.Contains(out.String(), `"admission":"`+test.admission+`"`) {
				t.Fatalf("%+v: %v output=%s", test, err, &out)
			}
			if calls != 1 {
				t.Fatal("workflow was reinvoked")
			}
		}
	}
}

func TestPolicyUnknownFailureAndNotStartedDoNotBecomeDeniedSuccess(t *testing.T) {
	for _, state := range []string{"not_started", "failed", "unknown", "busy", "not_found"} {
		for _, action := range []string{"check", "status"} {
			if state == "not_started" && action == "status" {
				continue
			}
			request := dataChildRequest{Action: action, Token: testWarehouseSession}
			value := policyEvent{SchemaVersion: 1, Event: "result", Command: action, EvidenceToken: request.Token,
				Result: state, Execution: "invoked", Verification: "journal-only", Admission: "unknown",
				ResponseContentCheck: "unknown", ErrorCode: warehouseString("POLICY_CHECK_FAILED")}
			if action == "status" {
				value.Execution = "read-only"
			}
			if state == "not_started" || state == "not_found" {
				value.Admission, value.Verification = "not-observed", "not-checked"
			}
			if state == "not_started" {
				value.Execution, value.ErrorCode = "not-started", warehouseString("INPUT_INVALID")
			}
			want := 1
			if action == "status" && value.Verification == "journal-only" {
				want = 0
			}
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				if state != "not_started" {
					if err := emit(policyTestLines(r, "allowed")[0]); err != nil {
						return dataChildResult{Started: true}, err
					}
				}
				raw, _ := json.Marshal(value)
				return dataChildResult{Started: true, ExitCode: want}, emit(raw)
			})
			var out bytes.Buffer
			err := runPolicyCommand(context.Background(), child, request, &out)
			if ExitCode(err) != want || !strings.Contains(out.String(), `"result":"`+state+`"`) || strings.Contains(out.String(), `"admission":"denied"`) {
				t.Fatalf("%s/%s: %v output=%s", action, state, err, &out)
			}
		}
	}
}

func TestPolicyInvalidEventOrderOrRawFailureDoesNotReinvokeOrExposeInput(t *testing.T) {
	request := dataChildRequest{Action: "check", Token: testWarehouseSession, Request: []byte("PRIVATE INPUT")}
	for _, mode := range []string{"missing-allocation", "duplicate-allocation", "duplicate-result", "raw-result", "lost-reply"} {
		calls := 0
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			calls++
			lines := policyTestLines(r, "denied")
			switch mode {
			case "missing-allocation":
				lines = lines[1:]
			case "duplicate-allocation":
				lines = append([][]byte{lines[0]}, lines...)
			case "duplicate-result":
				lines = append(lines, lines[1])
			case "raw-result":
				lines[1] = request.Request
			case "lost-reply":
				lines = lines[:1]
			}
			for _, line := range lines {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true, PID: 77, ExitCode: 2}, err
				}
			}
			return dataChildResult{Started: true, PID: 77, ExitCode: 2}, errors.New("PRIVATE FAILURE")
		})
		var out bytes.Buffer
		err := runPolicyCommand(context.Background(), child, request, &out)
		if ExitCode(err) != 1 || calls != 1 || strings.Contains(out.String(), "PRIVATE") || strings.Contains(err.Error(), "PRIVATE") || strings.Contains(out.String(), `"event":"result"`) {
			t.Fatalf("%s: %v output=%s", mode, err, &out)
		}
		if !strings.Contains(err.Error(), "policy status "+request.Token) {
			t.Fatal("unconfirmed attempt has no safe status guidance")
		}
	}
}

func TestPolicyOwnerCancellationAndDeadlineRetainIdentityWithoutRetry(t *testing.T) {
	request := dataChildRequest{Action: "check", Token: testWarehouseSession}
	for _, user := range []bool{false, true} {
		for _, exit := range []int{130, 143, -1} {
			calls := 0
			child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
				calls++
				return dataChildResult{Started: true, PID: 88, ExitCode: exit, Interrupted: user, Unconfirmed: true}, errDataInterrupted
			})
			var out bytes.Buffer
			err := runPolicyCommand(context.Background(), child, request, &out)
			want := 1
			if user && exit != -1 {
				want = exit
			}
			if ExitCode(err) != want || calls != 1 || out.Len() != 0 || !strings.Contains(err.Error(), "child_pid=88") {
				t.Fatalf("user=%t exit=%d: %v", user, exit, err)
			}
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result, err := (execPolicyChild{}).Run(ctx, request, func([]byte) error { return nil })
	if err == nil || result.Started {
		t.Fatal("cancelled context started child")
	}
}
