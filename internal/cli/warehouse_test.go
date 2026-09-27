package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const testWarehouseSession = "11111111111111111111111111111111"
const testWarehouseRequest = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
const testWarehouseOperation = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

func warehouseInt(value int) *int          { return &value }
func warehouseBool(value bool) *bool       { return &value }
func warehouseString(value string) *string { return &value }

func validWarehouseRequest(action string) warehouseTransitionRequest {
	return warehouseTransitionRequest{SchemaVersion: 1, SessionID: testWarehouseSession,
		RequestID: testWarehouseRequest, Warehouse: "XS", Action: action}
}

func validWarehouseResponse(request warehouseTransitionRequest) warehouseTransitionResponse {
	before, after := 1, 0
	if request.Action == "wake" {
		before, after = 0, 1
	}
	return warehouseTransitionResponse{SchemaVersion: 1, SessionID: request.SessionID,
		RequestID: request.RequestID, Warehouse: request.Warehouse, Action: request.Action,
		Result: "verified", Before: warehouseState{warehouseInt(before), warehouseBool(before == 1)},
		After:       warehouseState{warehouseInt(after), warehouseBool(after == 1)},
		OperationID: warehouseString(testWarehouseOperation), ReceiptSHA256: warehouseString(strings.Repeat("a", 64))}
}

type fakeWarehouseClient struct {
	requests []warehouseTransitionRequest
	call     func(context.Context, warehouseTransitionRequest) (warehouseTransitionResponse, error)
}

func (client *fakeWarehouseClient) Transition(ctx context.Context, request warehouseTransitionRequest) (warehouseTransitionResponse, error) {
	client.requests = append(client.requests, request)
	if client.call != nil {
		return client.call(ctx, request)
	}
	return validWarehouseResponse(request), nil
}

func warehouseCommand(t *testing.T, client warehouseTransitionClient, args ...string) (string, error) {
	t.Helper()
	var output bytes.Buffer
	root := newRootCommandWithWarehouse(&output, nil, client)
	root.SetArgs(args)
	err := root.Execute()
	return output.String(), err
}

func TestWarehouseCommandsPerformIndividualTypedRequests(t *testing.T) {
	for _, action := range []string{"chill", "wake"} {
		t.Run(action, func(t *testing.T) {
			client := &fakeWarehouseClient{}
			output, err := warehouseCommand(t, client, action, "XS", "--session", testWarehouseSession,
				"--profile", "local-experiment", "--request-id", testWarehouseRequest)
			if err != nil || len(client.requests) != 1 || client.requests[0] != validWarehouseRequest(action) {
				t.Fatalf("transition=%v, requests=%+v, output=%s", err, client.requests, output)
			}
			if _, err := decodeWarehouseResponse([]byte(output), client.requests[0]); err != nil {
				t.Fatalf("output is not a verified typed receipt: %v", err)
			}
		})
	}
}

func TestWarehouseCommandsRejectScopeBeforeCallingClient(t *testing.T) {
	tests := [][]string{
		{"chill", "S", "--session", testWarehouseSession},
		{"chill", "XS", "--session", "../escape"},
		{"chill", "XS"},
		{"wake", "XS", "--session", testWarehouseSession, "--profile", "production"},
		{"wake", "XS", "--session", testWarehouseSession, "--request-id", "not-a-uuid"},
		{"chill", "XS", "--session", testWarehouseSession, "--url", "http://external.invalid"},
		{"chill", "XS", "extra", "--session", testWarehouseSession},
	}
	for _, args := range tests {
		client := &fakeWarehouseClient{}
		if _, err := warehouseCommand(t, client, args...); err == nil || len(client.requests) != 0 {
			t.Errorf("args=%v err=%v requests=%v", args, err, client.requests)
		}
	}
}

