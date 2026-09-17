package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func sreTestLines(request dataChildRequest, outcome string) [][]byte {
	allocated, _ := json.Marshal(map[string]any{"schema_version": 1, "event": "allocated", "command": request.Action, "evidence_token": request.Token})
	value := sreEvent{SchemaVersion: 1, Event: "result", Command: request.Action, EvidenceToken: request.Token,
		WorkflowToken: warehouseString(strings.Repeat("b", 32)), Result: outcome, Execution: "invoked", Verification: "saved-evidence",
		Assessment: warehouseString("attention-required"), RootCause: "undetermined",
		Evidence: warehouseString("tmp/local-experiment/sre-llm/" + request.Token)}
	if request.Action == "status" {
		value.Execution = "read-only"
	}
	if outcome != "passed" {
		value.ErrorCode = warehouseString("SRE_FAILED")
	}
	if outcome == "unknown" || outcome == "busy" {
		value.Verification = "journal-only"
		value.Assessment = nil
	}
	if outcome == "not_found" || outcome == "not_started" {
		value.Verification = "not-checked"
		value.WorkflowToken, value.Assessment, value.Evidence = nil, nil, nil
		value.ErrorCode = warehouseString("SRE_NOT_FOUND")
	}
	if outcome == "not_started" {
		value.Execution = "not-started"
		value.ErrorCode = warehouseString("ENVIRONMENT_NOT_READY")
	}
	final, _ := json.Marshal(value)
	return [][]byte{allocated, final}
}

func sreTestRoot(t *testing.T) string {
	t.Helper()
	root := dataTestRoot(t)
	if err := os.WriteFile(filepath.Join(root, sreScript), []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func executeSRE(t *testing.T, child dataChildRunner, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	command := newSRECommand(child)
	command.SetArgs(args)
	command.SetOut(&out)
	command.SetErr(io.Discard)
	err := command.Execute()
	return out.String(), err
}

func TestSREArgumentsRejectedBeforeChild(t *testing.T) {
	root := sreTestRoot(t)
	for _, args := range [][]string{{"run", "--case", "retained-fixture"}, {"run", "extra"}, {"run", "--sql", "SELECT private"},
		{"run", "--request-stdin"}, {"run", "--profile", "production"}, {"status"}, {"status", "../escape"},
		{"status", testWarehouseSession, "extra"}, {"resume", testWarehouseSession}, {"run", "--remediate"}} {
		calls := 0
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			calls++
			return dataChildResult{}, nil
		})
		out, err := executeSRE(t, child, append(args, "--repo-root", root)...)
		if err == nil || calls != 0 || out != "" {
			t.Fatalf("invalid arguments reached child: %v", args)
		}
	}
}

func TestSREEnvironmentFailureDoesNotStartOrInstall(t *testing.T) {
	for _, name := range []string{sreScript, dataPython, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		root := sreTestRoot(t)
		if err := os.Remove(filepath.Join(root, name)); err != nil {
			t.Fatal(err)
		}
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			t.Fatal("child called without environment")
			return dataChildResult{}, nil
		})
		if _, err := executeSRE(t, child, "run", "--repo-root", root); err == nil {
			t.Fatal("missing environment accepted")
		}
	}
	root := sreTestRoot(t)
	path := filepath.Join(root, sreScript)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "Makefile"), path); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveSRERoot(root); err == nil {
		t.Fatal("redirected script accepted")
	}
}

func TestSRECommandsBindDifferentEvidenceAndWorkflowIdentities(t *testing.T) {
	root := sreTestRoot(t)
	for _, action := range []string{"run", "status"} {
		calls := 0
		child := dataChildFunc(func(_ context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			calls++
			if request.Root != root || !warehouseSessionPattern.MatchString(request.Token) || request.Request != nil || request.Case != "" ||
				(action == "status" && request.Token != testWarehouseSession) {
				t.Fatalf("incorrect SRE invocation: %+v", request)
			}
			for _, line := range sreTestLines(request, "passed") {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true, PID: 42}, err
				}
			}
			return dataChildResult{Started: true, PID: 42, ExitCode: 0}, nil
		})
		args := []string{action}
		if action == "status" {
			args = append(args, testWarehouseSession)
		}
		out, err := executeSRE(t, child, append(args, "--repo-root", root)...)
		if err != nil || calls != 1 || len(strings.Split(strings.TrimSpace(out), "\n")) != 2 {
			t.Fatalf("%s: %v output=%s", action, err, out)
		}
		if !strings.Contains(out, `"workflow_token":"`+strings.Repeat("b", 32)+`"`) {
			t.Fatal("workflow identity was lost")
		}
	}
}

