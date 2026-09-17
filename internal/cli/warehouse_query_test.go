package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type fakeWarehouseQueryClient struct {
	requests []warehouseTransitionRequest
	call     func(context.Context, warehouseTransitionRequest) (warehouseQueryResponse, error)
}

func (client *fakeWarehouseQueryClient) Query(ctx context.Context, r warehouseTransitionRequest) (warehouseQueryResponse, error) {
	client.requests = append(client.requests, r)
	if client.call != nil {
		return client.call(ctx, r)
	}
	return validWarehouseQueryResponse(r), nil
}

func validWarehouseQueryResponse(r warehouseTransitionRequest) warehouseQueryResponse {
	wake := r
	wake.Action = "wake"
	base := validWarehouseResponse(wake)
	base.Action = "query"
	return warehouseQueryResponse{warehouseTransitionResponse: base, Query: &warehouseQueryResult{
		Case: "xs_caller", QueryID: "20260918_010203_00001_abcde", RowsObserved: 25,
		RowsSHA256: strings.Repeat("b", 64), PostCount: 1, ResponseComplete: true, Matched: true}}
}

func warehouseQueryCommand(t *testing.T, client warehouseQueryClient, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := newWarehouseQueryCommand(client)
	cmd.SilenceErrors, cmd.SilenceUsage = true, true
	cmd.SetArgs(args)
	cmd.SetOut(&out)
	cmd.SetErr(io.Discard)
	err := cmd.Execute()
	return out.String(), err
}

func TestWarehouseQuerySingleFixedRequestAndIndependentDeadline(t *testing.T) {
	client := &fakeWarehouseQueryClient{call: func(ctx context.Context, r warehouseTransitionRequest) (warehouseQueryResponse, error) {
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > 240*time.Second || time.Until(deadline) < 239*time.Second {
			t.Fatal("query wait does not have its own bounded deadline")
		}
		return validWarehouseQueryResponse(r), nil
	}}
	out, err := warehouseQueryCommand(t, client, "query", "XS", "--session", testWarehouseSession, "--request-id", testWarehouseRequest)
	if err != nil || len(client.requests) != 1 || client.requests[0] != validWarehouseRequest("query") ||
		!strings.Contains(out, `"post_count":1`) || !strings.Contains(out, `"case":"xs_caller"`) {
		t.Fatalf("request/response mismatch: %v %s", err, out)
	}
	if warehouseRequestTimeout != 90*time.Second {
		t.Fatal("legacy timeout changed")
	}
	client.requests = nil
	if _, err := warehouseQueryCommand(t, client, "query", "XS", "--session", testWarehouseSession); err != nil ||
		len(client.requests) != 1 || !warehouseUUIDPattern.MatchString(client.requests[0].RequestID) {
		t.Fatal("automatic cryptographic request identity missing")
	}
}

func TestWarehouseQueryRejectsArbitraryInputBeforeRPC(t *testing.T) {
	for _, args := range [][]string{
		{"query", "S", "--session", testWarehouseSession}, {"query", "XS"},
		{"query", "XS", "--session", "../escape"},
		{"query", "XS", "--session", testWarehouseSession, "--request-id", "invalid"},
		{"query", "XS", "--session", testWarehouseSession, "--profile", "production"},
		{"query", "XS", "--session", testWarehouseSession, "--sql", "SELECT 1"},
		{"query", "XS", "--session", testWarehouseSession, "--case", "other"},
		{"query", "XS", "--session", testWarehouseSession, "--url", "https://example.com"},
		{"query", "XS", "extra", "--session", testWarehouseSession},
	} {
		client := &fakeWarehouseQueryClient{}
		if _, err := warehouseQueryCommand(t, client, args...); err == nil || len(client.requests) != 0 {
			t.Fatalf("invalid command reached RPC: %v", args)
		}
	}
}

func TestWarehouseQueryLostReplyNeverRetriesAndRetainsIdentity(t *testing.T) {
	for _, failure := range []error{errors.New("PRIVATE token body"), context.Canceled, context.DeadlineExceeded} {
		client := &fakeWarehouseQueryClient{call: func(context.Context, warehouseTransitionRequest) (warehouseQueryResponse, error) {
			return warehouseQueryResponse{}, failure
		}}
		out, err := warehouseQueryCommand(t, client, "query", "XS", "--session", testWarehouseSession, "--request-id", testWarehouseRequest)
		if err == nil || len(client.requests) != 1 || out != "" || strings.Contains(err.Error(), "PRIVATE") ||
			!strings.Contains(err.Error(), testWarehouseRequest) || !strings.Contains(err.Error(), testWarehouseSession) ||
			!strings.Contains(err.Error(), "15-second") {
			t.Fatalf("uncertain reply contract lost: %v %s", err, out)
		}
	}
}

