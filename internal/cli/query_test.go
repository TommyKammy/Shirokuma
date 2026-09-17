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

func queryTestLines(request dataChildRequest, outcome string) [][]byte {
	allocated, _ := json.Marshal(map[string]any{"schema_version": 1, "event": "allocated", "command": request.Action, "evidence_token": request.Token})
	cleanup := true
	value := queryEvent{SchemaVersion: 1, Event: "result", Command: request.Action, EvidenceToken: request.Token,
		QueryRunToken: warehouseString(strings.Repeat("b", 32)), Result: outcome, Execution: "invoked", Verification: "saved-evidence",
		CleanupVerified: &cleanup, RootCause: "undetermined", PerformanceGain: "unmeasured",
		Evidence: warehouseString("tmp/local-experiment/query-bear/" + request.Token)}
	if request.Action == "status" {
		value.Execution = "read-only"
	}
	if outcome != "passed" {
		value.ErrorCode = warehouseString("QUERY_FAILED")
	}
	if outcome == "unknown" || outcome == "busy" {
		value.Verification = "journal-only"
		value.CleanupVerified = nil
	}
	if outcome == "not_found" {
		value.Verification = "not-checked"
		value.CleanupVerified = nil
		value.QueryRunToken = nil
		value.Evidence = nil
		value.ErrorCode = warehouseString("QUERY_NOT_FOUND")
	}
	final, _ := json.Marshal(value)
	return [][]byte{allocated, final}
}

func queryTestRoot(t *testing.T) string {
	t.Helper()
	root := dataTestRoot(t)
	if err := os.WriteFile(filepath.Join(root, queryScript), []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func executeQuery(t *testing.T, child dataChildRunner, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	command := newQueryCommand(child)
	command.SetArgs(args)
	command.SetOut(&out)
	command.SetErr(io.Discard)
	err := command.Execute()
	return out.String(), err
}

func TestQueryArgumentsRejectedBeforeChild(t *testing.T) {
	root := queryTestRoot(t)
	for _, args := range [][]string{{"run"}, {"run", "--case", "other"}, {"run", "--case", "retained-fixture", "extra"},
		{"run", "--sql", "SELECT secret"}, {"run", "--request-stdin"}, {"run", "--case", "retained-fixture", "--profile", "production"},
		{"status"}, {"status", "../escape"}, {"status", testWarehouseSession, "extra"}, {"resume", testWarehouseSession}} {
		calls := 0
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			calls++
			return dataChildResult{}, nil
		})
		out, err := executeQuery(t, child, append(args, "--repo-root", root)...)
		if err == nil || calls != 0 || out != "" {
			t.Fatalf("invalid arguments reached child: %v", args)
		}
	}
}

func TestQueryEnvironmentFailureDoesNotStartOrInstall(t *testing.T) {
	for _, name := range []string{queryScript, dataPython, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		root := queryTestRoot(t)
		if err := os.Remove(filepath.Join(root, name)); err != nil {
			t.Fatal(err)
		}
		child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
			t.Fatal("child called without environment")
			return dataChildResult{}, nil
		})
		if _, err := executeQuery(t, child, "run", "--case", "retained-fixture", "--repo-root", root); err == nil {
			t.Fatal("missing environment accepted")
		}
	}
	root := queryTestRoot(t)
	path := filepath.Join(root, queryScript)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "Makefile"), path); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveQueryRoot(root); err == nil {
		t.Fatal("redirected script accepted")
	}
}

