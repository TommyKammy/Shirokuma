package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"reflect"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

type queryDiagnosisReport struct {
	SchemaVersion int    `json:"schema_version"`
	Command       string `json:"command"`
	Selection     struct {
		Mode  string  `json:"mode"`
		Token *string `json:"token"`
	} `json:"selection"`
	CommandResult string `json:"command_result"`
	Inventory     struct {
		Root                string                     `json:"root"`
		Complete            bool                       `json:"complete"`
		InspectedEntries    int                        `json:"inspected_entries"`
		UnrecognizedEntries int                        `json:"unrecognized_entries"`
		ErrorCode           *string                    `json:"error_code"`
		Counts              queryDiagnosisReportCounts `json:"counts"`
	} `json:"inventory"`
	Runs        []queryDiagnosisReportEntry `json:"runs"`
	Groups      []queryDiagnosisReportGroup `json:"groups"`
	Limitations []string                    `json:"limitations"`
}

type queryDiagnosisReportCounts struct {
	Passed      int `json:"passed"`
	Failed      int `json:"failed"`
	Interrupted int `json:"interrupted"`
	Active      int `json:"active"`
	Incomplete  int `json:"incomplete"`
	Invalid     int `json:"invalid"`
	Missing     int `json:"missing"`
}

type queryDiagnosisReportEntry struct {
	EvidenceToken   string                    `json:"evidence_token"`
	State           string                    `json:"state"`
	RecordedResult  *string                   `json:"recorded_result"`
	Verification    string                    `json:"verification"`
	ReadToken       *string                   `json:"read_token"`
	QueryID         *string                   `json:"query_id"`
	SourceProfile   *string                   `json:"source_profile"`
	StartedAt       *string                   `json:"started_at"`
	FinishedAt      *string                   `json:"finished_at"`
	CleanupVerified *bool                     `json:"cleanup_verified"`
	ErrorCode       *string                   `json:"error_code"`
	History         *string                   `json:"history"`
	Findings        []string                  `json:"findings"`
	NextSteps       []string                  `json:"next_steps"`
	Model           queryDiagnosisReportModel `json:"model"`
	Audit           queryDiagnosisReportAudit `json:"audit"`
	Evidence        *string                   `json:"evidence"`
}

type queryDiagnosisReportModel struct {
	CallIntents           *int64   `json:"call_intents"`
	RecordedInference     *string  `json:"recorded_inference"`
	Provider              *string  `json:"provider"`
	Name                  *string  `json:"name"`
	PromptTokens          *int64   `json:"prompt_tokens"`
	CompletionTokens      *int64   `json:"completion_tokens"`
	DurationMS            *int64   `json:"duration_ms"`
	InferenceCostEstimate *float64 `json:"inference_cost_estimate"`
}

type queryDiagnosisReportAudit struct {
	Events           *int64  `json:"events"`
	DeliveredEvents  *int64  `json:"delivered_events"`
	SavedSpans       *int64  `json:"saved_spans"`
	ProducerVerified *bool   `json:"producer_verified"`
	Correlation      *string `json:"correlation"`
}

type queryDiagnosisReportGroup struct {
	SourceProfile          *string  `json:"source_profile"`
	Provider               *string  `json:"provider"`
	Model                  *string  `json:"model"`
	TerminalRuns           int64    `json:"terminal_runs"`
	Passed                 int64    `json:"passed"`
	Failed                 int64    `json:"failed"`
	Interrupted            int64    `json:"interrupted"`
	CallIntents            int64    `json:"call_intents"`
	RecordedInferenceRuns  int64    `json:"recorded_inference_runs"`
	KnownPromptTokens      int64    `json:"known_prompt_tokens"`
	KnownCompletionTokens  int64    `json:"known_completion_tokens"`
	MissingPromptUsage     int64    `json:"missing_prompt_usage"`
	MissingCompletionUsage int64    `json:"missing_completion_usage"`
	InferenceCostEstimate  *float64 `json:"inference_cost_estimate"`
}

var queryDiagnosisReportLimitations = []string{"SAVED_DIAGNOSIS_EVIDENCE_ONLY", "ORIGINAL_READER_NOT_REOPENED", "AUDIT_DELIVERY_CLAIMS_NOT_REFRESHED", "RAW_MODEL_RESPONSE_NOT_RETAINED", "NO_MODEL_QUALITY_OR_CURRENT_TABLE_STATE_CLAIM"}

