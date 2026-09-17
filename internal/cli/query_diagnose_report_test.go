package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

func qdrPtr[T any](value T) *T { return &value }

func qdrTestRequest() dataChildRequest {
	return dataChildRequest{Action: "diagnose-report", Request: []byte(`{"token":null}`)}
}

func qdrTestRoot(t *testing.T) string {
	t.Helper()
	root := queryDiagnoseTestRoot(t)
	path := filepath.Join(root, queryDiagnoseReportScript)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func qdrTestReport() queryDiagnosisReport {
	good := queryDiagnosisReportEntry{
		EvidenceToken: strings.Repeat("a", 32), State: "passed", RecordedResult: qdrPtr("passed"), Verification: "saved-evidence",
		ReadToken: qdrPtr(strings.Repeat("c", 32)), QueryID: qdrPtr("20260918_010203_01234_abcde"), SourceProfile: qdrPtr("reader-v1"),
		StartedAt: qdrPtr("2026-09-18T01:02:03.000001Z"), FinishedAt: qdrPtr("2026-09-18T01:02:04.000001Z"), CleanupVerified: qdrPtr(true), History: qdrPtr("observed"),
		Findings: []string{"TABLE_STATISTICS_INCOMPLETE"}, NextSteps: []string{"MEASURE_BEFORE_OPTIMIZING", "REVIEW_TABLE_STATISTICS"},
		Model:    queryDiagnosisReportModel{CallIntents: qdrPtr(int64(1)), RecordedInference: qdrPtr("success"), Provider: qdrPtr("mtplx"), Name: qdrPtr("qwen3.8-flash-next"), PromptTokens: qdrPtr(int64(525)), CompletionTokens: qdrPtr(int64(95)), DurationMS: qdrPtr(int64(4208))},
		Audit:    queryDiagnosisReportAudit{Events: qdrPtr(int64(3)), DeliveredEvents: qdrPtr(int64(3)), SavedSpans: qdrPtr(int64(3)), ProducerVerified: qdrPtr(true), Correlation: qdrPtr("matched")},
		Evidence: qdrPtr("tmp/local-experiment/query-diagnose/" + strings.Repeat("a", 32) + "/summary.json"),
	}
	failed := queryDiagnosisReportEntry{
		EvidenceToken: strings.Repeat("b", 32), State: "failed", RecordedResult: qdrPtr("failed"), Verification: "saved-evidence",
		ReadToken: good.ReadToken, QueryID: good.QueryID, SourceProfile: good.SourceProfile, StartedAt: good.StartedAt, FinishedAt: good.FinishedAt,
		CleanupVerified: qdrPtr(true), ErrorCode: qdrPtr("OBSERVATION_FAILED"),
		Model:    queryDiagnosisReportModel{CallIntents: qdrPtr(int64(0)), RecordedInference: qdrPtr("not-recorded")},
		Audit:    queryDiagnosisReportAudit{Events: qdrPtr(int64(2)), DeliveredEvents: qdrPtr(int64(2)), SavedSpans: qdrPtr(int64(2)), ProducerVerified: qdrPtr(true), Correlation: qdrPtr("matched")},
		Evidence: qdrPtr("tmp/local-experiment/query-diagnose/" + strings.Repeat("b", 32) + "/summary.json"),
	}
	r := queryDiagnosisReport{SchemaVersion: 1, Command: "query diagnose report", CommandResult: "complete", Runs: []queryDiagnosisReportEntry{good, failed}, Limitations: append([]string(nil), queryDiagnosisReportLimitations...)}
	r.Selection.Mode = "all"
	r.Inventory.Root, r.Inventory.Complete, r.Inventory.InspectedEntries = "present", true, 2
	r.Inventory.Counts.Passed, r.Inventory.Counts.Failed = 1, 1
	r.Groups = queryDiagnosisReportGroups(r.Runs)
	return r
}

func qdrTestLine(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func executeQDR(t *testing.T, child dataChildRunner, args ...string) (string, error) {
	t.Helper()
	root, profile := "", localExperimentProfile
	parent := &cobra.Command{Use: "query", SilenceErrors: true, SilenceUsage: true}
	parent.PersistentFlags().StringVar(&root, "repo-root", "", "")
	parent.PersistentFlags().StringVar(&profile, "profile", profile, "")
	owner := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("report launched diagnosis owner")
		return dataChildResult{}, nil
	})
	parent.AddCommand(newQueryDiagnoseCommandWithReport(owner, child, &root, &profile))
	var out bytes.Buffer
	parent.SetOut(&out)
	parent.SetErr(io.Discard)
	parent.SetArgs(args)
	err := parent.Execute()
	return out.String(), err
}