func TestQueryCommandsBindDifferentEvidenceAndQueryIdentities(t *testing.T) {
	root := queryTestRoot(t)
	for _, action := range []string{"run", "status"} {
		calls := 0
		child := dataChildFunc(func(_ context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			calls++
			if request.Root != root || !warehouseSessionPattern.MatchString(request.Token) || request.Request != nil ||
				(action == "run" && request.Case != "retained-fixture") || (action == "status" && (request.Case != "" || request.Token != testWarehouseSession)) {
				t.Fatalf("incorrect query invocation: %+v", request)
			}
			for _, line := range queryTestLines(request, "passed") {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true, PID: 42}, err
				}
			}
			return dataChildResult{Started: true, PID: 42, ExitCode: 0}, nil
		})
		args := []string{"run", "--case", "retained-fixture"}
		if action == "status" {
			args = []string{"status", testWarehouseSession}
		}
		out, err := executeQuery(t, child, append(args, "--repo-root", root)...)
		if err != nil || calls != 1 || len(strings.Split(strings.TrimSpace(out), "\n")) != 2 {
			t.Fatalf("%s: %v output=%s", action, err, out)
		}
	}
}

func TestQueryHelpAndRootRegistrationNeverInvokeChild(t *testing.T) {
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("help started work")
		return dataChildResult{}, nil
	})
	for _, args := range [][]string{{}, {"--help"}, {"run", "--help"}, {"status", "--help"}} {
		if out, err := executeQuery(t, child, args...); err != nil || out == "" {
			t.Fatalf("help %v: %v", args, err)
		}
	}
	if !strings.Contains(execute(t, "--help"), "query") {
		t.Fatal("query root command missing")
	}
}

