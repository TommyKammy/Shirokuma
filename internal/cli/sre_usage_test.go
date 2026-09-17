package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func sreUsageTestObserved() *sreUsage {
	binding, request, observed := "name-time-correlated", "2026-09-17T19:19:00.000000Z", "2026-09-17T19:19:01.000000000Z"
	return &sreUsage{SchemaVersion: 1, Scope: "fixed-trino-container", State: "observed", Binding: &binding,
		RequestStartedAt: &request, ObservedAt: &observed, GetAttempts: 2,
		Identity: &sreUsageIdentity{Namespace: "shirokuma-dev", PodName: "trino-local-abcde-12345",
			PodUID: "117b813d-42aa-48d1-86db-56430424d624", Container: "trino", ContainerIDSHA256: strings.Repeat("a", 64),
			RestartCount: 1, StartedAt: "2026-09-17T00:51:07.000000Z"},
		Sample: &sreUsageSample{Timestamp: "2026-09-17T19:18:52.000000000Z", WindowNS: "15014000000",
			CPUNanocores: "11121886", MemoryWorkingSetBytes: "2162106368"}}
}

func sreUsageTestUnknown() *sreUsage {
	reason := "READ_FAILED"
	return &sreUsage{SchemaVersion: 1, Scope: "fixed-trino-container", State: "unknown", Reason: &reason}
}

func sreUsageTestReport(before, after *sreUsage, reobserved bool) sreUsageReport {
	comparison := compareSREUsage(before, after, reobserved)
	return sreUsageReport{Source: "saved-evidence", Availability: "available", Initial: before, Followup: after, Comparison: &comparison}
}

func sreUsageTestRaw(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func sreUsageTestEvent() sreEvent { return sreEvent{Result: "passed", Verification: "saved-evidence"} }

func TestSREUsagePreservesObservedZeroAndInt64Precision(t *testing.T) {
	initial := sreUsageTestObserved()
	initial.Sample.CPUNanocores = "0"
	initial.Sample.MemoryWorkingSetBytes = "9223372036854775807"
	want := sreUsageTestReport(initial, nil, false)
	raw := sreUsageTestRaw(t, want)
	got, err := decodeSREUsage(raw, sreUsageTestEvent())
	if err != nil || !reflect.DeepEqual(*got, want) {
		t.Fatalf("valid observed values rejected: %v", err)
	}
	if !bytes.Equal(sreUsageTestRaw(t, got), raw) {
		t.Fatal("decoder changed exact integer strings or explicit nulls")
	}
}

func TestSREUsageAvailabilityRequiresVerifiedSavedSuccess(t *testing.T) {
	for _, tc := range []struct {
		name         string
		event        sreEvent
		availability string
		valid        bool
	}{
		{"legacy", sreUsageTestEvent(), "legacy-absent", true},
		{"passed unverified", sreUsageTestEvent(), "unverified", false},
		{"failed", sreEvent{Result: "failed", Verification: "saved-evidence"}, "unverified", true},
		{"unknown", sreEvent{Result: "unknown", Verification: "journal-only"}, "unverified", true},
		{"missing", sreEvent{Result: "not_found", Verification: "not-checked"}, "unverified", true},
		{"busy", sreEvent{Result: "busy", Verification: "journal-only"}, "unverified", true},
		{"failed legacy", sreEvent{Result: "failed", Verification: "saved-evidence"}, "legacy-absent", false},
		{"unchecked legacy", sreEvent{Result: "passed", Verification: "not-checked"}, "legacy-absent", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			value := sreUsageReport{Source: "saved-evidence", Availability: tc.availability}
			_, err := decodeSREUsage(sreUsageTestRaw(t, value), tc.event)
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v, error=%v", tc.valid, err)
			}
		})
	}
	for _, availability := range []string{"legacy-absent", "unverified"} {
		value := sreUsageTestReport(sreUsageTestUnknown(), nil, false)
		value.Availability = availability
		if _, err := decodeSREUsage(sreUsageTestRaw(t, value), sreUsageTestEvent()); err == nil {
			t.Fatalf("%s exposed a partial payload", availability)
		}
	}
	value := sreUsageTestReport(sreUsageTestObserved(), nil, false)
	if _, err := decodeSREUsage(sreUsageTestRaw(t, value), sreEvent{Result: "failed", Verification: "saved-evidence"}); err == nil {
		t.Fatal("failed result exposed available usage")
	}
}