func TestSREHelpNeverInvokesChild(t *testing.T) {
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("help started work")
		return dataChildResult{}, nil
	})
	for _, args := range [][]string{{}, {"--help"}, {"run", "--help"}, {"status", "--help"}} {
		if out, err := executeSRE(t, child, args...); err != nil || out == "" {
			t.Fatalf("help %v: %v", args, err)
		}
	}
}

func TestSREStrictProtocolRejectsLeaksTypesAndFalseSuccess(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	good := sreTestLines(request, "passed")[1]
	var base map[string]any
	if err := json.Unmarshal(good, &base); err != nil {
		t.Fatal(err)
	}
	for key, bad := range map[string]any{"raw_logs": "private", "command": "status", "evidence_token": strings.Repeat("c", 32),
		"workflow_token": nil, "result": "healthy", "execution": "not-started", "verification": "journal-only", "assessment": nil,
		"root_cause": "determined", "remediation_executed": true, "error_code": "private raw error", "evidence": "/private/path", "schema_version": nil} {
		altered := make(map[string]any)
		for k, v := range base {
			altered[k] = v
		}
		altered[key] = bad
		raw, _ := json.Marshal(altered)
		if _, err := decodeSREEvent(raw, request); err == nil {
			t.Errorf("accepted %s", key)
		}
	}
	for _, key := range []string{"assessment", "workflow_token", "evidence", "schema_version", "root_cause", "remediation_executed"} {
		altered := make(map[string]any)
		for k, v := range base {
			altered[k] = v
		}
		delete(altered, key)
		raw, _ := json.Marshal(altered)
		if _, err := decodeSREEvent(raw, request); err == nil {
			t.Errorf("accepted missing %s", key)
		}
	}
	for key, values := range map[string][]any{
		"remediation_executed": {nil, "false", 0}, "assessment": {false, "healthy", "private assessment"},
		"workflow_token": {1, "workflow-" + strings.Repeat("b", 32)}, "schema_version": {true, "1"},
	} {
		for _, bad := range values {
			altered := make(map[string]any)
			for k, v := range base {
				altered[k] = v
			}
			altered[key] = bad
			raw, _ := json.Marshal(altered)
			if _, err := decodeSREEvent(raw, request); err == nil {
				t.Errorf("accepted invalid %s=%v", key, bad)
			}
		}
	}
	for _, raw := range [][]byte{[]byte(`{"event":"allocated","event":"result"}`), append(good, good...), bytes.Repeat([]byte("x"), 4097), {0xff}, []byte(`{"event":NaN}`)} {
		if _, err := decodeSREEvent(raw, request); err == nil {
			t.Error("accepted malformed protocol")
		}
	}
}

func TestSRECompletedObservationDoesNotImplyHealthyOrRemediated(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	for _, assessment := range []string{"attention-required", "healthy-observed-scope", "unknown"} {
		var value sreEvent
		_ = json.Unmarshal(sreTestLines(request, "passed")[1], &value)
		value.Assessment = &assessment
		raw, _ := json.Marshal(value)
		observed, err := decodeSREEvent(raw, request)
		if err != nil || observed.Result != "passed" || *observed.Assessment != assessment || observed.RemediationExecuted || observed.RootCause != "undetermined" {
			t.Fatalf("assessment=%s: %v", assessment, err)
		}
	}
}

func TestSREStatusAndRunPreserveResultAndExitMeaning(t *testing.T) {
	for _, test := range []struct {
		action, outcome string
		exit            int
	}{{"status", "failed", 0}, {"status", "unknown", 0}, {"status", "busy", 0}, {"status", "not_found", 1},
		{"run", "not_started", 1}, {"run", "failed", 1}, {"run", "unknown", 1}} {
		request := dataChildRequest{Action: test.action, Token: testWarehouseSession}
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			lines := sreTestLines(r, test.outcome)
			if test.outcome == "not_started" {
				lines = lines[1:] // Preflight failure may precede an allocation event.
			}
			for _, line := range lines {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true}, err
				}
			}
			return dataChildResult{Started: true, ExitCode: test.exit}, nil
		})
		var out bytes.Buffer
		err := runSRECommand(context.Background(), child, request, &out)
		if ExitCode(err) != test.exit || !strings.Contains(out.String(), `"result":"`+test.outcome+`"`) || strings.Contains(out.String(), `"result":"passed"`) {
			t.Fatalf("%+v: %v output=%s", test, err, &out)
		}
	}
}

