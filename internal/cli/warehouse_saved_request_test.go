package cli

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeWarehouseSavedChild struct {
	calls   int
	request dataChildRequest
	lines   [][]byte
	result  dataChildResult
	err     error
}

func (child *fakeWarehouseSavedChild) Run(_ context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
	child.calls++
	child.request = request
	for _, line := range child.lines {
		if err := emit(line); err != nil {
			return child.result, err
		}
	}
	return child.result, child.err
}

func savedLookupLine(session, request, observation string) []byte {
	return []byte(`{"schema_version":1,"session_id":"` + session + `","journal_sha256":"` + strings.Repeat("a", 64) +
		`","observation":"saved-journal-only","runtime_state":"unknown","recorded_actions":[],"reply_count":0,"recorded_error_count":0,"request_lookup":{"request_id":"` + request +
		`","observation":"` + observation + `","recorded_action":null,"recorded_result":null,"operation_id":null,"receipt_sha256":null}}`)
}

func TestWarehouseSavedRequestNeverEmitsPartialOrUnconfirmedOutput(t *testing.T) {
	request := dataChildRequest{Root: "/unused", Action: "saved-request", Token: testWarehouseSession, Case: testWarehouseRequest}
	good := savedLookupLine(request.Token, request.Case, "not-recorded-in-snapshot")
	for _, tc := range []struct {
		name   string
		lines  [][]byte
		result dataChildResult
		err    error
		pass   bool
	}{
		{"valid absence is not failure", [][]byte{good}, dataChildResult{Started: true, ExitCode: 0}, nil, true},
		{"missing", nil, dataChildResult{Started: true, ExitCode: 0}, nil, false},
		{"two lines", [][]byte{good, good}, dataChildResult{Started: true, ExitCode: 0}, nil, false},
		{"wrong session", [][]byte{savedLookupLine(strings.Repeat("f", 32), request.Case, "not-recorded-in-snapshot")}, dataChildResult{Started: true, ExitCode: 0}, nil, false},
		{"wrong UUID", [][]byte{savedLookupLine(request.Token, testWarehouseOperation, "not-recorded-in-snapshot")}, dataChildResult{Started: true, ExitCode: 0}, nil, false},
		{"claimed live", [][]byte{bytes.Replace(good, []byte(`"runtime_state":"unknown"`), []byte(`"runtime_state":"ready"`), 1)}, dataChildResult{Started: true}, nil, false},
		{"false success", [][]byte{savedLookupLine(request.Token, request.Case, "recorded-in-snapshot")}, dataChildResult{Started: true}, nil, false},
		{"nonzero exit", [][]byte{good}, dataChildResult{Started: true, ExitCode: 1}, nil, false},
		{"interrupted", [][]byte{good}, dataChildResult{Started: true, Unconfirmed: true}, errors.New("PRIVATE"), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			child := &fakeWarehouseSavedChild{lines: tc.lines, result: tc.result, err: tc.err}
			var out bytes.Buffer
			err := runWarehouseSavedRequest(context.Background(), child, request, &out)
			if (err == nil) != tc.pass || child.calls != 1 {
				t.Fatalf("unexpected outcome: %v calls=%d", err, child.calls)
			}
			if tc.pass && out.String() != string(good)+"\n" || !tc.pass && out.Len() != 0 {
				t.Fatalf("unexpected output: %q", out.String())
			}
			if err != nil && strings.Contains(err.Error(), "PRIVATE") {
				t.Fatal("child diagnostics escaped")
			}
		})
	}
}

func TestWarehouseSavedRequestCorrelatesLookupWithActions(t *testing.T) {
	request := dataChildRequest{Action: "saved-request", Token: testWarehouseSession, Case: testWarehouseRequest}
	base := savedLookupLine(request.Token, request.Case, "recorded-in-snapshot")
	verified := bytes.Replace(base, []byte(`"recorded_actions":[]`), []byte(`"recorded_actions":[{"action":"query","recorded_result":"verified"}]`), 1)
	verified = bytes.Replace(verified, []byte(`"recorded_action":null,"recorded_result":null`), []byte(`"recorded_action":"query","recorded_result":"verified"`), 1)
	verified = bytes.Replace(verified, []byte(`"operation_id":null,"receipt_sha256":null`), []byte(`"operation_id":"`+testWarehouseOperation+`","receipt_sha256":"`+strings.Repeat("b", 64)+`"`), 1)
	if err := validateWarehouseSavedOutput(verified, request); err != nil {
		t.Fatalf("valid saved lookup rejected: %v", err)
	}
	for _, bad := range [][]byte{
		bytes.Replace(verified, []byte(`"recorded_result":"verified"`), []byte(`"recorded_result":"failed"`), 1),
		bytes.Replace(verified, []byte(`"runtime_state":"unknown"`), []byte(`"runtime_state":"verified"`), 1),
		append(bytes.Clone(verified), []byte(` {}`)...),
		bytes.Replace(verified, []byte(`"request_id":"`+request.Case+`"`), []byte(`"request_id":"`+request.Case+`","request_id":"`+request.Case+`"`), 1),
	} {
		if err := validateWarehouseSavedOutput(bad, request); err == nil {
			t.Fatalf("unsafe saved response accepted: %s", bad)
		}
	}
}