func qdrString(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
func qdrNumber(value *int64, maximum int64) bool {
	return value == nil || *value >= 0 && *value <= maximum
}
func qdrChoice(value *string, choices ...string) bool {
	return value == nil || dataOneOf(*value, choices...)
}
func qdrTrue(value *bool) bool                { return value != nil && *value }
func qdrIs(value *int64, expected int64) bool { return value != nil && *value == expected }
func qdrCost(value *float64) bool {
	return value == nil || !math.IsNaN(*value) && !math.IsInf(*value, 0) && *value >= 0 && *value <= 1e9
}
func qdrStamp(value *string) bool {
	if value == nil {
		return true
	}
	parsed, err := time.Parse("2006-01-02T15:04:05.000000Z", *value)
	return err == nil && parsed.Year() > 0 && parsed.Format("2006-01-02T15:04:05.000000Z") == *value
}

// Round-trip structural equality requires every field, including explicit nulls.
// Unknown fields, duplicate keys and wrong scalar types are rejected separately.
func decodeQueryDiagnoseReport(line []byte, request dataChildRequest) (int, error) {
	invalid := errors.New("query diagnosis report failed the fixed output contract")
	input, err := decodeQueryDiagnoseReportRequest(request)
	if err != nil || len(line) > queryDiagnoseReportLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
		return 1, invalid
	}
	var report queryDiagnosisReport
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&report) != nil {
		return 1, invalid
	}
	encoded, err := json.Marshal(report)
	var before, after any
	if err != nil || json.Unmarshal(line, &before) != nil || json.Unmarshal(encoded, &after) != nil || !reflect.DeepEqual(before, after) {
		return 1, invalid
	}
	if report.SchemaVersion != 1 || report.Command != "query diagnose report" || !reflect.DeepEqual(report.Selection.Token, input.Token) || !reflect.DeepEqual(report.Limitations, queryDiagnosisReportLimitations) {
		return 1, invalid
	}
	mode := "all"
	if input.Token != nil {
		mode = "single"
	}
	if report.Selection.Mode != mode || report.Runs == nil || len(report.Runs) > 128 || report.Groups == nil {
		return 1, invalid
	}
	inv := report.Inventory
	if !dataOneOf(inv.Root, "present", "absent", "not-observed") || inv.InspectedEntries != len(report.Runs) || inv.UnrecognizedEntries < 0 || inv.UnrecognizedEntries > 1000000 || !qdrChoice(inv.ErrorCode, "REPORT_ROOT_INVALID", "REPORT_ROOT_CHANGED", "REPORT_ENTRY_LIMIT", "REPORT_READ_LIMIT", "REPORT_PUBLIC_LIMIT", "REPORT_UNRECOGNIZED_ENTRIES", "REPORT_REQUEST_INVALID") {
		return 1, invalid
	}
	if mode == "single" && (len(report.Runs) != 1 || report.Runs[0].EvidenceToken != *input.Token) {
		return 1, invalid
	}
	if inv.Root == "absent" && len(report.Runs) > 0 && (mode != "single" || report.Runs[0].State != "missing") {
		return 1, invalid
	}
	counts := queryDiagnosisReportCounts{}
	previous := ""
	for _, row := range report.Runs {
		if row.EvidenceToken <= previous || !validQueryDiagnosisReportEntry(row) {
			return 1, invalid
		}
		previous = row.EvidenceToken
		switch row.State {
		case "passed":
			counts.Passed++
		case "failed":
			counts.Failed++
		case "interrupted":
			counts.Interrupted++
		case "active":
			counts.Active++
		case "incomplete":
			counts.Incomplete++
		case "invalid":
			counts.Invalid++
		case "missing":
			counts.Missing++
		}
	}
	if counts != inv.Counts || !reflect.DeepEqual(queryDiagnosisReportGroups(report.Runs), report.Groups) {
		return 1, invalid
	}
	complete := inv.Complete && inv.ErrorCode == nil && inv.Root != "not-observed" && inv.UnrecognizedEntries == 0 && counts.Incomplete+counts.Invalid+counts.Missing == 0
	if complete && report.CommandResult == "complete" {
		return 0, nil
	}
	if !complete && report.CommandResult == "incomplete" {
		return 1, nil
	}
	return 1, invalid
}

