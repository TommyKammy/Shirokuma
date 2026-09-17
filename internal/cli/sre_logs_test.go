package cli

import (
	"bytes"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"
)

func sreLogsObserved() *sreLogs {
	return &sreLogs{SchemaVersion: 1, Scope: "fixed-trino-current-container-console", State: "observed", Binding: qdrPtr("name-time-correlated"), Identity: sreUsageTestObserved().Identity,
		RequestStartedAt: qdrPtr("2026-09-17T19:19:00.000000001Z"), ObservedAt: qdrPtr("2026-09-17T19:19:01.000000002Z"), SinceTime: qdrPtr("2026-09-17T19:14:00Z"), GetAttempts: 2,
		Fragment: &sreLogsFragment{ReturnedBytes: 400, CompleteLines: 3, HeaderLines: 2, WarnHeaderLines: 1, ErrorHeaderLines: 1, UnparsedLines: 1, FirstPrefixAt: qdrPtr("2026-09-17T19:18:59.123456789Z"), LastPrefixAt: qdrPtr("2026-09-17T19:19:00.987654321Z")}, Limitations: append([]string{}, sreLogsLimitations...)}
}
func sreLogsUnknown() *sreLogs {
	return &sreLogs{SchemaVersion: 1, Scope: "fixed-trino-current-container-console", State: "unknown", Reason: qdrPtr("READ_FAILED"), Limitations: append([]string{}, sreLogsLimitations...)}
}
func sreLogsTestReport(a, b *sreLogs, reobserved bool) sreLogsReport {
	c := compareSRELogs(a, b, reobserved)
	return sreLogsReport{Source: "saved-evidence", Availability: "available", Initial: a, Followup: b, Comparison: &c}
}
func sreLogsShift(v *sreLogs, d time.Duration) {
	for _, clock := range []*string{v.RequestStartedAt, v.ObservedAt, v.SinceTime, v.Fragment.FirstPrefixAt, v.Fragment.LastPrefixAt} {
		if clock != nil {
			value, _ := sreUsageTimestamp(*clock)
			format := "2006-01-02T15:04:05.000000000Z"
			if clock == v.SinceTime {
				format = "2006-01-02T15:04:05Z"
			}
			*clock = value.Add(d).Format(format)
		}
	}
}
func TestSRELogsObservedEmptyAndUnknownRemainDistinct(t *testing.T) {
	for _, kind := range []string{"nonempty", "empty", "unknown"} {
		t.Run(kind, func(t *testing.T) {
			v := sreLogsObserved()
			if kind == "empty" {
				v.Fragment = &sreLogsFragment{}
			} else if kind == "unknown" {
				v = sreLogsUnknown()
			}
			raw := sreUsageTestRaw(t, sreLogsTestReport(v, nil, false))
			got, e := decodeSRELogs(raw, sreUsageTestEvent())
			if e != nil || !bytes.Equal(raw, sreUsageTestRaw(t, got)) {
				t.Fatalf("typed roundtrip differs: %v", e)
			}
			if kind == "unknown" && got.Initial.Fragment != nil || kind == "empty" && (got.Initial.Fragment == nil || got.Initial.Fragment.ReturnedBytes != 0) {
				t.Fatal("unknown and empty were collapsed")
			}
			if got.Comparison.AfterState != "not-reobserved" || got.Comparison.RequestedWindowOverlap != nil {
				t.Fatal("missing read became a comparison")
			}
		})
	}
}
func TestSRELogsAvailabilityOnlyExposesVerifiedPassedEvidence(t *testing.T) {
	for _, state := range []string{"passed", "failed", "busy", "unknown", "not_found"} {
		event := sreUsageTestEvent()
		event.Result = state
		for _, availability := range []string{"available", "legacy-absent", "unverified"} {
			t.Run(state+"/"+availability, func(t *testing.T) {
				v := sreLogsReport{Source: "saved-evidence", Availability: availability}
				if availability == "available" {
					v = sreLogsTestReport(sreLogsUnknown(), nil, false)
				}
				_, e := decodeSRELogs(sreUsageTestRaw(t, v), event)
				want := (state == "passed") == (availability != "unverified")
				if (e == nil) != want {
					t.Fatal("availability exposed unverified data or hid valid unknown")
				}
			})
		}
	}
	v := sreLogsReport{Source: "saved-evidence", Availability: "unverified", Initial: sreLogsUnknown()}
	if _, e := decodeSRELogs(sreUsageTestRaw(t, v), sreEvent{Result: "failed", Verification: "saved-evidence"}); e == nil {
		t.Fatal("unverified state exposed payload")
	}
}
func TestSRELogsComparisonUsesOnlyIdentityAndRequestedIntervals(t *testing.T) {
	for _, tc := range []struct {
		name            string
		a, b            *sreLogs
		again           bool
		edit            func(*sreLogs)
		identity, after string
		overlap         *bool
	}{
		{"no followup", sreLogsObserved(), nil, false, nil, "unavailable", "not-reobserved", nil},
		{"followup legacy absent", sreLogsObserved(), nil, true, nil, "unavailable", "not-observed", nil},
		{"initial legacy absent", nil, sreLogsObserved(), true, nil, "unavailable", "observed", nil},
		{"initial unknown", sreLogsUnknown(), sreLogsObserved(), true, nil, "unavailable", "observed", nil},
		{"followup unknown", sreLogsObserved(), sreLogsUnknown(), true, nil, "unavailable", "unknown", nil},
		{"same interval", sreLogsObserved(), sreLogsObserved(), true, nil, "same", "observed", qdrPtr(true)},
		{"different identity", sreLogsObserved(), sreLogsObserved(), true, func(v *sreLogs) { v.Identity.RestartCount++ }, "different", "observed", nil},
		{"overlap boundary", sreLogsObserved(), sreLogsObserved(), true, func(v *sreLogs) { sreLogsShift(v, 301*time.Second) }, "same", "observed", qdrPtr(true)},
		{"disjoint", sreLogsObserved(), sreLogsObserved(), true, func(v *sreLogs) { sreLogsShift(v, 302*time.Second) }, "same", "observed", qdrPtr(false)},
		{"reversed time", sreLogsObserved(), sreLogsObserved(), true, func(v *sreLogs) { sreLogsShift(v, -time.Second) }, "same", "observed", nil},
		{"counts unrelated", sreLogsObserved(), sreLogsObserved(), true, func(v *sreLogs) { v.Fragment = &sreLogsFragment{} }, "same", "observed", qdrPtr(true)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.edit != nil {
				tc.edit(tc.b)
			}
			r := sreLogsTestReport(tc.a, tc.b, tc.again)
			if r.Comparison.Identity != tc.identity || r.Comparison.AfterState != tc.after || !reflect.DeepEqual(r.Comparison.RequestedWindowOverlap, tc.overlap) {
				t.Fatal("comparison meaning differs")
			}
			if _, e := decodeSRELogs(sreUsageTestRaw(t, r), sreUsageTestEvent()); e != nil {
				t.Fatal(e)
			}
		})
	}
}
func TestSRELogsRejectsStructuralAndTypeSmuggling(t *testing.T) {
	original := sreUsageTestRaw(t, sreLogsTestReport(sreLogsObserved(), nil, false))
	mutations := map[string]func(map[string]any){
		"extra raw":     func(v map[string]any) { v["initial"].(map[string]any)["raw"] = "PRIVATE_SQL_TOKEN" },
		"missing field": func(v map[string]any) { delete(v["initial"].(map[string]any), "since_time") },
		"null count": func(v map[string]any) {
			v["initial"].(map[string]any)["fragment"].(map[string]any)["complete_lines"] = nil
		},
		"bool count": func(v map[string]any) {
			v["initial"].(map[string]any)["fragment"].(map[string]any)["complete_lines"] = true
		},
		"float count": func(v map[string]any) {
			v["initial"].(map[string]any)["fragment"].(map[string]any)["complete_lines"] = 1.5
		},
		"numeric clock":    func(v map[string]any) { v["initial"].(map[string]any)["observed_at"] = 2026 },
		"null limitations": func(v map[string]any) { v["initial"].(map[string]any)["limitations"] = nil },
		"null state":       func(v map[string]any) { v["initial"].(map[string]any)["state"] = nil },
		"bool overlap":     func(v map[string]any) { v["comparison"].(map[string]any)["requested_window_overlap"] = 1 },
		"invented compare": func(v map[string]any) { v["comparison"].(map[string]any)["count_delta"] = 0 },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			var v map[string]any
			_ = json.Unmarshal(original, &v)
			mutate(v)
			if _, e := decodeSRELogs(sreUsageTestRaw(t, v), sreUsageTestEvent()); e == nil || strings.Contains(e.Error(), "PRIVATE") {
				t.Fatal("invalid nested data accepted/exposed")
			}
		})
	}
	for _, raw := range [][]byte{bytes.Replace(original, []byte(`"source":"saved-evidence"`), []byte(`"source":"saved-evidence","source":"saved-evidence"`), 1), bytes.Replace(original, []byte(`"get_attempts":2`), []byte(`"get_attempts":2.0`), 1), append(original, []byte("{}")...), {0xff}} {
		if _, e := decodeSRELogs(raw, sreUsageTestEvent()); e == nil {
			t.Fatal("duplicate/numeric/trailing/invalid bytes accepted")
		}
	}
}
func TestSRELogsRejectsInvalidFragmentClockAndIdentity(t *testing.T) {
	tests := map[string]func(*sreLogs){
		"max bytes": func(v *sreLogs) { v.Fragment.ReturnedBytes = 32768 }, "negative bytes": func(v *sreLogs) { v.Fragment.ReturnedBytes = -1 },
		"max lines": func(v *sreLogs) { v.Fragment.CompleteLines = 4097 }, "sum": func(v *sreLogs) { v.Fragment.UnparsedLines = 0 }, "levels": func(v *sreLogs) { v.Fragment.ErrorHeaderLines = 2 },
		"unparsed only": func(v *sreLogs) {
			v.Fragment.HeaderLines = 0
			v.Fragment.WarnHeaderLines = 0
			v.Fragment.ErrorHeaderLines = 0
			v.Fragment.UnparsedLines = 3
		},
		"empty with prefix":       func(v *sreLogs) { v.Fragment = &sreLogsFragment{FirstPrefixAt: qdrPtr("2026-09-17T19:19:00Z")} },
		"nonempty without prefix": func(v *sreLogs) { v.Fragment.LastPrefixAt = nil },
		"prefix reversed":         func(v *sreLogs) { v.Fragment.FirstPrefixAt = qdrPtr("2026-09-17T19:19:01Z") },
		"prefix before since":     func(v *sreLogs) { v.Fragment.FirstPrefixAt = qdrPtr("2026-09-17T19:13:59.999999999Z") },
		"future prefix":           func(v *sreLogs) { v.Fragment.LastPrefixAt = qdrPtr("2026-09-17T19:19:06.000000003Z") },
		"wrong since":             func(v *sreLogs) { v.SinceTime = qdrPtr("2026-09-17T19:14:00.000000000Z") },
		"one clock null":          func(v *sreLogs) { v.RequestStartedAt = nil },
		"reversed clocks":         func(v *sreLogs) { v.ObservedAt = qdrPtr("2026-09-17T19:19:00Z") },
		"collection cap":          func(v *sreLogs) { v.ObservedAt = qdrPtr("2026-09-17T19:19:20.000000002Z") },
		"before epoch":            func(v *sreLogs) { v.RequestStartedAt = qdrPtr("1969-12-31T23:59:59Z") },
		"wrong namespace":         func(v *sreLogs) { v.Identity.Namespace = "other" }, "wrong container": func(v *sreLogs) { v.Identity.Container = "sidecar" },
		"bad name": func(v *sreLogs) { v.Identity.PodName = "trino/secret" }, "bad uid": func(v *sreLogs) { v.Identity.PodUID = strings.ToUpper(v.Identity.PodUID) },
		"bad hash": func(v *sreLogs) { v.Identity.ContainerIDSHA256 = strings.Repeat("g", 64) }, "negative restart": func(v *sreLogs) { v.Identity.RestartCount = -1 }, "restart overflow": func(v *sreLogs) { v.Identity.RestartCount = 1 << 31 },
		"start precision":       func(v *sreLogs) { v.Identity.StartedAt = "2026-09-17T00:51:07Z" },
		"container not started": func(v *sreLogs) { v.Identity.StartedAt = "2026-09-17T19:19:00.000000Z" },
		"wrong limitations":     func(v *sreLogs) { v.Limitations[0] = "events-counted" }, "wrong state": func(v *sreLogs) { v.State = "healthy" },
		"observed reason": func(v *sreLogs) { v.Reason = qdrPtr("READ_FAILED") }, "observed one intent": func(v *sreLogs) { v.GetAttempts = 1 }, "unknown keeps fragment": func(v *sreLogs) { v.State = "unknown"; v.Reason = qdrPtr("READ_FAILED"); v.Binding = nil },
	}
	for name, edit := range tests {
		t.Run(name, func(t *testing.T) {
			v := sreLogsObserved()
			edit(v)
			if validateSRELogs(sreUsageTestRaw(t, v)) {
				t.Fatal("invalid logs accepted")
			}
		})
	}
}
func TestSRELogsUnknownAllowsItsOwnBoundedMetadataWithoutUsageSemantics(t *testing.T) {
	v := sreLogsUnknown()
	v.Identity = sreLogsObserved().Identity
	v.RequestStartedAt = qdrPtr("2026-09-17T19:19:00Z")
	v.ObservedAt = qdrPtr("2026-09-17T19:20:00Z")
	v.SinceTime = qdrPtr("2026-09-17T19:14:00Z")
	v.GetAttempts = 2
	if !validateSRELogs(sreUsageTestRaw(t, v)) {
		t.Fatal("unknown was constrained as an observed metrics sample")
	}
	for _, reason := range []string{"READ_FAILED", "FORMAT_UNSUPPORTED", "TARGET_AMBIGUOUS", "IDENTITY_CHANGED"} {
		v.Reason = &reason
		if !validateSRELogs(sreUsageTestRaw(t, v)) {
			t.Fatal("fixed log reason rejected")
		}
	}
	for _, reason := range []string{"METRICS_INVALID", "SAMPLE_STALE", "PRIVATE_ERROR"} {
		v.Reason = &reason
		if validateSRELogs(sreUsageTestRaw(t, v)) {
			t.Fatal("usage/foreign reason accepted as logs")
		}
	}
}
func TestSRELogsNanosecondAndDistantDatesRemainExact(t *testing.T) {
	v := sreLogsObserved()
	v.Identity.StartedAt = "9999-01-01T00:00:00.000000Z"
	v.RequestStartedAt = qdrPtr("9999-01-01T00:05:00.000001000Z")
	v.ObservedAt = qdrPtr("9999-01-01T00:05:20.000001000Z")
	v.SinceTime = qdrPtr("9999-01-01T00:00:00Z")
	v.Fragment.FirstPrefixAt = qdrPtr("9999-01-01T00:00:00.000001000Z")
	v.Fragment.LastPrefixAt = qdrPtr("9999-01-01T00:05:25.000001000Z")
	if !validateSRELogs(sreUsageTestRaw(t, v)) {
		t.Fatal("valid distant ns boundary rejected")
	}
	v.Fragment.FirstPrefixAt = qdrPtr("9999-01-01T00:00:00.000000999Z")
	if validateSRELogs(sreUsageTestRaw(t, v)) {
		t.Fatal("one ns before container boundary accepted")
	}
	v.Fragment.FirstPrefixAt = qdrPtr("9999-01-01T00:00:00.000001000Z")
	v.Fragment.LastPrefixAt = qdrPtr("9999-01-01T00:05:25.000001001Z")
	if validateSRELogs(sreUsageTestRaw(t, v)) {
		t.Fatal("one ns future excess accepted")
	}
}