func completedSavedLookup(session, request string) []byte {
	base := savedLookupLine(session, request, "recorded-in-snapshot")
	base = bytes.Replace(base, []byte(`"recorded_actions":[]`), []byte(`"recorded_actions":[{"action":"wake","recorded_result":"verified"}]`), 1)
	base = bytes.Replace(base, []byte(`"reply_count":0`), []byte(`"reply_count":1`), 1)
	base = bytes.Replace(base, []byte(`"recorded_action":null,"recorded_result":null`), []byte(`"recorded_action":"wake","recorded_result":"verified"`), 1)
	base = bytes.Replace(base, []byte(`"operation_id":null,"receipt_sha256":null`), []byte(`"operation_id":"`+testWarehouseOperation+`","receipt_sha256":"`+strings.Repeat("b", 64)+`"`), 1)
	return bytes.Replace(base, []byte(`"request_lookup":`), []byte(`"recorded_completion":"correlated-saved-claim","saved_receipt_sha256":"`+strings.Repeat("c", 64)+`","operation_count":1,"request_lookup":`), 1)
}

func TestWarehouseSavedRequestCompletionIsOptInAndFailClosed(t *testing.T) {
	request := dataChildRequest{Action: "saved-request-completion", Token: testWarehouseSession, Case: testWarehouseRequest}
	good := completedSavedLookup(request.Token, request.Case)
	otherFailed := bytes.Replace(good, []byte(`"recorded_actions":[{"action":"wake","recorded_result":"verified"}]`), []byte(`"recorded_actions":[{"action":"wake","recorded_result":"verified"},{"action":"chill","recorded_result":"failed"}]`), 1)
	otherFailed = bytes.Replace(otherFailed, []byte(`"operation_count":1`), []byte(`"operation_count":2`), 1)
	otherFailed = bytes.Replace(otherFailed, []byte(`"reply_count":1`), []byte(`"reply_count":2`), 1)
	missingUUID := bytes.Replace(good, []byte(`"observation":"recorded-in-snapshot"`), []byte(`"observation":"not-recorded-in-snapshot"`), 1)
	missingUUID = bytes.Replace(missingUUID, []byte(`"recorded_action":"wake","recorded_result":"verified","operation_id":"`+testWarehouseOperation+`","receipt_sha256":"`+strings.Repeat("b", 64)+`"`), []byte(`"recorded_action":null,"recorded_result":null,"operation_id":null,"receipt_sha256":null`), 1)
	if err := validateWarehouseSavedOutput(good, request); err != nil {
		t.Fatalf("correlated completion rejected: %v", err)
	}
	journalOnly := request
	journalOnly.Action = "saved-request"
	if validateWarehouseSavedOutput(good, journalOnly) == nil {
		t.Fatal("opt-in completion was accepted by the journal-only command")
	}
	for _, bad := range [][]byte{
		otherFailed,
		missingUUID,
		bytes.Replace(good, []byte(`,"saved_receipt_sha256":"`+strings.Repeat("c", 64)+`"`), nil, 1),
		bytes.Replace(good, []byte(`"recorded_completion":"correlated-saved-claim"`), []byte(`"recorded_completion":"verified"`), 1),
		bytes.Replace(good, []byte(`"operation_count":1`), []byte(`"operation_count":0`), 1),
		bytes.Replace(good, []byte(`"reply_count":1`), []byte(`"reply_count":0`), 1),
		bytes.Replace(good, []byte(`"recorded_error_count":0`), []byte(`"recorded_error_count":1`), 1),
		bytes.Replace(good, []byte(`"recorded_result":"verified"`), []byte(`"recorded_result":"pending"`), 2),
		bytes.Replace(good, []byte(`"runtime_state":"unknown"`), []byte(`"runtime_state":"ready"`), 1),
		bytes.Replace(good, []byte(`"recorded_completion":"correlated-saved-claim"`), []byte(`"recorded_completion":"correlated-saved-claim","recorded_completion":"correlated-saved-claim"`), 1),
		savedLookupLine(request.Token, request.Case, "not-recorded-in-snapshot"),
	} {
		child := &fakeWarehouseSavedChild{lines: [][]byte{bad}, result: dataChildResult{Started: true}}
		var output bytes.Buffer
		if err := runWarehouseSavedRequest(context.Background(), child, request, &output); err == nil || output.Len() != 0 {
			t.Fatalf("unverified completion emitted: %s", bad)
		}
	}
	child := &fakeWarehouseSavedChild{lines: [][]byte{good}, result: dataChildResult{Started: true}}
	var output bytes.Buffer
	if err := runWarehouseSavedRequest(context.Background(), child, request, &output); err != nil || output.String() != string(good)+"\n" {
		t.Fatalf("correlated completion not delivered: %v", err)
	}
}