func TestQueryDiagnoseReportRetainsFailedOutcomesWithoutAllocating(t *testing.T) {
	root := qdrTestRoot(t)
	calls := 0
	child := dataChildFunc(func(_ context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		calls++
		input, err := decodeQueryDiagnoseReportRequest(request)
		if err != nil || input.Token != nil || request.Token != "" || request.Root != root {
			t.Fatal("report request mismatch")
		}
		err = emit(qdrTestLine(t, qdrTestReport()))
		return dataChildResult{Started: true, ExitCode: 0}, err
	})
	out, err := executeQDR(t, child, "diagnose", "report", "--repo-root", root)
	if err != nil || calls != 1 || !strings.Contains(out, `"command_result":"complete"`) || strings.Contains(out, "allocated") || !strings.Contains(out, `"state":"failed"`) {
		t.Fatalf("%v %s", err, out)
	}
}

func TestQueryDiagnoseReportBadArgumentsDoNotInspect(t *testing.T) {
	root := qdrTestRoot(t)
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("invalid input reached report")
		return dataChildResult{}, nil
	})
	for _, suffix := range [][]string{{"--token", ""}, {"--token", "PRIVATE_PATH"}, {"--token", strings.Repeat("A", 32)}, {"extra-private"}, {"--profile", "production"}, {"--sql", "PRIVATE_SQL"}, {"--read-token", strings.Repeat("b", 32)}, {"--request-stdin"}} {
		args := append([]string{"diagnose", "report", "--repo-root", root}, suffix...)
		out, err := executeQDR(t, child, args...)
		if err == nil || out != "" || strings.Contains(err.Error(), "PRIVATE_") || strings.Contains(err.Error(), "extra-private") {
			t.Fatalf("unsafe rejection: %v %s", err, out)
		}
	}
	out, err := executeQDR(t, child, "diagnose", "--read-token", strings.Repeat("b", 32), "report", "--repo-root", root)
	if err == nil || out != "" {
		t.Fatal("parent read token was ignored")
	}
}

func TestQueryDiagnoseReportSingleSelectionAndEmptyInventory(t *testing.T) {
	request := qdrTestRequest()
	r := qdrTestReport()
	token := r.Runs[0].EvidenceToken
	request.Request = qdrTestLine(t, queryDiagnoseReportRequest{Token: &token})
	r.Selection.Mode, r.Selection.Token = "single", &token
	r.Runs = r.Runs[:1]
	r.Groups = queryDiagnosisReportGroups(r.Runs)
	r.Inventory.Counts.Failed = 0
	r.Inventory.InspectedEntries = 1
	if code, err := decodeQueryDiagnoseReport(qdrTestLine(t, r), request); err != nil || code != 0 {
		t.Fatal(err)
	}
	request = qdrTestRequest()
	r = qdrTestReport()
	r.Runs = []queryDiagnosisReportEntry{}
	r.Groups = []queryDiagnosisReportGroup{}
	r.Inventory.Root = "absent"
	r.Inventory.InspectedEntries = 0
	r.Inventory.Counts = queryDiagnosisReportCounts{}
	if code, err := decodeQueryDiagnoseReport(qdrTestLine(t, r), request); err != nil || code != 0 {
		t.Fatal(err)
	}
	for _, state := range []string{"active", "incomplete", "invalid", "missing"} {
		x := r
		x.Inventory.Root = "present"
		x.Inventory.InspectedEntries = 1
		x.Runs = []queryDiagnosisReportEntry{{EvidenceToken: token, State: state, Verification: "not-checked"}}
		want := 1
		x.CommandResult = "incomplete"
		switch state {
		case "active":
			want = 0
			x.CommandResult = "complete"
			x.Inventory.Counts.Active = 1
		case "incomplete":
			x.Inventory.Counts.Incomplete = 1
			x.Runs[0].ErrorCode = qdrPtr("REPORT_INCOMPLETE")
		case "invalid":
			x.Inventory.Counts.Invalid = 1
			x.Runs[0].ErrorCode = qdrPtr("REPORT_RECEIPT_INVALID")
		case "missing":
			x.Inventory.Counts.Missing = 1
			x.Runs[0].ErrorCode = qdrPtr("REPORT_NOT_FOUND")
		}
		if code, err := decodeQueryDiagnoseReport(qdrTestLine(t, x), request); err != nil || code != want {
			t.Fatalf("%s: %d %v", state, code, err)
		}
	}
}

