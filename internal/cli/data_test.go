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

type fakeDataChild struct {
	requests []dataChildRequest
	result   string
}

func (child *fakeDataChild) Run(_ context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	child.requests = append(child.requests, request)
	for _, line := range dataTestLines(request, child.result) {
		if err := emit(line); err != nil {
			return dataChildResult{Started: true, PID: 123, ExitCode: 1}, err
		}
	}
	exit := 0
	if child.result == "unsupported" && request.Action != "status" {
		exit = 2
	}
	return dataChildResult{Started: true, PID: 123, ExitCode: exit}, nil
}

func dataTestLines(request dataChildRequest, result string) [][]byte {
	if result == "" {
		result = "passed"
	}
	allocation, _ := json.Marshal(map[string]any{"schema_version": 1, "event": "allocated", "command": request.Action, "handoff_token": request.Token})
	phase, execution, verification := "completed", "invoked", "saved-evidence"
	if request.Action == "status" {
		execution = "read-only"
	}
	if result == "unsupported" {
		phase = "unsupported"
	}
	value := dataEvent{SchemaVersion: 1, Event: "result", Command: request.Action, HandoffToken: request.Token,
		Result: result, Phase: &phase, Execution: execution, Verification: verification,
		DataAttemptToken: warehouseString(strings.Repeat("a", 32)), DataPlanToken: warehouseString(strings.Repeat("b", 32)),
		CatalogAttempts: 1, Evidence: warehouseString("tmp/local-experiment/data-catalog/" + request.Token)}
	if result == "unsupported" {
		value.DataPlanToken = nil
		value.CatalogAttempts = 0
	}
	final, _ := json.Marshal(value)
	return [][]byte{allocation, final}
}

func dataTestRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	for name, mode := range map[string]os.FileMode{"Makefile": 0600, "security/resident-images.json": 0600, dataScript: 0600,
		"tools/local_experiment/llm/.venv/pyvenv.cfg": 0600, dataPython: 0700} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("fixture"), mode); err != nil {
			t.Fatal(err)
		}
	}
	canonical, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	return canonical
}

func executeData(t *testing.T, child dataChildRunner, input []byte, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := newDataCommand(child)
	cmd.SetArgs(args)
	cmd.SetIn(bytes.NewReader(input))
	cmd.SetOut(&out)
	cmd.SetErr(io.Discard)
	returnOut := func() (string, error) { err := cmd.Execute(); return out.String(), err }
	return returnOut()
}

func TestDataRunRejectsNoInputAndInvalidInputsBeforeChild(t *testing.T) {
	root := dataTestRoot(t)
	for _, test := range []struct {
		args  []string
		input []byte
	}{
		{[]string{"run"}, nil}, {[]string{"run", "--case", "other"}, nil},
		{[]string{"run", "--case", "row_count_ja", "--request-stdin"}, []byte("request")},
		{[]string{"run", "--case", "", "--request-stdin"}, []byte("request")},
		{[]string{"run", "--request-stdin"}, nil}, {[]string{"run", "--request-stdin"}, []byte(" \n")},
		{[]string{"run", "--request-stdin"}, []byte("first\nsecond")},
		{[]string{"run", "--request-stdin"}, []byte{0xff}}, {[]string{"run", "--request-stdin"}, []byte("secret\x00")},
		{[]string{"run", "--request-stdin"}, []byte(strings.Repeat("あ", 513))},
		{[]string{"run", "--request-stdin"}, []byte(strings.Repeat("x", 4097))},
		{[]string{"resume", "../escape"}, nil}, {[]string{"status"}, nil},
		{[]string{"run", "--case", "row_count_ja", "--profile", "production"}, nil},
	} {
		child := &fakeDataChild{}
		args := append(append([]string{}, test.args...), "--repo-root", root)
		out, err := executeData(t, child, test.input, args...)
		if err == nil || len(child.requests) != 0 || out != "" {
			t.Errorf("args=%v err=%v child=%d output=%q", test.args, err, len(child.requests), out)
		}
	}
}