func TestWarehouseQueryRequiresOneCompleteCallerResultAndExactWakeState(t *testing.T) {
	request := validWarehouseRequest("query")
	for _, change := range []func(*warehouseQueryResponse){
		func(r *warehouseQueryResponse) { r.Result = "noop" },
		func(r *warehouseQueryResponse) { r.Action = "wake" },
		func(r *warehouseQueryResponse) { r.SessionID = strings.Repeat("2", 32) },
		func(r *warehouseQueryResponse) { r.RequestID = testWarehouseOperation },
		func(r *warehouseQueryResponse) { r.Before = r.After },
		func(r *warehouseQueryResponse) { r.After.BackendActive = warehouseBool(false) },
		func(r *warehouseQueryResponse) { r.OperationID = nil },
		func(r *warehouseQueryResponse) { r.ReceiptSHA256 = nil },
		func(r *warehouseQueryResponse) { r.Query = nil },
		func(r *warehouseQueryResponse) { r.Query.Case = "xs_after" },
		func(r *warehouseQueryResponse) { r.Query.QueryID = "foreign" },
		func(r *warehouseQueryResponse) { r.Query.PostCount = 2 },
		func(r *warehouseQueryResponse) { r.Query.RowsObserved = 24 },
		func(r *warehouseQueryResponse) { r.Query.RowsSHA256 = "bad" },
		func(r *warehouseQueryResponse) { r.Query.ResponseComplete = false },
		func(r *warehouseQueryResponse) { r.Query.Matched = false },
	} {
		report := validWarehouseQueryResponse(request)
		change(&report)
		if validateWarehouseQueryResponse(report, request) == nil {
			t.Fatalf("unverified caller accepted: %+v", report)
		}
	}
	for _, result := range []string{"busy", "unknown", "failed"} {
		report := validWarehouseQueryResponse(request)
		report.Result = result
		if validateWarehouseQueryResponse(report, request) == nil {
			t.Fatal("partial failure exposed complete query proof")
		}
		report.Query = nil
		if validateWarehouseQueryResponse(report, request) != nil {
			t.Fatal("valid unsuccessful outcome was not retained")
		}
		client := &fakeWarehouseQueryClient{call: func(context.Context, warehouseTransitionRequest) (warehouseQueryResponse, error) { return report, nil }}
		out, err := warehouseQueryCommand(t, client, "query", "XS", "--session", testWarehouseSession, "--request-id", testWarehouseRequest)
		if err == nil || !strings.Contains(out, `"query":null`) || len(client.requests) != 1 {
			t.Fatal("failed query promoted or retried")
		}
	}
}

func TestWarehouseQueryJSONRejectsExtraMissingDuplicateAndWrongTypes(t *testing.T) {
	r := validWarehouseRequest("query")
	raw, _ := json.Marshal(validWarehouseQueryResponse(r))
	valid := string(raw)
	if _, err := decodeWarehouseQueryResponse(raw, r); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{
		strings.Replace(valid, `"post_count":1`, `"post_count":true`, 1),
		strings.Replace(valid, `"post_count":1`, `"post_count":1.0`, 1),
		strings.Replace(valid, `"post_count":1`, `"post_count":null`, 1),
		strings.Replace(valid, `"post_count":1`, `"post_count":2,"post_count":1`, 1),
		strings.Replace(valid, `"matched":true`, `"matched":true,"sql":"PRIVATE"`, 1),
		strings.Replace(valid, `,"matched":true`, ``, 1),
		strings.Replace(valid, `"rows_observed":25`, `"rows_observed":[25]`, 1),
		strings.Replace(valid, `"replicas":0`, `"replicas":false`, 1),
		valid + `{}`, `null`, strings.Repeat(" ", warehouseResponseLimit+1) + valid,
	} {
		if _, err := decodeWarehouseQueryResponse([]byte(bad), r); err == nil {
			t.Fatal("invalid query response accepted")
		}
	}
	if _, err := decodeWarehouseResponse(raw, r); err == nil {
		t.Fatal("query result was accepted by the legacy exact response parser")
	}
}

func TestWarehouseQueryUnixHTTPFixedProtocolAndStatusCoherence(t *testing.T) {
	request := validWarehouseRequest("query")
	for _, status := range []int{200, 409, 302} {
		calls := 0
		path, server := privateWarehouseTestSocket(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			calls++
			if r.Method != "POST" || r.URL.Path != "/v1/transition" || r.Host != "warehouse.local" || r.URL.RawQuery != "" {
				t.Error("fixed RPC target changed")
			}
			var got map[string]json.RawMessage
			if json.NewDecoder(r.Body).Decode(&got) != nil || !warehouseKeys(got, "schema_version", "session_id", "request_id", "warehouse", "action") {
				t.Error("arbitrary query input crossed RPC")
			}
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Location", "https://example.com/PRIVATE")
			w.WriteHeader(status)
			_ = json.NewEncoder(w).Encode(validWarehouseQueryResponse(request))
		}))
		t.Setenv("HTTP_PROXY", "http://127.0.0.1:1")
		_, err := queryAtWarehouseSocket(context.Background(), request, path)
		server.Close()
		if (err == nil) != (status == 200) || calls != 1 {
			t.Fatalf("HTTP result mismatched or retried: status=%d calls=%d err=%v", status, calls, err)
		}
	}
}