func TestQueryDiagnoseReportRejectsContradictoryProjection(t *testing.T) {
	mutations := map[string]func(*queryDiagnosisReport){
		"selection":             func(r *queryDiagnosisReport) { r.Selection.Token = qdrPtr(strings.Repeat("c", 32)) },
		"counts":                func(r *queryDiagnosisReport) { r.Inventory.Counts.Passed++ },
		"intent":                func(r *queryDiagnosisReport) { r.Runs[0].Model.CallIntents = qdrPtr(int64(0)) },
		"unknown-model":         func(r *queryDiagnosisReport) { r.Runs[0].Model.Name = qdrPtr("PRIVATE_MODEL") },
		"free-findings":         func(r *queryDiagnosisReport) { r.Runs[0].Findings = []string{"PRIVATE_TEXT"} },
		"wrong-path":            func(r *queryDiagnosisReport) { r.Runs[0].Evidence = qdrPtr("/PRIVATE_PATH") },
		"missing-usage-group":   func(r *queryDiagnosisReport) { r.Groups[1].KnownPromptTokens++ },
		"missing-failure-group": func(r *queryDiagnosisReport) { r.Groups = r.Groups[1:] },
		"nullable-model":        func(r *queryDiagnosisReport) { r.Runs[1].Model.PromptTokens = qdrPtr(int64(0)) },
		"reverse-order":         func(r *queryDiagnosisReport) { r.Runs[0], r.Runs[1] = r.Runs[1], r.Runs[0] },
		"duplicate-token":       func(r *queryDiagnosisReport) { r.Runs[1].EvidenceToken = r.Runs[0].EvidenceToken },
		"large-metric":          func(r *queryDiagnosisReport) { r.Runs[0].Model.PromptTokens = qdrPtr(int64(1000000000001)) },
		"timestamp":             func(r *queryDiagnosisReport) { r.Runs[0].StartedAt = qdrPtr("PRIVATE_DATE") },
		"negative-cost":         func(r *queryDiagnosisReport) { r.Runs[0].Model.InferenceCostEstimate = qdrPtr(-1.0) },
		"incomplete-claim":      func(r *queryDiagnosisReport) { r.Inventory.ErrorCode = qdrPtr("REPORT_ENTRY_LIMIT") },
		"unknown-source":        func(r *queryDiagnosisReport) { r.Runs[0].SourceProfile = qdrPtr("PRIVATE_PROFILE") },
		"audit-count":           func(r *queryDiagnosisReport) { r.Runs[0].Audit.DeliveredEvents = qdrPtr(int64(4)) },
		"missing-required":      func(r *queryDiagnosisReport) { r.Runs = nil },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			r := qdrTestReport()
			mutate(&r)
			if _, err := decodeQueryDiagnoseReport(qdrTestLine(t, r), qdrTestRequest()); err == nil || strings.Contains(err.Error(), "PRIVATE_") {
				t.Fatal("contradictory or unsafe output accepted")
			}
		})
	}
	var raw map[string]any
	json.Unmarshal(qdrTestLine(t, qdrTestReport()), &raw)
	delete(raw, "groups")
	if _, err := decodeQueryDiagnoseReport(qdrTestLine(t, raw), qdrTestRequest()); err == nil {
		t.Fatal("missing key accepted")
	}
	raw["groups"] = []any{}
	raw["private_extra"] = "PRIVATE_SECRET"
	if _, err := decodeQueryDiagnoseReport(qdrTestLine(t, raw), qdrTestRequest()); err == nil {
		t.Fatal("unknown key accepted")
	}
	line := qdrTestLine(t, qdrTestReport())
	line = append([]byte(`{"schema_version":1,`), line[1:]...)
	if _, err := decodeQueryDiagnoseReport(line, qdrTestRequest()); err == nil {
		t.Fatal("duplicate key accepted")
	}
}