func TestDataMissingOrRedirectedEnvironmentDoesNotStart(t *testing.T) {
	for _, name := range []string{dataPython, "tools/local_experiment/llm/.venv/pyvenv.cfg", dataScript} {
		root := dataTestRoot(t)
		path := filepath.Join(root, name)
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
		child := &fakeDataChild{}
		if _, err := executeData(t, child, nil, "run", "--case", "name_lengths_ja", "--repo-root", root); err == nil || len(child.requests) > 0 {
			t.Fatalf("missing %s started", name)
		}
	}
	root := dataTestRoot(t)
	script := filepath.Join(root, dataScript)
	if err := os.Remove(script); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "Makefile"), script); err != nil {
		t.Fatal(err)
	}
	child := &fakeDataChild{}
	if _, err := executeData(t, child, nil, "run", "--case", "row_count_ja", "--repo-root", root); err == nil || len(child.requests) > 0 {
		t.Fatal("symlink script started")
	}
}

func TestDataCommandsBindTokenRequestAndExitCodes(t *testing.T) {
	root := dataTestRoot(t)
	child := &fakeDataChild{}
	out, err := executeData(t, child, []byte("名前を大文字にしてください\r\n"), "run", "--request-stdin", "--repo-root", root)
	if err != nil || len(child.requests) != 1 {
		t.Fatal(err)
	}
	r := child.requests[0]
	if !warehouseSessionPattern.MatchString(r.Token) || r.Root != root || string(r.Request) != "名前を大文字にしてください" || r.Case != "" {
		t.Fatalf("request %+v", r)
	}
	if strings.Contains(out, "大文字") || len(strings.Split(strings.TrimSpace(out), "\n")) != 2 {
		t.Fatalf("unsafe output %s", out)
	}
	child = &fakeDataChild{result: "unsupported"}
	_, err = executeData(t, child, nil, "run", "--case", "row_count_ja", "--repo-root", root)
	if ExitCode(err) != 2 {
		t.Fatalf("unsupported exit=%d err=%v", ExitCode(err), err)
	}
	for _, action := range []string{"resume", "status"} {
		child = &fakeDataChild{}
		if _, err := executeData(t, child, nil, action, testWarehouseSession, "--repo-root", root); err != nil {
			t.Fatal(err)
		}
		if child.requests[0].Token != testWarehouseSession || child.requests[0].Request != nil || child.requests[0].Case != "" {
			t.Fatalf("unexpected input %+v", child.requests)
		}
	}
	if ExitCode(nil) != 0 || ExitCode(errors.New("legacy")) != 1 || ExitCode(fmt.Errorf("wrapped: %w", &dataExitError{2, "unsupported"})) != 2 {
		t.Fatal("exit mapping")
	}
}

func TestDataHelpAndRootRegistrationDoNotStartWork(t *testing.T) {
	child := &fakeDataChild{}
	for _, args := range [][]string{{}, {"--help"}, {"run", "--help"}, {"resume", "--help"}, {"status", "--help"}} {
		out, err := executeData(t, child, nil, args...)
		if err != nil || out == "" || len(child.requests) > 0 {
			t.Fatalf("help %v: %v", args, err)
		}
	}
	if !strings.Contains(execute(t, "--help"), "data") {
		t.Fatal("missing root registration")
	}
}

