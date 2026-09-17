package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// sreUsageReport is a projection of saved evidence, never a fresh metrics read.
// Nullable fields are emitted explicitly so legacy absence cannot look observed.
type sreUsageReport struct {
	Source       string              `json:"source"`
	Availability string              `json:"availability"`
	Initial      *sreUsage           `json:"initial"`
	Followup     *sreUsage           `json:"followup"`
	Comparison   *sreUsageComparison `json:"comparison"`
}

type sreUsage struct {
	SchemaVersion    int               `json:"schema_version"`
	Scope            string            `json:"scope"`
	State            string            `json:"state"`
	Reason           *string           `json:"reason"`
	Binding          *string           `json:"binding"`
	RequestStartedAt *string           `json:"request_started_at"`
	ObservedAt       *string           `json:"observed_at"`
	Identity         *sreUsageIdentity `json:"identity"`
	Sample           *sreUsageSample   `json:"sample"`
	GetAttempts      int               `json:"get_attempts"`
}

type sreUsageIdentity struct {
	Namespace         string `json:"namespace"`
	PodName           string `json:"pod_name"`
	PodUID            string `json:"pod_uid"`
	Container         string `json:"container"`
	ContainerIDSHA256 string `json:"container_id_sha256"`
	RestartCount      int64  `json:"restart_count"`
	StartedAt         string `json:"started_at"`
}

type sreUsageSample struct {
	Timestamp             string `json:"timestamp"`
	WindowNS              string `json:"window_ns"`
	CPUNanocores          string `json:"cpu_nanocores"`
	MemoryWorkingSetBytes string `json:"memory_working_set_bytes"`
}

type sreUsageComparison struct {
	BeforeState string   `json:"before_state"`
	AfterState  string   `json:"after_state"`
	Comparison  string   `json:"comparison"`
	Limitations []string `json:"limitations"`
}