func TestWarehouseSavedRequestCompletionFlagUsesOnlyFixedChild(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, dataPython)); err != nil {
		t.Skip("dedicated Python not installed")
	}
	request := dataChildRequest{Root: root, Action: "saved-request-completion", Token: testWarehouseSession, Case: testWarehouseRequest}
	cmd, err := warehouseSavedRequestCommand(request)
	if err != nil || len(cmd.Args) != 8 || cmd.Args[4] != request.Token || cmd.Args[5] != "--request-id" || cmd.Args[6] != request.Case || cmd.Args[7] != "--with-saved-completion" || len(cmd.Env) != 0 {
		t.Fatalf("unsafe completion child: %v %+v", err, cmd)
	}
	child := &fakeWarehouseSavedChild{lines: [][]byte{completedSavedLookup(request.Token, request.Case)}, result: dataChildResult{Started: true}}
	group := newWarehouseQueryCommand(unixWarehouseClient{})
	group.AddCommand(newWarehouseSavedRequestCommand(child))
	group.SetArgs([]string{"saved-request", "--session", request.Token, "--request-id", request.Case, "--with-saved-completion"})
	group.SilenceErrors, group.SilenceUsage = true, true
	group.SetErr(io.Discard)
	var output bytes.Buffer
	group.SetOut(&output)
	if err := group.Execute(); err != nil || child.calls != 1 || child.request.Action != request.Action || !bytes.Contains(output.Bytes(), []byte(`"recorded_completion":"correlated-saved-claim"`)) {
		t.Fatalf("completion flag did not require saved correlation: %v calls=%d action=%s", err, child.calls, child.request.Action)
	}
}

func TestWarehouseSavedRequestRejectsInputBeforeChild(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"saved-request", "XS", "--session", testWarehouseSession, "--request-id", testWarehouseRequest},
		{"saved-request", "--session", "../outside", "--request-id", testWarehouseRequest},
		{"saved-request", "--session", testWarehouseSession, "--request-id", "BAD"},
		{"saved-request", "--session", testWarehouseSession, "--request-id", testWarehouseRequest, "--profile", "production"},
		{"saved-request", "--session", testWarehouseSession, "--request-id", testWarehouseRequest, "--url", "http://host"},
	} {
		child := &fakeWarehouseSavedChild{}
		group := newWarehouseQueryCommand(unixWarehouseClient{})
		group.AddCommand(newWarehouseSavedRequestCommand(child))
		group.SetArgs(args)
		group.SilenceErrors, group.SilenceUsage = true, true
		group.SetOut(io.Discard)
		group.SetErr(io.Discard)
		if err := group.Execute(); err == nil || child.calls != 0 {
			t.Fatalf("input reached child: %v", args)
		}
	}
	for _, request := range []dataChildRequest{
		{Root: root, Action: "saved-request", Token: "../bad", Case: testWarehouseRequest},
		{Root: root, Action: "saved-request", Token: testWarehouseSession, Case: "../bad"},
		{Root: root, Action: "run", Token: testWarehouseSession, Case: testWarehouseRequest},
	} {
		if _, err := warehouseSavedRequestCommand(request); err == nil {
			t.Fatal("invalid child invocation accepted")
		}
	}
}

func TestWarehouseSavedRequestChildIsFixedAndHasNoInheritedEnvironment(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, dataPython)); err != nil {
		t.Skip("dedicated Python not installed")
	}
	request := dataChildRequest{Root: root, Action: "saved-request", Token: testWarehouseSession, Case: testWarehouseRequest}
	cmd, err := warehouseSavedRequestCommand(request)
	if err != nil || len(cmd.Args) != 7 || cmd.Args[4] != request.Token || cmd.Args[5] != "--request-id" || cmd.Args[6] != request.Case || len(cmd.Env) != 0 {
		t.Fatalf("unsafe child: %v %+v", err, cmd)
	}
}