func validQueryDiagnosisReportEntry(row queryDiagnosisReportEntry) bool {
	if !warehouseSessionPattern.MatchString(row.EvidenceToken) || !dataOneOf(row.State, "passed", "failed", "interrupted", "active", "incomplete", "invalid", "missing") || !dataOneOf(row.Verification, "saved-evidence", "journal-only", "not-checked") || !qdrChoice(row.RecordedResult, "running", "passed", "failed", "interrupted") || !qdrChoice(row.SourceProfile, "reader-v1", "snapshot-reader-v2") {
		return false
	}
	if row.ReadToken != nil && !warehouseSessionPattern.MatchString(*row.ReadToken) || row.QueryID != nil && !queryDiagnoseQueryID(*row.QueryID) || !qdrStamp(row.StartedAt) || !qdrStamp(row.FinishedAt) || row.StartedAt != nil && row.FinishedAt != nil && *row.FinishedAt < *row.StartedAt {
		return false
	}
	if row.FinishedAt != nil && row.StartedAt == nil {
		return false
	}
	if !qdrChoice(row.ErrorCode, "ARGUMENTS_INVALID", "REQUEST_INVALID", "SOURCE_NOT_FOUND", "SOURCE_BUSY", "SOURCE_INVALID", "SOURCE_CHANGED", "ENVIRONMENT_NOT_READY", "OBSERVATION_FAILED", "MODEL_FAILED", "MODEL_REJECTED", "AUDIT_FAILED", "DIAGNOSE_FAILED", "INTERRUPTED", "EVIDENCE_ALREADY_EXISTS", "REPORT_RECEIPT_INVALID", "REPORT_INCOMPLETE", "REPORT_NOT_FOUND") || !qdrChoice(row.History, "observed", "not-retained") {
		return false
	}
	if row.Evidence != nil && *row.Evidence != "tmp/local-experiment/query-diagnose/"+row.EvidenceToken+"/summary.json" {
		return false
	}
	m, a := row.Model, row.Audit
	if !qdrNumber(m.CallIntents, 1) || !qdrChoice(m.RecordedInference, "success", "failed", "blocked", "not-recorded") || !qdrNumber(m.PromptTokens, 1e12) || !qdrNumber(m.CompletionTokens, 1e12) || !qdrNumber(m.DurationMS, 1e12) || !qdrCost(m.InferenceCostEstimate) {
		return false
	}
	if !((m.Provider == nil && m.Name == nil) || qdrString(m.Provider) == "mtplx" && qdrString(m.Name) == "qwen3.8-flash-next") {
		return false
	}
	if m.RecordedInference == nil || *m.RecordedInference == "not-recorded" {
		if m.Provider != nil || m.Name != nil || m.PromptTokens != nil || m.CompletionTokens != nil || m.DurationMS != nil || m.InferenceCostEstimate != nil {
			return false
		}
	} else if !qdrIs(m.CallIntents, 1) || m.Provider == nil {
		return false
	}
	if !qdrNumber(a.Events, 3) || !qdrNumber(a.DeliveredEvents, 3) || !qdrNumber(a.SavedSpans, 3) || !qdrChoice(a.Correlation, "matched", "partial", "not-recorded") {
		return false
	}
	if a.Events != nil && (a.DeliveredEvents == nil || *a.DeliveredEvents > *a.Events) {
		return false
	}
	if qdrString(a.Correlation) == "matched" && (a.Events == nil || *a.Events == 0 || a.SavedSpans == nil || *a.SavedSpans != *a.Events) {
		return false
	}
	if row.State == "passed" {
		if qdrString(row.RecordedResult) != "passed" || row.Verification != "saved-evidence" || !qdrTrue(row.CleanupVerified) || row.SourceProfile == nil || row.ReadToken == nil || row.QueryID == nil || row.StartedAt == nil || row.FinishedAt == nil || row.History == nil || row.ErrorCode != nil || qdrString(m.RecordedInference) != "success" || !qdrTrue(a.ProducerVerified) || qdrString(a.Correlation) != "matched" || !qdrIs(a.Events, 3) || !qdrIs(a.DeliveredEvents, 3) {
			return false
		}
		view := queryDiagnoseSummary{SchemaVersion: 1, ReadToken: *row.ReadToken, QueryID: *row.QueryID, History: *row.History, Findings: row.Findings, NextSteps: row.NextSteps, RootCause: "undetermined", PerformanceGain: "unmeasured", ModelCalls: 1}
		raw, _ := json.Marshal(view)
		if !validQueryDiagnoseSummary(raw, *row.ReadToken) {
			return false
		}
	} else if row.Findings != nil || row.NextSteps != nil {
		return false
	}
	if dataOneOf(row.State, "failed", "interrupted") {
		if qdrString(row.RecordedResult) != row.State || row.StartedAt == nil || row.FinishedAt == nil || row.ReadToken == nil || row.Evidence == nil || row.CleanupVerified == nil || row.ErrorCode == nil || !queryDiagnoseErrorCode(*row.ErrorCode) || m.CallIntents == nil || !dataOneOf(row.Verification, "saved-evidence", "journal-only") {
			return false
		}
		if row.State == "interrupted" && *row.ErrorCode != "INTERRUPTED" {
			return false
		}
		if row.Verification == "saved-evidence" && (row.SourceProfile == nil || qdrString(a.Correlation) != "matched") {
			return false
		}
	}
	if row.State == "incomplete" && (qdrString(row.ErrorCode) != "REPORT_INCOMPLETE" || !dataOneOf(row.Verification, "not-checked", "journal-only")) {
		return false
	}
	if dataOneOf(row.State, "passed", "failed", "interrupted") && row.Verification == "saved-evidence" && m.CallIntents == nil {
		return false
	}
	if dataOneOf(row.State, "active", "invalid", "missing") {
		minimal := queryDiagnosisReportEntry{EvidenceToken: row.EvidenceToken, State: row.State, Verification: "not-checked"}
		if row.State != "active" {
			code := "REPORT_RECEIPT_INVALID"
			if row.State == "missing" {
				code = "REPORT_NOT_FOUND"
			}
			minimal.ErrorCode = &code
		}
		if !reflect.DeepEqual(row, minimal) {
			return false
		}
	}
	return true
}