func TestQueryDiagnoseReportConfirmsExitBeforeOutput(t *testing.T) {
	for _, mode := range []string{"success", "failure", "unconfirmed", "error", "multiple", "interrupted", "missing"} {
		t.Run(mode, func(t *testing.T) {
			var out bytes.Buffer
			child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				result := dataChildResult{Started: true, ExitCode: 0}
				var err error
				if mode != "missing" {
					err = emit(qdrTestLine(t, qdrTestReport()))
				}
				if out.Len() != 0 {
					t.Fatal("output was released before child exit")
				}
				switch mode {
				case "failure":
					result.ExitCode = 1
				case "unconfirmed":
					result.Unconfirmed = true
				case "error":
					err = errors.New("PRIVATE_ERROR")
				case "multiple":
					err = emit(qdrTestLine(t, qdrTestReport()))
				case "interrupted":
					result.Interrupted = true
					result.ExitCode = 130
					err = errDataInterrupted
				}
				return result, err
			})
			err := runQueryDiagnoseReportCommand(context.Background(), child, qdrTestRequest(), &out)
			if mode == "success" {
				if err != nil || out.Len() == 0 {
					t.Fatal(err)
				}
			} else if err == nil || out.Len() != 0 || strings.Contains(err.Error(), "PRIVATE_") {
				t.Fatalf("unsafe output %v %s", err, out.String())
			}
		})
	}
}

func TestQueryDiagnoseReportFixedChildAndRequest(t *testing.T) {
	request := qdrTestRequest()
	request.Root = qdrTestRoot(t)
	t.Setenv("PYTHONPATH", "PRIVATE_PATH")
	t.Setenv("HTTP_PROXY", "PRIVATE_PROXY")
	t.Setenv("TOKEN", "PRIVATE_TOKEN")
	command, err := queryDiagnoseReportCommand(request)
	if err != nil {
		t.Fatal(err)
	}
	if command.Dir != request.Root || !strings.HasSuffix(command.Path, dataPython) || command.SysProcAttr == nil || !command.SysProcAttr.Setpgid {
		t.Fatal("fixed child mismatch")
	}
	if strings.Contains(strings.Join(command.Args, " "), "evidence-token") || !strings.Contains(strings.Join(command.Args, " "), "report --request-stdin") {
		t.Fatal("report allocated identity or changed argv")
	}
	if strings.Contains(strings.Join(command.Env, " "), "PRIVATE_") {
		t.Fatal("unfiltered environment")
	}
	body, err := io.ReadAll(command.Stdin)
	if err != nil || string(body) != `{"token":null}` {
		t.Fatal("request stdin mismatch")
	}
	for _, body := range []string{`{}`, `{"token":0}`, `{"token":""}`, `{"token":null,"token":null}`, `{"token":null,"path":"PRIVATE"}`, `{"token":null} {}`} {
		bad := request
		bad.Request = []byte(body)
		if _, err := decodeQueryDiagnoseReportRequest(bad); err == nil {
			t.Fatal("invalid request accepted")
		}
	}
	for _, mutate := range []func(*dataChildRequest){func(r *dataChildRequest) { r.Token = strings.Repeat("a", 32) }, func(r *dataChildRequest) { r.Case = "retained-fixture" }, func(r *dataChildRequest) { r.Action = "diagnose" }} {
		bad := request
		mutate(&bad)
		if _, err := queryDiagnoseReportCommand(bad); err == nil {
			t.Fatal("mutable invocation accepted")
		}
	}
}