func TestSREConfirmedFinalCancellationPreservesWhetherInvocationStarted(t *testing.T) {
	for _, state := range []string{"unknown", "not_started"} {
		for _, exit := range []int{130, 143} {
			request := dataChildRequest{Action: "run", Token: testWarehouseSession}
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				lines := sreTestLines(r, state)
				var value sreEvent
				_ = json.Unmarshal(lines[1], &value)
				value.Verification = "not-checked"
				value.WorkflowToken, value.Assessment, value.Evidence = nil, nil, nil
				value.ErrorCode = warehouseString("INTERRUPTED")
				lines[1], _ = json.Marshal(value)
				for _, line := range lines {
					if err := emit(line); err != nil {
						return dataChildResult{Started: true}, err
					}
				}
				return dataChildResult{Started: true, PID: 42, ExitCode: exit}, nil
			})
			var out bytes.Buffer
			err := runSRECommand(context.Background(), child, request, &out)
			if ExitCode(err) != exit || !strings.Contains(out.String(), `"result":"`+state+`"`) {
				t.Fatalf("%s/%d: %v output=%s", state, exit, err, &out)
			}
		}
	}
}

func TestSREFinalWaitsForExitAndRejectsMissingDuplicateOrExtraEvents(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	for _, broken := range []string{"exit", "extra", "timeout", "missing-allocation", "double-allocation", "missing-result", "raw-error"} {
		var out bytes.Buffer
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			lines := sreTestLines(r, "passed")
			switch broken {
			case "missing-allocation":
				lines = lines[1:]
			case "double-allocation":
				lines = append([][]byte{lines[0]}, lines...)
			case "missing-result":
				lines = lines[:1]
			case "extra":
				lines = append(lines, lines[1])
			}
			result := dataChildResult{Started: true, PID: 99, ExitCode: 0}
			for _, line := range lines {
				if err := emit(line); err != nil {
					return result, err
				}
			}
			if strings.Contains(out.String(), `"event":"result"`) {
				t.Fatal("result delivered before child exit")
			}
			if broken == "exit" {
				result.ExitCode = 1
			}
			if broken == "timeout" {
				result.Unconfirmed = true
			}
			if broken == "raw-error" {
				return result, errors.New("PRIVATE CHILD ERROR")
			}
			return result, nil
		})
		err := runSRECommand(context.Background(), child, request, &out)
		if err == nil || strings.Contains(out.String(), `"event":"result"`) || strings.Contains(err.Error(), "PRIVATE") {
			t.Fatalf("%s released success or raw error: %s %v", broken, &out, err)
		}
	}
}

func TestSREFixedCommandExcludesInputAndAmbientConfiguration(t *testing.T) {
	t.Setenv("PYTHONPATH", "/unsafe/import")
	t.Setenv("PRIVATE_TOKEN", "private-secret")
	request := dataChildRequest{Root: sreTestRoot(t), Action: "run", Token: testWarehouseSession}
	for _, action := range []string{"run", "status"} {
		request.Action = action
		cmd, err := sreCommand(request)
		if err != nil {
			t.Fatal(err)
		}
		want := []string{filepath.Join(request.Root, dataPython), "-I", "-B", filepath.Join(request.Root, sreScript), action, "--evidence-token", request.Token}
		if strings.Join(cmd.Args, "\n") != strings.Join(want, "\n") || cmd.Dir != request.Root || !cmd.SysProcAttr.Setpgid {
			t.Fatalf("unexpected fixed command: %v", cmd.Args)
		}
		for _, entry := range cmd.Env {
			if strings.HasPrefix(entry, "PYTHONPATH=") || strings.HasPrefix(entry, "PRIVATE_TOKEN=") {
				t.Fatal("ambient configuration inherited")
			}
		}
		if raw, err := io.ReadAll(cmd.Stdin); err != nil || len(raw) != 0 {
			t.Fatal("SRE inherited stdin")
		}
	}
	for _, bad := range []dataChildRequest{{Action: "resume", Token: request.Token}, {Action: "run", Token: request.Token, Case: "retained-fixture"},
		{Action: "run", Token: request.Token, Request: []byte("raw")}, {Action: "status", Token: request.Token, Request: []byte{}}, {Action: "run", Token: "../escape"}} {
		if _, err := sreCommand(bad); err == nil {
			t.Fatal("accepted non-fixed SRE command")
		}
	}
}