func TestWarehouseRequestIDsAreCryptographicUUIDsAndDeadlineIsBounded(t *testing.T) {
	client := &fakeWarehouseClient{call: func(ctx context.Context, request warehouseTransitionRequest) (warehouseTransitionResponse, error) {
		deadline, ok := ctx.Deadline()
		remaining := time.Until(deadline)
		if !ok || remaining > warehouseRequestTimeout || remaining < 89*time.Second {
			t.Errorf("bounded deadline missing: remaining=%v", remaining)
		}
		if !warehouseUUIDPattern.MatchString(request.RequestID) || request.RequestID[14] != '4' || !strings.ContainsRune("89ab", rune(request.RequestID[19])) {
			t.Errorf("invalid generated UUID: %s", request.RequestID)
		}
		return validWarehouseResponse(request), nil
	}}
	for range 2 {
		if _, err := warehouseCommand(t, client, "chill", "XS", "--session", testWarehouseSession); err != nil {
			t.Fatal(err)
		}
	}
	if client.requests[0].RequestID == client.requests[1].RequestID {
		t.Fatal("fresh requests reused the same random UUID")
	}
	if _, err := newWarehouseRequestID(strings.NewReader("short")); err == nil {
		t.Fatal("short random source accepted")
	}
}

func TestWarehouseLostReplyPreservesRequestIDWithoutRawError(t *testing.T) {
	client := &fakeWarehouseClient{call: func(context.Context, warehouseTransitionRequest) (warehouseTransitionResponse, error) {
		return warehouseTransitionResponse{}, errors.New("raw Authorization private-token")
	}}
	output, err := warehouseCommand(t, client, "chill", "XS", "--session", testWarehouseSession, "--request-id", testWarehouseRequest)
	if err == nil || len(client.requests) != 1 || output != "" || strings.Contains(err.Error(), "private-token") ||
		!strings.Contains(err.Error(), "warehouse saved-request --session "+testWarehouseSession+" --request-id "+testWarehouseRequest) ||
		!strings.Contains(err.Error(), "read-only") || !strings.Contains(err.Error(), "same owner is alive") ||
		!strings.Contains(err.Error(), "never resubmit after owner exit") {
		t.Fatalf("unsafe or ambiguous failure: output=%q err=%v", output, err)
	}
}

func TestWarehouseGeneratedIDOnLostReplyCanOnlyBeLookedUpUnderOriginalSession(t *testing.T) {
	client := &fakeWarehouseClient{call: func(context.Context, warehouseTransitionRequest) (warehouseTransitionResponse, error) {
		return warehouseTransitionResponse{}, context.DeadlineExceeded
	}}
	output, err := warehouseCommand(t, client, "wake", "XS", "--session", testWarehouseSession)
	if err == nil || output != "" || len(client.requests) != 1 ||
		!strings.Contains(err.Error(), "warehouse saved-request --session "+testWarehouseSession+" --request-id "+client.requests[0].RequestID) ||
		!strings.Contains(err.Error(), "never resubmit after owner exit") {
		t.Fatalf("generated request identity lost: output=%q err=%v", output, err)
	}
}

func TestWarehouseNonSuccessfulResultsAreNonzeroAndPreserveUnknown(t *testing.T) {
	for _, result := range []string{"busy", "unknown", "failed"} {
		client := &fakeWarehouseClient{call: func(_ context.Context, request warehouseTransitionRequest) (warehouseTransitionResponse, error) {
			report := validWarehouseResponse(request)
			report.Result, report.Before, report.After = result, warehouseState{}, warehouseState{}
			report.OperationID, report.ReceiptSHA256 = nil, nil
			return report, nil
		}}
		output, err := warehouseCommand(t, client, "wake", "XS", "--session", testWarehouseSession)
		if err == nil || !strings.Contains(output, `"replicas":null`) || !strings.Contains(output, `"result":"`+result+`"`) {
			t.Errorf("result=%s output=%s err=%v", result, output, err)
		}
	}
}