func TestQueryStrictProtocolRejectsLeaksTypesAndFalseSuccess(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	good := queryTestLines(request, "passed")[1]
	var base map[string]any
	if err := json.Unmarshal(good, &base); err != nil {
		t.Fatal(err)
	}
	for key, bad := range map[string]any{"raw_sql": "private", "command": "status", "evidence_token": strings.Repeat("c", 32),
		"query_run_token": nil, "result": "healthy", "execution": "not-started", "verification": "journal-only", "cleanup_verified": false,
		"root_cause": "determined", "performance_gain": "measured", "error_code": "private raw error", "evidence": "/private/path", "schema_version": nil} {
		altered := make(map[string]any)
		for k, v := range base {
			altered[k] = v
		}
		altered[key] = bad
		raw, _ := json.Marshal(altered)
		if _, err := decodeQueryEvent(raw, request); err == nil {
			t.Errorf("accepted %s", key)
		}
	}
	for _, key := range []string{"cleanup_verified", "query_run_token", "evidence", "schema_version", "root_cause"} {
		altered := make(map[string]any)
		for k, v := range base {
			altered[k] = v
		}
		delete(altered, key)
		raw, _ := json.Marshal(altered)
		if _, err := decodeQueryEvent(raw, request); err == nil {
			t.Errorf("accepted missing %s", key)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"event":"allocated","event":"result"}`), append(good, good...), bytes.Repeat([]byte("x"), 4097), {0xff}, []byte(`{"event":NaN}`)} {
		if _, err := decodeQueryEvent(raw, request); err == nil {
			t.Error("accepted malformed protocol")
		}
	}
}

func TestQueryStatusReportsVerifiedFailureWithoutPromotingIt(t *testing.T) {
	for _, outcome := range []string{"failed", "unknown", "busy", "not_found"} {
		request := dataChildRequest{Action: "status", Token: testWarehouseSession}
		want := 0
		if outcome == "not_found" {
			want = 1
		}
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			for _, line := range queryTestLines(r, outcome) {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true}, err
				}
			}
			return dataChildResult{Started: true, ExitCode: want}, nil
		})
		var out bytes.Buffer
		err := runQueryCommand(context.Background(), child, request, &out)
		if ExitCode(err) != want || !strings.Contains(out.String(), `"result":"`+outcome+`"`) || strings.Contains(out.String(), `"result":"passed"`) {
			t.Fatalf("%s: %v output=%s", outcome, err, &out)
		}
	}
}

func TestQueryNotStartedAndTerminalFailureKeepTheirOwnExitMeaning(t *testing.T) {
	for _, test := range []struct {
		action  string
		outcome string
		exit    int
	}{{"run", "not_started", 1}, {"run", "failed", 1}, {"status", "failed", 0}} {
		request := dataChildRequest{Action: test.action, Token: testWarehouseSession}
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			lines := queryTestLines(r, test.outcome)
			if test.outcome == "not_started" {
				var value queryEvent
				_ = json.Unmarshal(lines[1], &value)
				value.Execution, value.Verification = "not-started", "not-checked"
				value.QueryRunToken, value.CleanupVerified, value.Evidence = nil, nil, nil
				value.ErrorCode = warehouseString("ENVIRONMENT_NOT_READY")
				line, _ := json.Marshal(value)
				lines = [][]byte{line}
			}
			for _, line := range lines {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true}, err
				}
			}
			return dataChildResult{Started: true, ExitCode: test.exit}, nil
		})
		var out bytes.Buffer
		err := runQueryCommand(context.Background(), child, request, &out)
		if ExitCode(err) != test.exit || !strings.Contains(out.String(), `"result":"`+test.outcome+`"`) {
			t.Fatalf("%+v: %v output=%s", test, err, &out)
		}
	}
}

func TestQueryConfirmedFinalCancellationPreservesWhetherInvocationStarted(t *testing.T) {
	for _, state := range []string{"unknown", "not_started"} {
		for _, exit := range []int{130, 143} {
			request := dataChildRequest{Action: "run", Token: testWarehouseSession}
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				lines := queryTestLines(r, "unknown")
				var value queryEvent
				_ = json.Unmarshal(lines[1], &value)
				value.Result, value.Verification = state, "not-checked"
				value.QueryRunToken, value.CleanupVerified, value.Evidence = nil, nil, nil
				value.ErrorCode = warehouseString("INTERRUPTED")
				if state == "not_started" {
					value.Execution = "not-started"
				}
				lines[1], _ = json.Marshal(value)
				for _, line := range lines {
					if err := emit(line); err != nil {
						return dataChildResult{Started: true}, err
					}
				}
				return dataChildResult{Started: true, PID: 42, ExitCode: exit}, nil
			})
			var out bytes.Buffer
			err := runQueryCommand(context.Background(), child, request, &out)
			if ExitCode(err) != exit || !strings.Contains(out.String(), `"result":"`+state+`"`) || strings.Contains(out.String(), `"result":"passed"`) {
				t.Fatalf("%s/%d: %v output=%s", state, exit, err, &out)
			}
		}
	}
}

func TestQueryFinalWaitsForExitAndRejectsMissingDuplicateOrExtraEvents(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	for _, broken := range []string{"exit", "extra", "timeout", "missing-allocation", "double-allocation", "missing-result"} {
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			lines := queryTestLines(r, "passed")
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
			if broken == "exit" {
				result.ExitCode = 1
			}
			if broken == "timeout" {
				result.Unconfirmed = true
			}
			return result, nil
		})
		var out bytes.Buffer
		if err := runQueryCommand(context.Background(), child, request, &out); err == nil || strings.Contains(out.String(), `"result":"passed"`) {
			t.Fatalf("%s released success: %s %v", broken, &out, err)
		}
	}
}

func TestQueryFixedCommandExcludesRawInputAndAmbientConfiguration(t *testing.T) {
	t.Setenv("PYTHONPATH", "/unsafe/import")
	t.Setenv("PRIVATE_TOKEN", "private-secret")
	request := dataChildRequest{Root: queryTestRoot(t), Action: "run", Token: testWarehouseSession, Case: "retained-fixture"}
	cmd, err := queryCommand(request)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join(request.Root, dataPython), "-I", "-B", filepath.Join(request.Root, queryScript), "run", "--evidence-token", request.Token, "--case", "retained-fixture"}
	if strings.Join(cmd.Args, "\n") != strings.Join(want, "\n") || cmd.Dir != request.Root || !cmd.SysProcAttr.Setpgid {
		t.Fatalf("unexpected fixed command: %v", cmd.Args)
	}
	for _, entry := range cmd.Env {
		if strings.HasPrefix(entry, "PYTHONPATH=") || strings.HasPrefix(entry, "PRIVATE_TOKEN=") {
			t.Fatal("ambient configuration inherited")
		}
	}
	if raw, err := io.ReadAll(cmd.Stdin); err != nil || len(raw) != 0 {
		t.Fatal("query inherited stdin")
	}
	for _, bad := range []dataChildRequest{{Action: "resume", Token: request.Token}, {Action: "run", Token: request.Token, Case: "other"},
		{Action: "run", Token: request.Token, Case: "retained-fixture", Request: []byte("raw")}, {Action: "status", Token: request.Token, Case: "retained-fixture"}} {
		if _, err := queryCommand(bad); err == nil {
			t.Fatal("accepted non-fixed query command")
		}
	}
}

func TestQueryRealPythonProtocolWithFakeInvocation(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python unavailable; no installation attempted")
	}
	// The real parser, main, projection validator and status path run in the
	// isolated Python. Replace invocation only; no model or runtime can be used.
	harness := `
import pathlib, sys
sys.path.insert(0, sys.argv[1]+'/tools/local_experiment/bears')
import query_cli as C
C.STATE=pathlib.Path(sys.argv[2])/'intents'
C.EVIDENCE=pathlib.Path(sys.argv[2])/'evidence'
outcome=sys.argv[3]
def never():
    raise AssertionError('runtime must not be loaded')
C.runner=never
def invoke(identity, lifecycle):
    lifecycle['invoked']=True
    print('PRIVATE CHILD OUTPUT')
    print('PRIVATE CHILD ERROR',file=sys.stderr)
    return C.projection('run',identity,result=outcome,execution='invoked',verification='saved-evidence',
        query_run_token='b'*32,cleanup_verified=outcome=='passed',has_evidence=True,
        error_code=None if outcome=='passed' else 'QUERY_FAILED'),0 if outcome=='passed' else 1
C.invoke=invoke
raise SystemExit(C.main(sys.argv[4:]))
`
	for _, outcome := range []string{"passed", "failed", "not_found"} {
		action := "run"
		if outcome == "not_found" {
			action = "status"
		}
		request := dataChildRequest{Root: root, Action: action, Token: testWarehouseSession, Case: "retained-fixture"}
		state := filepath.Join(t.TempDir(), "query-test-state")
		runner := execQueryChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
			args := []string{"-I", "-B", "-c", harness, root, state, outcome, r.Action, "--evidence-token", r.Token}
			if r.Action == "run" {
				args = append(args, "--case", "retained-fixture")
			}
			cmd := exec.Command(python, args...)
			cmd.Dir = t.TempDir()
			return cmd, nil
		}}
		var out bytes.Buffer
		err := runQueryCommand(context.Background(), runner, request, &out)
		want := 1
		if outcome == "passed" {
			want = 0
		}
		if ExitCode(err) != want || strings.Contains(out.String(), "PRIVATE") || !strings.Contains(out.String(), `"result":"`+outcome+`"`) {
			t.Fatalf("%s: exit=%d err=%v output=%s", outcome, ExitCode(err), err, &out)
		}
		if _, err := os.Stat(state); !errors.Is(err, os.ErrNotExist) {
			t.Fatal("protocol-only child created state")
		}
	}
}

func TestQuerySubprocessHelper(t *testing.T) {
	mode := os.Getenv("SHIROKUMA_QUERY_TEST_HELPER")
	if mode == "" {
		return
	}
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt)
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	fmt.Fprintln(os.Stderr, "PRIVATE QUERY STDERR")
	if mode == "oversize" {
		fmt.Println(strings.Repeat("PRIVATE", 1000))
	} else {
		fmt.Println(string(queryTestLines(request, "passed")[0]))
	}
	if mode == "success" {
		fmt.Println(string(queryTestLines(request, "passed")[1]))
		os.Exit(0)
	}
	<-signals
	if mode == "cancel" {
		time.Sleep(20 * time.Millisecond)
		_ = os.WriteFile(os.Getenv("SHIROKUMA_QUERY_TEST_MARKER"), []byte("cleaned"), 0600)
		os.Exit(130)
	}
	if mode == "hang" {
		for {
			<-signals
		}
	}
	os.Exit(1)
}

func queryTestProcess(mode, marker string) *exec.Cmd {
	cmd := exec.Command(os.Args[0], "-test.run=^TestQuerySubprocessHelper$")
	cmd.Env = append(os.Environ(), "SHIROKUMA_QUERY_TEST_HELPER="+mode, "SHIROKUMA_QUERY_TEST_MARKER="+marker)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd
}

func TestQueryTransportSuccessAndOversizeSuppression(t *testing.T) {
	for _, mode := range []string{"success", "oversize"} {
		runner := execQueryChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess(mode, ""), nil }}
		var out bytes.Buffer
		err := runQueryCommand(context.Background(), runner, dataChildRequest{Action: "run", Token: testWarehouseSession}, &out)
		if (err == nil) != (mode == "success") || strings.Contains(out.String(), "PRIVATE") {
			t.Fatalf("%s: %v output=%s", mode, err, &out)
		}
	}
}

func TestQueryCancellationWaitsForOwnerCleanup(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "cleaned")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	runner := execQueryChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess("cancel", marker), nil }}
	child := dataChildFunc(func(ctx context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		return runner.Run(ctx, r, func(line []byte) error { err := emit(line); cancel(); return err })
	})
	var out bytes.Buffer
	err := runQueryCommand(ctx, child, dataChildRequest{Action: "run", Token: testWarehouseSession}, &out)
	if ExitCode(err) != 130 || !strings.Contains(err.Error(), "outcome unknown") || strings.Contains(out.String(), `"event":"result"`) {
		t.Fatalf("cancel: %v output=%s", err, &out)
	}
	if raw, err := os.ReadFile(marker); err != nil || string(raw) != "cleaned" {
		t.Fatal("owner cleanup not completed")
	}
}

func TestQueryGraceExpiryLeavesIdentifiedChildAlive(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	runner := execQueryChild{grace: 20 * time.Millisecond, command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess("hang", ""), nil }}
	result, err := runner.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if result.PID > 0 {
		defer syscall.Kill(result.PID, syscall.SIGKILL) // Only this disposable fake helper.
	}
	if err == nil || result.PID <= 0 || !result.Started || !result.Unconfirmed || result.ExitCode != -1 || syscall.Kill(result.PID, 0) != nil {
		t.Fatalf("grace result=%+v err=%v", result, err)
	}
}

func TestQueryDeadlineAndUnconfirmedCancellationNeverBecomeSuccess(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	for _, interruption := range []bool{false, true} {
		for _, exit := range []int{130, 143, -1, 0} {
			child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
				return dataChildResult{Started: true, PID: 101, ExitCode: exit, Interrupted: interruption, Unconfirmed: true}, errDataInterrupted
			})
			var out bytes.Buffer
			err := runQueryCommand(context.Background(), child, request, &out)
			want := 1
			if interruption && (exit == 130 || exit == 143) {
				want = exit
			}
			if ExitCode(err) != want || out.Len() != 0 || !strings.Contains(err.Error(), "child_pid=101") || !strings.Contains(err.Error(), "query status "+request.Token) {
				t.Fatalf("unexpected cancellation outcome: %v", err)
			}
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result, err := (execQueryChild{}).Run(ctx, request, func([]byte) error { return nil })
	if err == nil || result.Started {
		t.Fatal("canceled request started")
	}
	if !errors.Is(ctx.Err(), context.Canceled) {
		t.Fatal("test context not canceled")
	}
}