func TestDataProtocolRejectsLeaksMalformedAndMismatchedEvidence(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	good := dataTestLines(request, "passed")[1]
	var fields map[string]any
	if err := json.Unmarshal(good, &fields); err != nil {
		t.Fatal(err)
	}
	for key, bad := range map[string]any{"raw_request": "private", "command": "status", "handoff_token": strings.Repeat("b", 32),
		"error_code": "private error", "evidence": "/private/secret", "data_plan_token": "../x", "catalog_attempts": 9,
		"replayed": nil, "schema_version": nil, "phase": "data_pending", "result": "healthy"} {
		altered := map[string]any{}
		for k, v := range fields {
			altered[k] = v
		}
		altered[key] = bad
		raw, _ := json.Marshal(altered)
		if _, err := decodeDataEvent(raw, request); err == nil {
			t.Errorf("accepted %s", key)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"event":"allocated","event":"result"}`), append(good, good...), bytes.Repeat([]byte("x"), 4097)} {
		if _, err := decodeDataEvent(raw, request); err == nil {
			t.Error("accepted malformed protocol")
		}
	}
}

func TestDataSuccessRequiresMatchingPythonEvidenceContract(t *testing.T) {
	for _, action := range []string{"run", "resume", "status"} {
		for _, result := range []string{"passed", "unsupported"} {
			request := dataChildRequest{Action: action, Token: testWarehouseSession}
			var value map[string]any
			if err := json.Unmarshal(dataTestLines(request, result)[1], &value); err != nil {
				t.Fatal(err)
			}
			badPlan, badAttempts := any(nil), any(0)
			if result == "unsupported" {
				badPlan = strings.Repeat("c", 32)
				badAttempts = 1
			}
			for key, bad := range map[string]any{"data_plan_token": badPlan, "catalog_attempts": badAttempts, "verification": "journal-only"} {
				badValue := map[string]any{}
				for k, v := range value {
					badValue[k] = v
				}
				badValue[key] = bad
				raw, _ := json.Marshal(badValue)
				if _, err := decodeDataEvent(raw, request); err == nil {
					t.Errorf("accepted %s/%s/%s", action, result, key)
				}
			}
		}
	}
}

type dataChildFunc func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error)

func (f dataChildFunc) Run(ctx context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	return f(ctx, r, emit)
}

func TestDataFinalSuccessNotReleasedBeforeVerifiedProcessExit(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	for _, broken := range []string{"exit", "extra", "timeout"} {
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			for _, line := range dataTestLines(r, "passed") {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true, PID: 99}, err
				}
			}
			result := dataChildResult{Started: true, PID: 99, ExitCode: 0}
			if broken == "exit" {
				result.ExitCode = 1
			}
			if broken == "extra" {
				return result, emit(dataTestLines(r, "passed")[1])
			}
			if broken == "timeout" {
				result.Unconfirmed = true
			}
			return result, nil
		})
		var out bytes.Buffer
		if err := runDataCommand(context.Background(), child, request, &out); err == nil || strings.Contains(out.String(), `"result":"passed"`) {
			t.Fatalf("%s success leaked: %s %v", broken, &out, err)
		}
	}
}

func TestDataOnlyConfirmedInterruptedExitCodesArePreserved(t *testing.T) {
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	for _, code := range []int{130, 143, -1, 0} {
		child := dataChildFunc(func(_ context.Context, _ dataChildRequest, _ func([]byte) error) (dataChildResult, error) {
			return dataChildResult{Started: true, PID: 99, ExitCode: code, Unconfirmed: true, Interrupted: true}, errDataInterrupted
		})
		var out bytes.Buffer
		err := runDataCommand(context.Background(), child, request, &out)
		want := 1
		if code == 130 || code == 143 {
			want = code
		}
		if ExitCode(err) != want || !strings.Contains(err.Error(), "unknown") || out.Len() != 0 {
			t.Fatalf("code=%d got=%d err=%v", code, ExitCode(err), err)
		}
	}
	child := dataChildFunc(func(_ context.Context, _ dataChildRequest, _ func([]byte) error) (dataChildResult, error) {
		return dataChildResult{Started: true, PID: 99, ExitCode: 130, Unconfirmed: true}, errors.New("protocol violation")
	})
	if ExitCode(runDataCommand(context.Background(), child, request, io.Discard)) != 1 {
		t.Fatal("protocol error became confirmed cancellation")
	}
}

func TestDataRealPythonBridgeWithFakeWorkflows(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, dataPython)
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python environment unavailable; no installation attempted")
	}
	// Exercise the real bridge and JSONL contract in an isolated child. Only the
	// workflow dependency is replaced; no runtime, model, or saved user data is read.
	harness := `
import contextlib, json, pathlib, sys, types
sys.path.insert(0, sys.argv[1]+'/tools/local_experiment/bears')
import data_catalog_cli as C
C.H.STATE=pathlib.Path(sys.argv[2])
outcome=sys.argv[3]
C.H.modules=lambda:(types.SimpleNamespace(load_config=lambda p:{'active_provider':'local'}, validate_config=lambda c:None, request_spec=lambda **kw:kw, SESSION=types.SimpleNamespace(interruption_handlers=contextlib.nullcontext)),None,None)
async def fake(config, token, **kwargs):
    assert kwargs['request']=='private-test-request'
    print('PRIVATE CHILD OUTPUT')
    print('PRIVATE CHILD ERROR',file=sys.stderr)
    return {'handoff_token':token,'result':outcome,'phase':'completed' if outcome=='passed' else 'unsupported','data_attempt_token':'a'*32,'data_plan_token':'b'*32 if outcome=='passed' else None,'catalog_attempts':1 if outcome=='passed' else 0,'replayed':False,'error_code':None}
C.H.run_handoff=fake
raise SystemExit(C.main(sys.argv[4:]))
`
	for _, outcome := range []string{"passed", "unsupported", "not_found"} {
		action := "run"
		if outcome == "not_found" {
			action = "status"
		}
		request := dataChildRequest{Root: root, Action: action, Token: testWarehouseSession, Request: []byte("private-test-request")}
		state := filepath.Join(t.TempDir(), "data-catalog")
		runner := execDataChild{command: func(r dataChildRequest) (*exec.Cmd, error) {
			args := []string{"-I", "-B", "-c", harness, root, state, outcome, r.Action, "--handoff-token", r.Token}
			if r.Action == "run" {
				args = append(args, "--request-stdin")
			}
			cmd := exec.Command(python, args...)
			cmd.Stdin = bytes.NewReader(r.Request)
			cmd.Dir = t.TempDir()
			return cmd, nil
		}}
		var out bytes.Buffer
		err := runDataCommand(context.Background(), runner, request, &out)
		want := 0
		if outcome == "unsupported" {
			want = 2
		}
		if outcome == "not_found" {
			want = 1
		}
		if ExitCode(err) != want || strings.Contains(out.String(), "PRIVATE") || strings.Contains(out.String(), "private-test-request") || !strings.Contains(out.String(), `"result":"`+outcome+`"`) {
			t.Fatalf("%s: exit=%d err=%v output=%s", outcome, ExitCode(err), err, &out)
		}
		if _, err := os.Stat(state); !errors.Is(err, os.ErrNotExist) {
			t.Fatal("fake bridge created state")
		}
	}
}

func TestDataChildUsesFixedIsolatedPythonAndStdinOnly(t *testing.T) {
	t.Setenv("PYTHONPATH", "/arbitrary/import")
	t.Setenv("PRIVATE_TOKEN", "never-inherit")
	r := dataChildRequest{Root: dataTestRoot(t), Action: "run", Token: testWarehouseSession, Request: []byte("private-request")}
	cmd, err := dataCommand(r)
	if err != nil {
		t.Fatal(err)
	}
	if cmd.Path != filepath.Join(r.Root, dataPython) || cmd.Dir != r.Root || strings.Contains(strings.Join(cmd.Args, " "), "private-request") || cmd.Args[1] != "-I" || cmd.Args[2] != "-B" {
		t.Fatalf("command %+v", cmd.Args)
	}
	for _, entry := range cmd.Env {
		if strings.HasPrefix(entry, "PYTHONPATH=") || strings.HasPrefix(entry, "PRIVATE_TOKEN=") {
			t.Fatal("ambient environment inherited")
		}
	}
	data, err := io.ReadAll(cmd.Stdin)
	if err != nil || !bytes.Equal(data, r.Request) {
		t.Fatal("stdin changed")
	}
	if dataCleanupGrace <= 180*time.Second {
		t.Fatal("cleanup grace too short")
	}
}

// The helper is the test executable itself: no Python, model, cluster, or API.
func TestDataSubprocessHelper(t *testing.T) {
	mode := os.Getenv("SHIROKUMA_DATA_TEST_HELPER")
	if mode == "" {
		return
	}
	request := dataChildRequest{Action: "run", Token: testWarehouseSession}
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt)
	fmt.Fprintln(os.Stderr, "PRIVATE STDERR MUST NEVER APPEAR")
	if mode == "oversize" {
		fmt.Println(strings.Repeat("private", 1000))
	} else {
		fmt.Println(string(dataTestLines(request, "passed")[0]))
	}
	switch mode {
	case "success":
		fmt.Println(string(dataTestLines(request, "passed")[1]))
		os.Exit(0)
	case "cancel":
		<-signals
		time.Sleep(20 * time.Millisecond)
		_ = os.WriteFile(os.Getenv("SHIROKUMA_DATA_TEST_MARKER"), []byte("cleaned"), 0600)
		os.Exit(130)
	case "hang":
		for {
			<-signals
		}
	case "oversize":
		<-signals
		os.Exit(1)
	}
	os.Exit(3)
}

func testDataProcess(mode, marker string) *exec.Cmd {
	cmd := exec.Command(os.Args[0], "-test.run=^TestDataSubprocessHelper$")
	cmd.Env = append(os.Environ(), "SHIROKUMA_DATA_TEST_HELPER="+mode, "SHIROKUMA_DATA_TEST_MARKER="+marker)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd
}

func TestDataSubprocessSuccessAndStderrSuppression(t *testing.T) {
	runner := execDataChild{command: func(dataChildRequest) (*exec.Cmd, error) { return testDataProcess("success", ""), nil }}
	var output bytes.Buffer
	err := runDataCommand(context.Background(), runner, dataChildRequest{Action: "run", Token: testWarehouseSession}, &output)
	if err != nil || strings.Contains(output.String(), "PRIVATE") || !strings.Contains(output.String(), `"result":"passed"`) {
		t.Fatalf("err=%v output=%s", err, &output)
	}
}

func TestDataCancellationWaitsForCleanupWithoutKillingChild(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "cleaned")
	runner := execDataChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return testDataProcess("cancel", marker), nil }}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result, err := runner.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if err == nil || !result.Unconfirmed || !result.Interrupted || result.ExitCode != 130 {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if raw, err := os.ReadFile(marker); err != nil || string(raw) != "cleaned" {
		t.Fatalf("cleanup not completed: %v", err)
	}
}

func TestDataDeadlineRemainsUnknownExitOneAfterChildInterruptCleanup(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "cleaned")
	runner := execDataChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) {
		return testDataProcess("cancel", marker), nil
	}}
	var observed dataChildResult
	child := dataChildFunc(func(ctx context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		result, err := runner.Run(ctx, request, emit)
		observed = result
		return result, err
	})
	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	var output bytes.Buffer
	err := runDataCommand(ctx, child, dataChildRequest{Action: "run", Token: testWarehouseSession}, &output)
	if ExitCode(err) != 1 || !strings.Contains(err.Error(), "unknown") || observed.Interrupted ||
		!observed.Unconfirmed || observed.ExitCode != 130 {
		t.Fatalf("deadline result=%+v CLI exit=%d err=%v", observed, ExitCode(err), err)
	}
	if raw, err := os.ReadFile(marker); err != nil || string(raw) != "cleaned" {
		t.Fatalf("deadline cleanup did not complete: %v", err)
	}
	if strings.Contains(output.String(), `"event":"result"`) {
		t.Fatal("deadline released a confirmed result")
	}
}

func TestDataCancellationDeadlineRetainsPIDAndDoesNotClaimExit(t *testing.T) {
	runner := execDataChild{grace: 30 * time.Millisecond, command: func(dataChildRequest) (*exec.Cmd, error) { return testDataProcess("hang", ""), nil }}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result, err := runner.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	defer syscall.Kill(result.PID, syscall.SIGKILL) // Only this disposable fake test process.
	if err == nil || !result.Started || !result.Unconfirmed || result.PID <= 0 || result.ExitCode != -1 {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	if err := syscall.Kill(result.PID, 0); err != nil {
		t.Fatalf("child was killed before cleanup grace handoff: %v", err)
	}
}

func TestDataOversizedOutputIsNotExposedAndGetsGracefulInterrupt(t *testing.T) {
	runner := execDataChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return testDataProcess("oversize", ""), nil }}
	var output bytes.Buffer
	err := runDataCommand(context.Background(), runner, dataChildRequest{Action: "run", Token: testWarehouseSession}, &output)
	if err == nil || strings.Contains(output.String(), "private") || strings.Contains(err.Error(), "PRIVATE") || !strings.Contains(err.Error(), "handoff_token="+testWarehouseSession) {
		t.Fatalf("err=%v output=%q", err, &output)
	}
}