func TestSRERealPythonProtocolWithFakeWorkflow(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	// Exercise the real main, durable invocation and saved-evidence validation.
	// The sole workflow is fake; all fixtures and journals live in t.TempDir.
	harness := `
import asyncio, pathlib, sys
from types import SimpleNamespace
sys.path[:0]=[sys.argv[1]+'/tools/local_experiment/bears',sys.argv[1]+'/tests']
import sre_cli as C
from test_sre_cli import fixture_documents, write_fixture
state=pathlib.Path(sys.argv[2]).resolve()
C.STATE=state/'intents'
C.EVIDENCE=state/'evidence'
outcome=sys.argv[3]
def never():
    raise AssertionError('runtime must not be loaded')
C.runner=never
async def workflow(config, output):
    print('PRIVATE CHILD OUTPUT')
    print('PRIVATE CHILD ERROR',file=sys.stderr)
    if outcome=='cancel':
        raise asyncio.CancelledError('PRIVATE CANCELLATION')
    docs=fixture_documents()
    if outcome=='failed':
        r=docs['result.json']
        r.update(result='failed',error_code='SRE_OBSERVATION_INFERENCE_OR_AUDIT_FAILED')
        r.pop('checkpoint')
        r['audit_records'][-1]['status']='failed'
        docs['spans.jsonl'][-1]['status']='ERROR'
    return write_fixture(output,docs)
def configured():
    assert outcome!='not_found', 'status attempted a workflow'
    return SimpleNamespace(run=workflow),{}
C.configured_runner=configured
if outcome!='not_found':
    state.mkdir(mode=0o700)
raise SystemExit(C.main(sys.argv[4:]))
`
	for _, outcome := range []string{"passed", "failed", "not_found", "cancel"} {
		action := "run"
		if outcome == "not_found" {
			action = "status"
		}
		request := dataChildRequest{Root: root, Action: action, Token: testWarehouseSession}
		state := filepath.Join(t.TempDir(), "sre-test-state")
		runner := execSREChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
			args := []string{"-I", "-B", "-c", harness, root, state, outcome, r.Action, "--evidence-token", r.Token}
			cmd := exec.Command(python, args...)
			cmd.Dir = t.TempDir()
			return cmd, nil
		}}
		var out bytes.Buffer
		err := runSRECommand(context.Background(), runner, request, &out)
		want, result := 1, outcome
		if outcome == "passed" {
			want = 0
		}
		if outcome == "cancel" {
			want, result = 130, "unknown"
		}
		if ExitCode(err) != want || strings.Contains(out.String(), "PRIVATE") || !strings.Contains(out.String(), `"result": "`+result+`"`) && !strings.Contains(out.String(), `"result":"`+result+`"`) {
			t.Fatalf("%s: exit=%d err=%v output=%s", outcome, ExitCode(err), err, &out)
		}
		if err != nil && strings.Contains(err.Error(), "PRIVATE") {
			t.Fatal("private child error was exposed")
		}
		if outcome == "not_found" {
			if _, err := os.Stat(state); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("status created state")
			}
		} else {
			raw, err := os.ReadFile(filepath.Join(state, "intents", request.Token, "intent.json"))
			if err != nil {
				t.Fatal("invocation intent missing")
			}
			var journal struct {
				State       string `json:"state"`
				Invocations int    `json:"invocations"`
			}
			if err := json.Unmarshal(raw, &journal); err != nil {
				t.Fatal(err)
			}
			wantState := "returned"
			if outcome == "cancel" {
				wantState = "interrupted"
			}
			if journal.State != wantState || journal.Invocations != 1 {
				t.Fatalf("%s: incorrect durable invocation: %+v", outcome, journal)
			}
		}
	}
}