func TestWarehouseSuccessMustMatchActualTransitionAndEvidence(t *testing.T) {
	request := validWarehouseRequest("chill")
	tests := []struct {
		name   string
		change func(*warehouseTransitionResponse)
	}{
		{"session", func(r *warehouseTransitionResponse) { r.SessionID = strings.Repeat("2", 32) }},
		{"request", func(r *warehouseTransitionResponse) { r.RequestID = testWarehouseOperation }},
		{"action", func(r *warehouseTransitionResponse) { r.Action = "wake" }},
		{"unknown state", func(r *warehouseTransitionResponse) { r.After.Replicas = nil }},
		{"not scaled", func(r *warehouseTransitionResponse) { r.After.Replicas = warehouseInt(1) }},
		{"route active", func(r *warehouseTransitionResponse) { r.After.BackendActive = warehouseBool(true) }},
		{"no operation", func(r *warehouseTransitionResponse) { r.OperationID = nil }},
		{"no receipt", func(r *warehouseTransitionResponse) { r.ReceiptSHA256 = nil }},
		{"bad receipt", func(r *warehouseTransitionResponse) { r.ReceiptSHA256 = warehouseString("not-a-hash") }},
		{"outcome", func(r *warehouseTransitionResponse) { r.Result = "passed" }},
		{"bad noop", func(r *warehouseTransitionResponse) { r.Result = "noop" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			report := validWarehouseResponse(request)
			test.change(&report)
			data, _ := json.Marshal(report)
			if _, err := decodeWarehouseResponse(data, request); err == nil {
				t.Fatal("inconsistent success accepted")
			}
		})
	}
	for _, action := range []string{"chill", "wake"} {
		request := validWarehouseRequest(action)
		report := validWarehouseResponse(request)
		report.Result, report.Before = "noop", report.After
		if err := validateWarehouseResponse(report, request); err != nil {
			t.Fatalf("evidenced noop rejected: %v", err)
		}
	}
}

func TestWarehouseJSONRejectsDuplicateMissingExtraAndNonfinite(t *testing.T) {
	request := validWarehouseRequest("chill")
	data, _ := json.Marshal(validWarehouseResponse(request))
	valid := string(data)
	inputs := []string{
		strings.Replace(valid, `"result":"verified"`, `"result":"failed","result":"verified"`, 1),
		strings.Replace(valid, `"replicas":0`, `"replicas":1,"replicas":0`, 1),
		strings.Replace(valid, `"replicas":0`, `"replicas":NaN`, 1),
		strings.Replace(valid, `"replicas":0`, `"replicas":0.0`, 1),
		strings.Replace(valid, `"replicas":0,`, "", 1),
		strings.Replace(valid, `"action":"chill",`, "", 1),
		strings.Replace(valid, `"after":{`, `"after":{"sql":"bad",`, 1),
		strings.Replace(valid, `"after":{`, `"extra":1,"after":{`, 1),
		valid + `{}`, `[]`, `null`, strings.Repeat(" ", warehouseResponseLimit+1) + valid,
	}
	for index, input := range inputs {
		if _, err := decodeWarehouseResponse([]byte(input), request); err == nil {
			t.Errorf("invalid JSON #%d accepted", index)
		}
	}
}

func privateWarehouseTestSocket(t *testing.T, handler http.Handler) (string, *http.Server) {
	t.Helper()
	base := "/private/tmp"
	if _, err := os.Stat(base); err != nil {
		base = os.TempDir()
	}
	base, err := filepath.EvalSymlinks(base)
	if err != nil {
		t.Fatal(err)
	}
	directory, err := os.MkdirTemp(base, "shwl-cli-")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "control.sock")
	listener, err := net.Listen("unix", path)
	if err != nil {
		os.RemoveAll(directory)
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0600); err != nil {
		listener.Close()
		os.RemoveAll(directory)
		t.Fatal(err)
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: time.Second}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close(); os.RemoveAll(directory) })
	return path, server
}

func TestWarehouseUnixHTTPUsesExactRequestAndIgnoresProxy(t *testing.T) {
	request := validWarehouseRequest("chill")
	var observed atomic.Int32
	path, _ := privateWarehouseTestSocket(t, http.HandlerFunc(func(writer http.ResponseWriter, httpRequest *http.Request) {
		observed.Add(1)
		if httpRequest.Method != "POST" || httpRequest.URL.Path != "/v1/transition" || httpRequest.URL.RawQuery != "" || httpRequest.Host != "warehouse.local" {
			t.Errorf("unexpected request: %s %s host=%s", httpRequest.Method, httpRequest.URL, httpRequest.Host)
		}
		data, _ := io.ReadAll(httpRequest.Body)
		want, _ := json.Marshal(request)
		if !bytes.Equal(data, want) {
			t.Errorf("request=%s want=%s", data, want)
		}
		writer.Header().Set("Content-Type", "application/json")
		json.NewEncoder(writer).Encode(validWarehouseResponse(request))
	}))
	t.Setenv("HTTP_PROXY", "http://127.0.0.1:1")
	t.Setenv("ALL_PROXY", "http://127.0.0.1:1")
	report, err := transitionAtWarehouseSocket(context.Background(), request, path)
	if err != nil || report.Result != "verified" || observed.Load() != 1 {
		t.Fatalf("report=%+v err=%v observed=%d", report, err, observed.Load())
	}
	if got := warehouseSocketPath(testWarehouseSession); got != "/private/tmp/shirokuma-wl-"+testWarehouseSession+"/control.sock" {
		t.Fatalf("unexpected session path %s", got)
	}
}

