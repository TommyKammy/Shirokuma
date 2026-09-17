package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"reflect"
	"time"
	"unicode/utf8"
)

// Saved log-shaped counts are not authenticated events or a fresh log read.
type sreLogsReport struct {
	Source       string             `json:"source"`
	Availability string             `json:"availability"`
	Initial      *sreLogs           `json:"initial"`
	Followup     *sreLogs           `json:"followup"`
	Comparison   *sreLogsComparison `json:"comparison"`
}
type sreLogs struct {
	SchemaVersion    int               `json:"schema_version"`
	Scope            string            `json:"scope"`
	State            string            `json:"state"`
	Reason           *string           `json:"reason"`
	Binding          *string           `json:"binding"`
	Identity         *sreUsageIdentity `json:"identity"`
	RequestStartedAt *string           `json:"request_started_at"`
	ObservedAt       *string           `json:"observed_at"`
	SinceTime        *string           `json:"since_time"`
	GetAttempts      int               `json:"get_attempts"`
	Fragment         *sreLogsFragment  `json:"fragment"`
	Limitations      []string          `json:"limitations"`
}
type sreLogsFragment struct {
	ReturnedBytes    int     `json:"returned_bytes"`
	CompleteLines    int     `json:"complete_lines"`
	HeaderLines      int     `json:"header_lines"`
	WarnHeaderLines  int     `json:"warn_header_lines"`
	ErrorHeaderLines int     `json:"error_header_lines"`
	UnparsedLines    int     `json:"unparsed_lines"`
	FirstPrefixAt    *string `json:"first_prefix_at"`
	LastPrefixAt     *string `json:"last_prefix_at"`
}
type sreLogsComparison struct {
	BeforeState            string   `json:"before_state"`
	AfterState             string   `json:"after_state"`
	Identity               string   `json:"identity"`
	RequestedWindowOverlap *bool    `json:"requested_window_overlap"`
	Limitations            []string `json:"limitations"`
}

var sreLogsLimitations = []string{"unframed-text-not-event-counts", "latest-file-window-incomplete", "name-time-correlation-only", "no-negative-health-or-cause-claim"}
var errSRELogs = errors.New("SRE saved log signals failed the fixed protocol")

func decodeSRELogs(raw json.RawMessage, event sreEvent) (*sreLogsReport, error) {
	if len(raw) > dataResponseLimit || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return nil, errSRELogs
	}
	fields, ok := sreUsageObject(raw, []string{"source", "availability", "initial", "followup", "comparison"}, "initial", "followup", "comparison")
	if !ok {
		return nil, errSRELogs
	}
	var report sreLogsReport
	if json.Unmarshal(raw, &report) != nil || report.Source != "saved-evidence" {
		return nil, errSRELogs
	}
	verified := event.Result == "passed" && event.Verification == "saved-evidence"
	switch report.Availability {
	case "legacy-absent", "unverified":
		if (report.Availability == "legacy-absent") != verified || report.Initial != nil || report.Followup != nil || report.Comparison != nil {
			return nil, errSRELogs
		}
	case "available":
		if !verified || report.Initial == nil && report.Followup == nil || report.Comparison == nil {
			return nil, errSRELogs
		}
		for _, phase := range []string{"initial", "followup"} {
			if !bytes.Equal(bytes.TrimSpace(fields[phase]), []byte("null")) && !validateSRELogs(fields[phase]) {
				return nil, errSRELogs
			}
		}
		if _, ok := sreUsageObject(fields["comparison"], []string{"before_state", "after_state", "identity", "requested_window_overlap", "limitations"}, "requested_window_overlap"); !ok {
			return nil, errSRELogs
		}
		reobserved := report.Comparison.AfterState != "not-reobserved"
		if !reobserved && report.Followup != nil {
			return nil, errSRELogs
		}
		if !reflect.DeepEqual(*report.Comparison, compareSRELogs(report.Initial, report.Followup, reobserved)) {
			return nil, errSRELogs
		}
	default:
		return nil, errSRELogs
	}
	return &report, nil
}