func TestSREUsageComparisonsMatchSavedSemantics(t *testing.T) {
	for _, tc := range []struct {
		name       string
		before     *sreUsage
		after      *sreUsage
		reobserved bool
		edit       func(*sreUsage)
		comparison string
		extra      string
	}{
		{"not selected", sreUsageTestObserved(), nil, false, nil, "not-reobserved", ""},
		{"reobserved absent", sreUsageTestObserved(), nil, true, nil, "incomparable", "resource-usage-not-observed"},
		{"initial absent", nil, sreUsageTestObserved(), true, nil, "incomparable", "resource-usage-not-observed"},
		{"initial unknown", sreUsageTestUnknown(), sreUsageTestObserved(), true, nil, "incomparable", "resource-usage-unknown"},
		{"followup unknown", sreUsageTestObserved(), sreUsageTestUnknown(), true, nil, "incomparable", "resource-usage-unknown"},
		{"identity changed", sreUsageTestObserved(), sreUsageTestObserved(), true, func(v *sreUsage) { v.Identity.RestartCount++ }, "incomparable", "resource-usage-identity-changed"},
		{"time reversed", sreUsageTestObserved(), sreUsageTestObserved(), true, func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:18:51Z" }, "incomparable", "resource-usage-time-reversed"},
		{"same sample mixed precision", sreUsageTestObserved(), sreUsageTestObserved(), true, func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:18:52.000000Z" }, "same-sample-window", "cached-sample-not-new-measurement"},
		{"inconsistent same sample", sreUsageTestObserved(), sreUsageTestObserved(), true, func(v *sreUsage) { v.Sample.CPUNanocores = "0" }, "incomparable", "resource-usage-sample-inconsistent"},
		{"later sample", sreUsageTestObserved(), sreUsageTestObserved(), true, func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:18:53Z" }, "different-sample-window", ""},
		{"different window same timestamp", sreUsageTestObserved(), sreUsageTestObserved(), true, func(v *sreUsage) { v.Sample.WindowNS = "15000000000" }, "different-sample-window", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.edit != nil {
				tc.edit(tc.after)
			}
			value := sreUsageTestReport(tc.before, tc.after, tc.reobserved)
			wantLimits := []string{"name-time-correlation-only", "no-trend-or-cause-claim"}
			if tc.extra != "" {
				wantLimits = append(wantLimits, tc.extra)
			}
			if value.Comparison.Comparison != tc.comparison || !reflect.DeepEqual(value.Comparison.Limitations, wantLimits) {
				t.Fatal("comparison changed its bounded meaning")
			}
			if _, err := decodeSREUsage(sreUsageTestRaw(t, value), sreUsageTestEvent()); err != nil {
				t.Fatal(err)
			}
			value.Comparison.Comparison = "recovered"
			if _, err := decodeSREUsage(sreUsageTestRaw(t, value), sreUsageTestEvent()); err == nil {
				t.Fatal("unsupported recovery claim accepted")
			}
		})
	}
}

func TestSREUsageRejectsForgedComparisons(t *testing.T) {
	for _, edit := range []func(*sreUsageReport){
		func(v *sreUsageReport) { v.Comparison.AfterState = "not-reobserved" },
		func(v *sreUsageReport) { v.Comparison.BeforeState = "unknown" },
		func(v *sreUsageReport) { v.Comparison.Limitations = v.Comparison.Limitations[:2] },
		func(v *sreUsageReport) {
			v.Comparison.Limitations[0], v.Comparison.Limitations[1] = v.Comparison.Limitations[1], v.Comparison.Limitations[0]
		},
		func(v *sreUsageReport) { v.Comparison.Limitations = append(v.Comparison.Limitations, "raw error") },
		func(v *sreUsageReport) { v.Initial, v.Followup = nil, nil },
		func(v *sreUsageReport) { v.Comparison = nil },
	} {
		value := sreUsageTestReport(sreUsageTestObserved(), sreUsageTestObserved(), true)
		edit(&value)
		if _, err := decodeSREUsage(sreUsageTestRaw(t, value), sreUsageTestEvent()); err == nil {
			t.Fatal("forged comparison accepted")
		}
	}
}

func TestSREUsageRejectsNoncanonicalShapesAndTypes(t *testing.T) {
	for _, tc := range []struct {
		name  string
		path  []string
		value any
	}{
		{"extra root", []string{"private"}, "SECRET"},
		{"wrong source", []string{"source"}, "live"},
		{"scope", []string{"initial", "scope"}, "all-pods"},
		{"schema float", []string{"initial", "schema_version"}, json.RawMessage("1.0")},
		{"schema bool", []string{"initial", "schema_version"}, true},
		{"attempts null", []string{"initial", "get_attempts"}, nil},
		{"attempts float", []string{"initial", "get_attempts"}, json.RawMessage("2.0")},
		{"attempts too many", []string{"initial", "get_attempts"}, 3},
		{"binding", []string{"initial", "binding"}, "uid-authenticated"},
		{"null identity", []string{"initial", "identity"}, nil},
		{"namespace", []string{"initial", "identity", "namespace"}, "another"},
		{"container", []string{"initial", "identity", "container"}, "sidecar"},
		{"UUID case", []string{"initial", "identity", "pod_uid"}, "117B813D-42AA-48D1-86DB-56430424D624"},
		{"UUID compact", []string{"initial", "identity", "pod_uid"}, strings.Repeat("a", 32)},
		{"bad hash", []string{"initial", "identity", "container_id_sha256"}, strings.Repeat("g", 64)},
		{"restart null", []string{"initial", "identity", "restart_count"}, nil},
		{"restart bool", []string{"initial", "identity", "restart_count"}, false},
		{"restart overflow", []string{"initial", "identity", "restart_count"}, 1 << 31},
		{"name unsafe", []string{"initial", "identity", "pod_name"}, "bad..name"},
		{"label too long", []string{"initial", "identity", "pod_name"}, strings.Repeat("a", 64)},
		{"started precision", []string{"initial", "identity", "started_at"}, "2026-09-17T00:51:07Z"},
		{"CPU integer", []string{"initial", "sample", "cpu_nanocores"}, 0},
		{"CPU decimal", []string{"initial", "sample", "cpu_nanocores"}, "1.0"},
		{"CPU exponent", []string{"initial", "sample", "cpu_nanocores"}, "1e3"},
		{"CPU negative", []string{"initial", "sample", "cpu_nanocores"}, "-1"},
		{"CPU leading zero", []string{"initial", "sample", "cpu_nanocores"}, "01"},
		{"memory int64 overflow", []string{"initial", "sample", "memory_working_set_bytes"}, "9223372036854775808"},
		{"zero window", []string{"initial", "sample", "window_ns"}, "0"},
		{"window int64 overflow", []string{"initial", "sample", "window_ns"}, "9223372036854775808"},
		{"sample extra key", []string{"initial", "sample", "raw_quantity"}, "SECRET"},
		{"comparison null limits", []string{"comparison", "limitations"}, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var value map[string]any
			if err := json.Unmarshal(sreUsageTestRaw(t, sreUsageTestReport(sreUsageTestObserved(), nil, false)), &value); err != nil {
				t.Fatal(err)
			}
			node := value
			for _, part := range tc.path[:len(tc.path)-1] {
				node = node[part].(map[string]any)
			}
			node[tc.path[len(tc.path)-1]] = tc.value
			if _, err := decodeSREUsage(sreUsageTestRaw(t, value), sreUsageTestEvent()); err == nil {
				t.Fatal("invalid nested field accepted")
			} else if err.Error() != errSREUsage.Error() || strings.Contains(err.Error(), "SECRET") {
				t.Fatal("raw response leaked through error")
			}
		})
	}
	valid := sreUsageTestRaw(t, sreUsageTestReport(sreUsageTestObserved(), nil, false))
	for _, raw := range [][]byte{
		bytes.Replace(valid, []byte(`"get_attempts":2`), []byte(`"get_attempts":2,"get_attempts":2`), 1),
		bytes.Replace(valid, []byte(`"source":"saved-evidence"`), []byte(`"source":"saved-evidence","\u0073ource":"saved-evidence"`), 1),
		bytes.Replace(valid, []byte(`"followup":null,`), nil, 1),
		append(append([]byte{}, valid...), []byte(` {}`)...),
		[]byte("null"), []byte(strings.Repeat(" ", dataResponseLimit+1)),
		append(append([]byte{}, valid...), 0xff),
	} {
		if _, err := decodeSREUsage(raw, sreUsageTestEvent()); err == nil {
			t.Fatal("malformed, duplicate, missing, or oversized JSON accepted")
		}
	}
}

func TestSREUsageExactTimeBoundaries(t *testing.T) {
	for _, tc := range []struct {
		name  string
		edit  func(*sreUsage)
		valid bool
	}{
		{"request 20s", func(v *sreUsage) { *v.RequestStartedAt = "2026-09-17T19:18:41Z" }, true},
		{"request >20s", func(v *sreUsage) { *v.RequestStartedAt = "2026-09-17T19:18:40.999999999Z" }, false},
		{"request reversed", func(v *sreUsage) { *v.RequestStartedAt = "2026-09-17T19:19:01.000000001Z" }, false},
		{"sample 120s old", func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:17:01Z" }, true},
		{"sample >120s old", func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:17:00.999999999Z" }, false},
		{"sample 5s future", func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:19:06Z" }, true},
		{"sample >5s future", func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:19:06.000000001Z" }, false},
		{"window 120s", func(v *sreUsage) { v.Sample.WindowNS = "120000000000" }, true},
		{"window >120s", func(v *sreUsage) { v.Sample.WindowNS = "120000000001" }, false},
		{"before epoch", func(v *sreUsage) { v.Identity.StartedAt = "1969-12-31T23:59:59.999999Z" }, false},
		{"non UTC", func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:18:52+00:00" }, false},
		{"invalid calendar", func(v *sreUsage) { v.Sample.Timestamp = "2026-02-30T19:18:52Z" }, false},
		{"precision overflow", func(v *sreUsage) { v.Sample.Timestamp = "2026-09-17T19:18:52.0000000000Z" }, false},
		{"window starts at +999ns", func(v *sreUsage) {
			v.Sample.WindowNS = "1000000000"
			v.Identity.StartedAt = "2026-09-17T19:18:51.000000Z"
			v.Sample.Timestamp = "2026-09-17T19:18:52.000000999Z"
		}, false},
		{"window starts at +1000ns", func(v *sreUsage) {
			v.Sample.WindowNS = "1000000000"
			v.Identity.StartedAt = "2026-09-17T19:18:51.000000Z"
			v.Sample.Timestamp = "2026-09-17T19:18:52.000001000Z"
		}, true},
		{"year beyond UnixNano", func(v *sreUsage) {
			v.Identity.StartedAt = "9999-09-17T00:51:07.000000Z"
			*v.RequestStartedAt = "9999-09-17T19:19:00Z"
			*v.ObservedAt = "9999-09-17T19:19:01Z"
			v.Sample.Timestamp = "9999-09-17T19:18:52Z"
		}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			usage := sreUsageTestObserved()
			tc.edit(usage)
			_, err := decodeSREUsage(sreUsageTestRaw(t, sreUsageTestReport(usage, nil, false)), sreUsageTestEvent())
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v, error=%v", tc.valid, err)
			}
		})
	}
}

func TestSREUsageUnknownRetainsMissingCoverage(t *testing.T) {
	for _, reason := range []string{"POD_LIST_INCOMPLETE", "TARGET_NOT_FOUND", "TARGET_AMBIGUOUS", "POD_INVALID", "POD_NOT_RUNNING", "CONTAINER_UNSUPPORTED", "IDENTITY_CHANGED", "METRICS_INVALID", "QUANTITY_UNSUPPORTED", "TIME_INVALID", "SAMPLE_STALE", "SAMPLE_PRECEDES_CONTAINER", "READ_LIMIT", "READ_TIMEOUT", "READ_FAILED"} {
		value := sreUsageTestUnknown()
		value.Reason = &reason
		if _, err := decodeSREUsage(sreUsageTestRaw(t, sreUsageTestReport(value, nil, false)), sreUsageTestEvent()); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct {
		name  string
		edit  func(*sreUsage)
		valid bool
	}{
		{"before identity retained", func(v *sreUsage) { v.Identity = sreUsageTestObserved().Identity }, true},
		{"long failed read", func(v *sreUsage) {
			a, b := "1970-01-01T00:00:00Z", "9999-01-01T00:00:00Z"
			v.RequestStartedAt, v.ObservedAt = &a, &b
		}, true},
		{"single clock", func(v *sreUsage) { a := "2026-09-17T19:19:00Z"; v.RequestStartedAt = &a }, false},
		{"null reason", func(v *sreUsage) { v.Reason = nil }, false},
		{"raw reason", func(v *sreUsage) { s := "SECRET"; v.Reason = &s }, false},
		{"unknown binding", func(v *sreUsage) { s := "name-time-correlated"; v.Binding = &s }, false},
		{"unknown sample", func(v *sreUsage) { v.Sample = sreUsageTestObserved().Sample }, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			v := sreUsageTestUnknown()
			tc.edit(v)
			_, err := decodeSREUsage(sreUsageTestRaw(t, sreUsageTestReport(v, nil, false)), sreUsageTestEvent())
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v, error=%v", tc.valid, err)
			}
		})
	}
}

func TestSREUsageActualPythonPureProjection(t *testing.T) {
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, "tools", "local_experiment", "llm", ".venv", "bin", "python")
	if _, err := os.Stat(python); err != nil {
		t.Skip("dedicated Python environment is unavailable")
	}
	// Actual committed validator/comparator only: no workflow, API or model.
	script := `import copy,json,sys
sys.path.insert(0,sys.argv[1])
from tests.test_local_sre_contract import usage_observed,usage_unknown
from tools.local_experiment.gateway import pod_usage as U
from tools.local_experiment.bears import sre_contract as C
cases=[]
def add(a,b,again):
    for value in (a,b):
        if value is not None: U.validate(value)
    cases.append({'source':'saved-evidence','availability':'available','initial':a,'followup':b,'comparison':C._compare_resource_usage(a,b,reobserved=again)})
a=usage_observed()
add(a,None,False);add(a,None,True);add(None,a,True);add(usage_unknown(),a,True)
add(a,copy.deepcopy(a),True)
b=copy.deepcopy(a);b['sample']['cpu_nanocores']='0';add(a,b,True)
b=copy.deepcopy(a);b['sample']['window_ns']=str(int(b['sample']['window_ns'])+1);add(a,b,True)
print(json.dumps(cases,separators=(',',':')))
`
	command := exec.Command(python, "-I", "-B", "-c", script, root)
	var stderr bytes.Buffer
	command.Stderr = &stderr
	output, err := command.Output()
	if err != nil {
		t.Fatalf("pure Python fixture failed: %v", err)
	}
	var cases []json.RawMessage
	if json.Unmarshal(output, &cases) != nil || len(cases) != 7 {
		t.Fatal("invalid pure fixture output")
	}
	for i, raw := range cases {
		value, err := decodeSREUsage(raw, sreUsageTestEvent())
		if err != nil {
			t.Fatalf("Python case %d: %v", i, err)
		}
		var before, after any
		if json.Unmarshal(raw, &before) != nil || json.Unmarshal(sreUsageTestRaw(t, value), &after) != nil || !reflect.DeepEqual(before, after) {
			t.Fatalf("Python case %d changed", i)
		}
	}
}