func TestWarehouseSocketOwnershipPermissionsAndSymlinks(t *testing.T) {
	path, _ := privateWarehouseTestSocket(t, http.NotFoundHandler())
	if _, err := checkWarehouseSocket(path); err != nil {
		t.Fatal(err)
	}
	for _, change := range []struct {
		path string
		mode os.FileMode
	}{
		{path, 0660}, {filepath.Dir(path), 0755},
	} {
		original, _ := os.Stat(change.path)
		if err := os.Chmod(change.path, change.mode); err != nil {
			t.Fatal(err)
		}
		if _, err := checkWarehouseSocket(path); err == nil {
			t.Fatal("broad permissions accepted")
		}
		if err := os.Chmod(change.path, original.Mode().Perm()); err != nil {
			t.Fatal(err)
		}
	}
	link := filepath.Join(filepath.Dir(path), "link.sock")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if _, err := checkWarehouseSocket(link); err == nil {
		t.Fatal("socket symlink accepted")
	}
	regular := filepath.Join(filepath.Dir(path), "file.sock")
	if err := os.WriteFile(regular, []byte("not a socket"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := checkWarehouseSocket(regular); err == nil {
		t.Fatal("regular file accepted")
	}
	parentLink := filepath.Dir(path) + "-link"
	if err := os.Symlink(filepath.Dir(path), parentLink); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Remove(parentLink) })
	if _, err := checkWarehouseSocket(filepath.Join(parentLink, "control.sock")); err == nil {
		t.Fatal("parent symlink accepted")
	}
}

func TestWarehouseHTTPResultStatusRedirectAndBodyBounds(t *testing.T) {
	request := validWarehouseRequest("chill")
	tests := []struct {
		name        string
		status      int
		result      string
		body        string
		contentType string
		accepted    bool
	}{
		{"success", 200, "verified", "", "application/json", true},
		{"wrong status", 409, "verified", "", "application/json", false},
		{"busy", 409, "busy", "", "application/json", true},
		{"unknown", 409, "unknown", "", "application/json", true},
		{"failed", 500, "failed", "", "application/json", true},
		{"wrong success", 200, "failed", "", "application/json", false},
		{"redirect", 307, "verified", "", "application/json", false},
		{"huge", 200, "", strings.Repeat("x", warehouseResponseLimit+1), "application/json", false},
		{"truncated", 200, "", `{`, "application/json", false},
		{"content type", 200, "verified", "", "text/plain", false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var calls atomic.Int32
			path, _ := privateWarehouseTestSocket(t, http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				calls.Add(1)
				writer.Header().Set("Content-Type", test.contentType)
				writer.Header().Set("Location", "http://127.0.0.1:1/never-follow")
				writer.WriteHeader(test.status)
				if test.body != "" {
					io.WriteString(writer, test.body)
					return
				}
				report := validWarehouseResponse(request)
				report.Result = test.result
				json.NewEncoder(writer).Encode(report)
			}))
			_, err := transitionAtWarehouseSocket(context.Background(), request, path)
			if (err == nil) != test.accepted || calls.Load() != 1 {
				t.Fatalf("err=%v accepted=%v calls=%d", err, test.accepted, calls.Load())
			}
		})
	}
}

func TestWarehouseHTTPCancellationDoesNotStopSessionServer(t *testing.T) {
	request := validWarehouseRequest("chill")
	var calls atomic.Int32
	started := make(chan struct{})
	path, _ := privateWarehouseTestSocket(t, http.HandlerFunc(func(writer http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			close(started)
			<-r.Context().Done()
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		json.NewEncoder(writer).Encode(validWarehouseResponse(request))
	}))
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { _, err := transitionAtWarehouseSocket(ctx, request, path); done <- err }()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("request did not reach session")
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled HTTP request succeeded")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("HTTP cancellation did not return")
	}
	if report, err := transitionAtWarehouseSocket(context.Background(), request, path); err != nil || report.Result != "verified" {
		t.Fatalf("session was stopped by HTTP cancellation: report=%+v err=%v", report, err)
	}
}