func TestSRESubprocessHelper(t *testing.T) {
	mode := os.Getenv("SHIROKUMA_SRE_TEST_HELPER")
	if mode == "" {
		return
	}
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt)
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	fmt.Fprintln(os.Stderr, "PRIVATE SRE STDERR")
	if mode == "oversize" {
		fmt.Println(strings.Repeat("PRIVATE", 1000))
	} else {
		fmt.Println(string(sreTestLines(request, "passed")[0]))
	}
	if mode == "success" {
		fmt.Println(string(sreTestLines(request, "passed")[1]))
		os.Exit(0)
	}
	<-signals
	if mode == "cancel" {
		time.Sleep(20 * time.Millisecond)
		_ = os.WriteFile(os.Getenv("SHIROKUMA_SRE_TEST_MARKER"), []byte("cleaned"), 0600)
		os.Exit(130)
	}
	if mode == "hang" {
		for {
			<-signals
		}
	}
	os.Exit(1)
}

func sreTestProcess(mode, marker string) *exec.Cmd {
	cmd := exec.Command(os.Args[0], "-test.run=^TestSRESubprocessHelper$")
	cmd.Env = append(os.Environ(), "SHIROKUMA_SRE_TEST_HELPER="+mode, "SHIROKUMA_SRE_TEST_MARKER="+marker)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd
}

func TestSRETransportSuccessAndOversizeSuppression(t *testing.T) {
	for _, mode := range []string{"success", "oversize"} {
		runner := execSREChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return sreTestProcess(mode, ""), nil }}
		var out bytes.Buffer
		err := runSRECommand(context.Background(), runner, dataChildRequest{Action: "run", Token: testWarehouseSession}, &out)
		if (err == nil) != (mode == "success") || strings.Contains(out.String(), "PRIVATE") {
			t.Fatalf("%s: %v output=%s", mode, err, &out)
		}
	}
}

func TestSRECancellationWaitsForOwnerCleanup(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "cleaned")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	runner := execSREChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return sreTestProcess("cancel", marker), nil }}
	child := dataChildFunc(func(ctx context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		return runner.Run(ctx, r, func(line []byte) error { err := emit(line); cancel(); return err })
	})
	var out bytes.Buffer
	err := runSRECommand(ctx, child, dataChildRequest{Action: "run", Token: testWarehouseSession}, &out)
	if ExitCode(err) != 130 || !strings.Contains(err.Error(), "outcome unknown") || strings.Contains(out.String(), `"event":"result"`) {
		t.Fatalf("cancel: %v output=%s", err, &out)
	}
	if raw, err := os.ReadFile(marker); err != nil || string(raw) != "cleaned" {
		t.Fatal("owner cleanup not completed")
	}
}

func TestSREGraceExpiryLeavesIdentifiedChildAlive(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	runner := execSREChild{grace: 20 * time.Millisecond, command: func(dataChildRequest) (*exec.Cmd, error) { return sreTestProcess("hang", ""), nil }}
	result, err := runner.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if result.PID > 0 {
		defer syscall.Kill(result.PID, syscall.SIGKILL) // Only this disposable fake helper.
	}
	if err == nil || result.PID <= 0 || !result.Started || !result.Unconfirmed || result.ExitCode != -1 || syscall.Kill(result.PID, 0) != nil {
		t.Fatalf("grace result=%+v err=%v", result, err)
	}
}

func TestSREDeadlineAndUnconfirmedCancellationNeverBecomeSuccess(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	for _, interruption := range []bool{false, true} {
		for _, exit := range []int{130, 143, -1, 0} {
			child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
				return dataChildResult{Started: true, PID: 101, ExitCode: exit, Interrupted: interruption, Unconfirmed: true}, errDataInterrupted
			})
			var out bytes.Buffer
			err := runSRECommand(context.Background(), child, request, &out)
			want := 1
			if interruption && (exit == 130 || exit == 143) {
				want = exit
			}
			if ExitCode(err) != want || out.Len() != 0 || !strings.Contains(err.Error(), "child_pid=101") || !strings.Contains(err.Error(), "sre status "+request.Token) {
				t.Fatalf("unexpected cancellation outcome: %v", err)
			}
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result, err := (execSREChild{}).Run(ctx, request, func([]byte) error { return nil })
	if err == nil || result.Started {
		t.Fatal("canceled request started")
	}
}