func queryDiagnosisReportGroups(rows []queryDiagnosisReportEntry) []queryDiagnosisReportGroup {
	groups := map[string]*queryDiagnosisReportGroup{}
	costs, costUnknown, costObserved := map[string]float64{}, map[string]bool{}, map[string]bool{}
	for _, row := range rows {
		if !dataOneOf(row.State, "passed", "failed", "interrupted") || row.Verification != "saved-evidence" {
			continue
		}
		m := row.Model
		key := strings.Join([]string{qdrString(row.SourceProfile), qdrString(m.Provider), qdrString(m.Name)}, "\x00")
		g := groups[key]
		if g == nil {
			g = &queryDiagnosisReportGroup{SourceProfile: row.SourceProfile, Provider: m.Provider, Model: m.Name}
			groups[key] = g
		}
		g.TerminalRuns++
		switch row.State {
		case "passed":
			g.Passed++
		case "failed":
			g.Failed++
		case "interrupted":
			g.Interrupted++
		}
		g.CallIntents += *m.CallIntents
		if m.RecordedInference != nil && *m.RecordedInference != "not-recorded" {
			g.RecordedInferenceRuns++
		}
		if m.PromptTokens != nil {
			g.KnownPromptTokens += *m.PromptTokens
		} else if *m.CallIntents == 1 {
			g.MissingPromptUsage++
		}
		if m.CompletionTokens != nil {
			g.KnownCompletionTokens += *m.CompletionTokens
		} else if *m.CallIntents == 1 {
			g.MissingCompletionUsage++
		}
		if *m.CallIntents == 1 {
			costObserved[key] = true
			if m.InferenceCostEstimate == nil {
				costUnknown[key] = true
			} else {
				costs[key] += *m.InferenceCostEstimate
			}
		}
	}
	keys := make([]string, 0, len(groups))
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	result := make([]queryDiagnosisReportGroup, 0, len(keys))
	for _, key := range keys {
		g := groups[key]
		if costObserved[key] && !costUnknown[key] {
			cost := costs[key]
			g.InferenceCostEstimate = &cost
		}
		result = append(result, *g)
	}
	return result
}