func validateSRELogs(raw json.RawMessage) bool {
	fields, ok := sreUsageObject(raw, []string{"schema_version", "scope", "state", "reason", "binding", "identity", "request_started_at", "observed_at", "since_time", "get_attempts", "fragment", "limitations"}, "reason", "binding", "identity", "request_started_at", "observed_at", "since_time", "fragment")
	if !ok {
		return false
	}
	var logs sreLogs
	if json.Unmarshal(raw, &logs) != nil || logs.SchemaVersion != 1 || logs.Scope != "fixed-trino-current-container-console" || !dataOneOf(logs.State, "observed", "unknown") || logs.GetAttempts < 0 || logs.GetAttempts > 2 || !reflect.DeepEqual(logs.Limitations, sreLogsLimitations) {
		return false
	}
	var started, request, observed, since time.Time
	if logs.Identity != nil {
		if _, ok := sreUsageObject(fields["identity"], []string{"namespace", "pod_name", "pod_uid", "container", "container_id_sha256", "restart_count", "started_at"}); !ok {
			return false
		}
		id := logs.Identity
		if id.Namespace != "shirokuma-dev" || id.Container != "trino" || !sreUsageName(id.PodName) || !sreUsageIdentityRE.MatchString(id.PodUID) || !sreUsageHashRE.MatchString(id.ContainerIDSHA256) || id.RestartCount < 0 || id.RestartCount > 1<<31-1 {
			return false
		}
		started, ok = sreUsageTimestamp(id.StartedAt)
		if !ok || id.StartedAt != started.Format("2006-01-02T15:04:05.000000Z") {
			return false
		}
	}
	if (logs.RequestStartedAt == nil) != (logs.ObservedAt == nil) || (logs.RequestStartedAt == nil) != (logs.SinceTime == nil) {
		return false
	}
	if logs.RequestStartedAt != nil {
		request, ok = sreUsageTimestamp(*logs.RequestStartedAt)
		if !ok {
			return false
		}
		observed, ok = sreUsageTimestamp(*logs.ObservedAt)
		if !ok || request.After(observed) {
			return false
		}
		since, ok = sreUsageTimestamp(*logs.SinceTime)
		if !ok || *logs.SinceTime != request.Truncate(time.Second).Add(-300*time.Second).Format("2006-01-02T15:04:05Z") {
			return false
		}
	}
	if logs.State == "unknown" {
		return logs.Reason != nil && sreLogsReason(*logs.Reason) && logs.Binding == nil && logs.Fragment == nil
	}
	if logs.Reason != nil || logs.Binding == nil || *logs.Binding != "name-time-correlated" || logs.GetAttempts != 2 || logs.Identity == nil || logs.RequestStartedAt == nil || logs.Fragment == nil {
		return false
	}
	if !request.After(started.Add(999*time.Nanosecond)) || observed.After(request.Add(20*time.Second)) {
		return false
	}
	if _, ok := sreUsageObject(fields["fragment"], []string{"returned_bytes", "complete_lines", "header_lines", "warn_header_lines", "error_header_lines", "unparsed_lines", "first_prefix_at", "last_prefix_at"}, "first_prefix_at", "last_prefix_at"); !ok {
		return false
	}
	f := logs.Fragment
	if f.ReturnedBytes < 0 || f.ReturnedBytes >= 32768 {
		return false
	}
	for _, count := range []int{f.CompleteLines, f.HeaderLines, f.WarnHeaderLines, f.ErrorHeaderLines, f.UnparsedLines} {
		if count < 0 || count > 4096 {
			return false
		}
	}
	if f.CompleteLines != f.HeaderLines+f.UnparsedLines || f.WarnHeaderLines+f.ErrorHeaderLines > f.HeaderLines {
		return false
	}
	if f.ReturnedBytes == 0 {
		return f.CompleteLines == 0 && f.HeaderLines == 0 && f.WarnHeaderLines == 0 && f.ErrorHeaderLines == 0 && f.UnparsedLines == 0 && f.FirstPrefixAt == nil && f.LastPrefixAt == nil
	}
	if f.HeaderLines < 1 || f.HeaderLines > f.CompleteLines || f.CompleteLines > f.ReturnedBytes || f.FirstPrefixAt == nil || f.LastPrefixAt == nil {
		return false
	}
	first, ok := sreUsageTimestamp(*f.FirstPrefixAt)
	if !ok {
		return false
	}
	last, ok := sreUsageTimestamp(*f.LastPrefixAt)
	if !ok {
		return false
	}
	earliest := started.Add(time.Microsecond)
	if since.After(earliest) {
		earliest = since
	}
	return !first.Before(earliest) && !last.Before(first) && !last.After(observed.Add(5*time.Second))
}
func sreLogsReason(value string) bool {
	return dataOneOf(value, "POD_LIST_INCOMPLETE", "TARGET_NOT_FOUND", "TARGET_AMBIGUOUS", "POD_INVALID", "POD_NOT_RUNNING", "CONTAINER_UNSUPPORTED", "IDENTITY_CHANGED", "LOG_INVALID", "FORMAT_UNSUPPORTED", "TIME_INVALID", "READ_LIMIT", "READ_TIMEOUT", "READ_FAILED")
}

// Only request/collection intervals are compared. Counts and textual prefixes
// cannot establish new events, window coverage, trends, recovery or causality.
func compareSRELogs(before, after *sreLogs, reobserved bool) sreLogsComparison {
	result := sreLogsComparison{BeforeState: "not-observed", AfterState: "not-observed", Identity: "unavailable", Limitations: append(append([]string{}, sreLogsLimitations...), "no-count-delta-rate-or-recovery-claim")}
	if before != nil {
		result.BeforeState = before.State
	}
	if after != nil {
		result.AfterState = after.State
	} else if !reobserved {
		result.AfterState = "not-reobserved"
	}
	if !reobserved || before == nil || after == nil || before.State != "observed" || after.State != "observed" {
		return result
	}
	result.Identity = "different"
	if *before.Identity != *after.Identity {
		return result
	}
	result.Identity = "same"
	leftStart, _ := sreUsageTimestamp(*before.RequestStartedAt)
	rightStart, _ := sreUsageTimestamp(*after.RequestStartedAt)
	leftEnd, _ := sreUsageTimestamp(*before.ObservedAt)
	rightEnd, _ := sreUsageTimestamp(*after.ObservedAt)
	if leftStart.After(rightStart) || leftEnd.After(rightEnd) {
		return result
	}
	leftSince, _ := sreUsageTimestamp(*before.SinceTime)
	rightSince, _ := sreUsageTimestamp(*after.SinceTime)
	start, end := leftSince, leftEnd
	if rightSince.After(start) {
		start = rightSince
	}
	if rightEnd.Before(end) {
		end = rightEnd
	}
	overlap := !start.After(end)
	result.RequestedWindowOverlap = &overlap
	return result
}