var (
	errSREUsage        = errors.New("SRE saved usage failed the fixed protocol")
	sreUsageStampRE    = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z$`)
	sreUsageIdentityRE = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	sreUsageHashRE     = regexp.MustCompile(`^[0-9a-f]{64}$`)
	sreUsageQuantityRE = regexp.MustCompile(`^(0|[1-9][0-9]{0,18})$`)
	sreUsageNameRE     = regexp.MustCompile(`^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$`)
)

// decodeSREUsage validates the nested schema-2 status field. The caller validates
// the enclosing status protocol. Only passed/saved-evidence can expose usage;
// this function uses saved clocks and never reads the current time or runtime.
func decodeSREUsage(raw json.RawMessage, event sreEvent) (*sreUsageReport, error) {
	if len(raw) > dataResponseLimit || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return nil, errSREUsage
	}
	fields, ok := sreUsageObject(raw,
		[]string{"source", "availability", "initial", "followup", "comparison"},
		"initial", "followup", "comparison")
	if !ok {
		return nil, errSREUsage
	}
	var report sreUsageReport
	if json.Unmarshal(raw, &report) != nil || report.Source != "saved-evidence" {
		return nil, errSREUsage
	}
	verified := event.Result == "passed" && event.Verification == "saved-evidence"
	switch report.Availability {
	case "legacy-absent", "unverified":
		if (report.Availability == "legacy-absent") != verified || report.Initial != nil || report.Followup != nil || report.Comparison != nil {
			return nil, errSREUsage
		}
	case "available":
		if !verified || (report.Initial == nil && report.Followup == nil) || report.Comparison == nil {
			return nil, errSREUsage
		}
		for _, phase := range []string{"initial", "followup"} {
			if !bytes.Equal(bytes.TrimSpace(fields[phase]), []byte("null")) && !validateSREUsage(fields[phase]) {
				return nil, errSREUsage
			}
		}
		if _, ok := sreUsageObject(fields["comparison"], []string{"before_state", "after_state", "comparison", "limitations"}); !ok {
			return nil, errSREUsage
		}
		// An absent follow-up usage can mean no follow-up read, or a read whose
		// older snapshot had no usage field. Only the saved after_state tells us.
		reobserved := report.Comparison.AfterState != "not-reobserved"
		if !reobserved && report.Followup != nil {
			return nil, errSREUsage
		}
		expected := compareSREUsage(report.Initial, report.Followup, reobserved)
		if !reflect.DeepEqual(*report.Comparison, expected) {
			return nil, errSREUsage
		}
	default:
		return nil, errSREUsage
	}
	return &report, nil
}

func sreUsageObject(raw json.RawMessage, keys []string, nullable ...string) (map[string]json.RawMessage, bool) {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || !warehouseKeys(fields, keys...) {
		return nil, false
	}
	for key, value := range fields {
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) && !dataOneOf(key, nullable...) {
			return nil, false
		}
	}
	return fields, true
}

func validateSREUsage(raw json.RawMessage) bool {
	fields, ok := sreUsageObject(raw,
		[]string{"schema_version", "scope", "state", "reason", "binding", "request_started_at", "observed_at", "identity", "sample", "get_attempts"},
		"reason", "binding", "request_started_at", "observed_at", "identity", "sample")
	if !ok {
		return false
	}
	var usage sreUsage
	if json.Unmarshal(raw, &usage) != nil || usage.SchemaVersion != 1 || usage.Scope != "fixed-trino-container" ||
		!dataOneOf(usage.State, "observed", "unknown") || usage.GetAttempts < 0 || usage.GetAttempts > 2 {
		return false
	}
	var started, request, observed time.Time
	if usage.Identity != nil {
		if _, ok := sreUsageObject(fields["identity"], []string{"namespace", "pod_name", "pod_uid", "container", "container_id_sha256", "restart_count", "started_at"}); !ok {
			return false
		}
		identity := usage.Identity
		if identity.Namespace != "shirokuma-dev" || identity.Container != "trino" || !sreUsageName(identity.PodName) ||
			!sreUsageIdentityRE.MatchString(identity.PodUID) || !sreUsageHashRE.MatchString(identity.ContainerIDSHA256) ||
			identity.RestartCount < 0 || identity.RestartCount > 1<<31-1 {
			return false
		}
		started, ok = sreUsageTimestamp(identity.StartedAt)
		if !ok || identity.StartedAt != started.Format("2006-01-02T15:04:05.000000Z") {
			return false
		}
	}
	if (usage.RequestStartedAt == nil) != (usage.ObservedAt == nil) {
		return false
	}
	if usage.RequestStartedAt != nil {
		request, ok = sreUsageTimestamp(*usage.RequestStartedAt)
		if !ok {
			return false
		}
		observed, ok = sreUsageTimestamp(*usage.ObservedAt)
		if !ok || request.After(observed) {
			return false
		}
	}
	if usage.State == "unknown" {
		return usage.Reason != nil && sreUsageReason(*usage.Reason) && usage.Sample == nil && usage.Binding == nil
	}
	if usage.Reason != nil || usage.Binding == nil || *usage.Binding != "name-time-correlated" ||
		usage.GetAttempts != 2 || usage.Identity == nil || usage.RequestStartedAt == nil || usage.Sample == nil {
		return false
	}
	if _, ok := sreUsageObject(fields["sample"], []string{"timestamp", "window_ns", "cpu_nanocores", "memory_working_set_bytes"}); !ok {
		return false
	}
	sample, ok := sreUsageTimestamp(usage.Sample.Timestamp)
	if !ok {
		return false
	}
	window, ok := sreUsageQuantity(usage.Sample.WindowNS, true)
	if !ok || window > int64(120*time.Second) {
		return false
	}
	if _, ok := sreUsageQuantity(usage.Sample.CPUNanocores, false); !ok {
		return false
	}
	if _, ok := sreUsageQuantity(usage.Sample.MemoryWorkingSetBytes, false); !ok {
		return false
	}
	// time.Time operations preserve exact ns for years beyond UnixNano's 2262
	// limit. The only duration converted from input is already bounded to 120s.
	return sample.Add(-time.Duration(window)).After(started.Add(999*time.Nanosecond)) &&
		!sample.Before(observed.Add(-120*time.Second)) && !sample.After(observed.Add(5*time.Second)) &&
		!observed.After(request.Add(20*time.Second))
}

func sreUsageTimestamp(value string) (time.Time, bool) {
	if !sreUsageStampRE.MatchString(value) {
		return time.Time{}, false
	}
	clock, err := time.Parse(time.RFC3339Nano, value)
	return clock, err == nil && clock.Year() >= 1970
}

func sreUsageQuantity(value string, positive bool) (int64, bool) {
	if !sreUsageQuantityRE.MatchString(value) {
		return 0, false
	}
	number, err := strconv.ParseInt(value, 10, 64)
	return number, err == nil && (!positive || number > 0)
}

func sreUsageName(value string) bool {
	if len(value) > 253 || !sreUsageNameRE.MatchString(value) {
		return false
	}
	for _, label := range strings.Split(value, ".") {
		if label == "" || len(label) > 63 || strings.HasPrefix(label, "-") || strings.HasSuffix(label, "-") {
			return false
		}
	}
	return true
}

func sreUsageReason(value string) bool {
	return dataOneOf(value, "POD_LIST_INCOMPLETE", "TARGET_NOT_FOUND", "TARGET_AMBIGUOUS", "POD_INVALID", "POD_NOT_RUNNING",
		"CONTAINER_UNSUPPORTED", "IDENTITY_CHANGED", "METRICS_INVALID", "QUANTITY_UNSUPPORTED", "TIME_INVALID",
		"SAMPLE_STALE", "SAMPLE_PRECEDES_CONTAINER", "READ_LIMIT", "READ_TIMEOUT", "READ_FAILED")
}

func compareSREUsage(before, after *sreUsage, reobserved bool) sreUsageComparison {
	result := sreUsageComparison{BeforeState: "not-observed", AfterState: "not-observed", Comparison: "incomparable",
		Limitations: []string{"name-time-correlation-only", "no-trend-or-cause-claim"}}
	if before != nil {
		result.BeforeState = before.State
	}
	if after != nil {
		result.AfterState = after.State
	}
	if !reobserved {
		result.AfterState, result.Comparison = "not-reobserved", "not-reobserved"
		return result
	}
	limitation := ""
	switch {
	case before == nil || after == nil:
		limitation = "resource-usage-not-observed"
	case before.State != "observed" || after.State != "observed":
		limitation = "resource-usage-unknown"
	case *before.Identity != *after.Identity:
		limitation = "resource-usage-identity-changed"
	default:
		left, _ := sreUsageTimestamp(before.Sample.Timestamp)
		right, _ := sreUsageTimestamp(after.Sample.Timestamp)
		switch {
		case right.Before(left):
			limitation = "resource-usage-time-reversed"
		case left.Equal(right) && before.Sample.WindowNS == after.Sample.WindowNS:
			if before.Sample.CPUNanocores == after.Sample.CPUNanocores && before.Sample.MemoryWorkingSetBytes == after.Sample.MemoryWorkingSetBytes {
				result.Comparison = "same-sample-window"
				limitation = "cached-sample-not-new-measurement"
			} else {
				limitation = "resource-usage-sample-inconsistent"
			}
		default:
			result.Comparison = "different-sample-window"
		}
	}
	if limitation != "" {
		result.Limitations = append(result.Limitations, limitation)
	}
	return result
}
