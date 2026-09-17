package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"path/filepath"
	"strings"
	"testing"
)

func statusLogsTestLines(request dataChildRequest) [][]byte {
	if request.Action != sreLogsAction {
		return statusUsageTestLines(request)
	}
	plain := request
	plain.Action = "status"
	lines := sreTestLines(plain, "passed")
	var value map[string]any
	_ = json.Unmarshal(lines[1], &value)
	value["schema_version"] = 3
	value["log_signals"] = sreLogsTestReport(sreLogsObserved(), nil, false)
	lines[1], _ = json.Marshal(value)
	return lines
}
func TestSRELogsFlagOnlyChangesRequestedStatusView(t *testing.T) {
	for _, tc := range []struct {
		name   string
		flags  []string
		action string
	}{{"plain", nil, "status"}, {"logs false", []string{"--logs=false"}, "status"}, {"usage", []string{"--usage"}, sreUsageAction}, {"logs", []string{"--logs"}, sreLogsAction}, {"false usage logs", []string{"--usage=false", "--logs"}, sreLogsAction}, {"false logs usage", []string{"--usage", "--logs=false"}, sreUsageAction}, {"both false", []string{"--usage=false", "--logs=false"}, "status"}} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			var wanted bytes.Buffer
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				calls++
				if r.Action != tc.action || r.Token != testWarehouseSession || r.Case != "" || r.Request != nil {
					t.Fatal("fixed status scope changed")
				}
				for _, line := range statusLogsTestLines(r) {
					wanted.Write(append(append([]byte{}, line...), '\n'))
					if e := emit(line); e != nil {
						return dataChildResult{Started: true}, e
					}
				}
				return dataChildResult{Started: true, ExitCode: 0}, nil
			})
			args := append([]string{"status", testWarehouseSession, "--repo-root", sreTestRoot(t)}, tc.flags...)
			out, e := executeSRE(t, child, args...)
			if e != nil || calls != 1 || out != wanted.String() || strings.Contains(out, "status-logs") {
				t.Fatalf("status bytes differ: %v", e)
			}
			if strings.Contains(out, `"log_signals"`) != (tc.action == sreLogsAction) || strings.Contains(out, `"resource_usage"`) != (tc.action == sreUsageAction) {
				t.Fatal("unrequested view exposed")
			}
		})
	}
}
func TestSRELogsInvalidCombinationsNeverStartChild(t *testing.T) {
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("invalid flags reached child")
		return dataChildResult{}, nil
	})
	for _, args := range [][]string{{"status", testWarehouseSession, "--usage", "--logs"}, {"status", testWarehouseSession, "--usage=true", "--logs=true"}, {"run", "--logs"}, {"run", "--logs=false"}, {"status", "PRIVATE", "--logs"}} {
		out, e := executeSRE(t, child, append(args, "--repo-root", sreTestRoot(t))...)
		if e == nil || out != "" || strings.Contains(e.Error(), "PRIVATE") {
			t.Fatal("invalid request accepted/exposed")
		}
	}
}
func TestSRELogsFixedChildAddsOnlyStatusFlag(t *testing.T) {
	r := dataChildRequest{Root: sreTestRoot(t), Action: sreLogsAction, Token: testWarehouseSession}
	cmd, e := sreCommand(r)
	if e != nil {
		t.Fatal(e)
	}
	want := []string{filepath.Join(r.Root, dataPython), "-I", "-B", filepath.Join(r.Root, sreScript), "status", "--evidence-token", r.Token, "--logs"}
	if strings.Join(cmd.Args, "\n") != strings.Join(want, "\n") || cmd.Dir != r.Root || !cmd.SysProcAttr.Setpgid {
		t.Fatal("fixed command differs")
	}
	if raw, e := io.ReadAll(cmd.Stdin); e != nil || len(raw) != 0 {
		t.Fatal("status inherited input")
	}
}
func TestSRELogsExactSchemaAndPublicFields(t *testing.T) {
	r := dataChildRequest{Action: sreLogsAction, Token: testWarehouseSession}
	line := statusLogsTestLines(r)[1]
	for _, mode := range []string{"schema1", "schema2", "missing", "null", "usage extra", "raw extra", "duplicate", "plain receiver", "usage receiver", "failed without workflow", "failed journal", "allocated3"} {
		t.Run(mode, func(t *testing.T) {
			var v map[string]any
			_ = json.Unmarshal(line, &v)
			request := r
			switch mode {
			case "schema1":
				v["schema_version"] = 1
			case "schema2":
				v["schema_version"] = 2
			case "missing":
				delete(v, "log_signals")
			case "null":
				v["log_signals"] = nil
			case "usage extra":
				v["resource_usage"] = nil
			case "raw extra":
				v["raw"] = "PRIVATE_LOG_SQL_TOKEN"
			case "plain receiver":
				request.Action = "status"
			case "usage receiver":
				request.Action = sreUsageAction
			case "failed without workflow", "failed journal":
				v["result"] = "failed"
				v["log_signals"] = sreLogsReport{Source: "saved-evidence", Availability: "unverified"}
				if mode == "failed without workflow" {
					v["workflow_token"] = nil
				} else {
					v["verification"] = "journal-only"
				}
			case "allocated3":
				v = map[string]any{"schema_version": 3, "event": "allocated", "command": "status", "evidence_token": r.Token}
			}
			raw, _ := json.Marshal(v)
			if mode == "duplicate" {
				raw = bytes.Replace(raw, []byte(`"state":"observed"`), []byte(`"state":"observed","state":"observed"`), 1)
			}
			if _, e := decodeSREEvent(raw, request); e == nil || strings.Contains(e.Error(), "PRIVATE") {
				t.Fatal("invalid frame accepted/exposed")
			}
		})
	}
}
func TestSRELogsFullFrameBoundsAndFinalExit(t *testing.T) {
	r := dataChildRequest{Action: sreLogsAction, Token: testWarehouseSession}
	line := statusLogsTestLines(r)[1]
	for _, length := range []int{4095, 4096} {
		raw := append(append([]byte{}, line...), bytes.Repeat([]byte(" "), length-len(line))...)
		_, e := decodeSREEvent(raw, r)
		if (e == nil) != (length == 4095) {
			t.Fatal("newline-inclusive bound differs")
		}
	}
	for _, broken := range []string{"exit", "unconfirmed", "raw error", "trailing", "duplicate final"} {
		t.Run(broken, func(t *testing.T) {
			var out bytes.Buffer
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				lines := statusLogsTestLines(r)
				for _, line := range lines {
					if e := emit(line); e != nil {
						return dataChildResult{Started: true}, e
					}
				}
				if strings.Contains(out.String(), `"event":"result"`) {
					t.Fatal("unconfirmed final exposed")
				}
				result := dataChildResult{Started: true, ExitCode: 0}
				switch broken {
				case "exit":
					result.ExitCode = 1
				case "unconfirmed":
					result.Unconfirmed = true
				case "raw error":
					return result, errors.New("PRIVATE")
				case "trailing":
					return result, emit([]byte("PRIVATE"))
				case "duplicate final":
					return result, emit(lines[1])
				}
				return result, nil
			})
			if e := runSRECommand(context.Background(), child, r, &out); e == nil || strings.Contains(out.String(), `"event":"result"`) || strings.Contains(e.Error(), "PRIVATE") {
				t.Fatal("unconfirmed logs accepted")
			}
		})
	}
}
func TestSRELogsValidLargeFrameStaysWithinExistingCap(t *testing.T) {
	a := sreLogsObserved()
	a.Identity.PodName = strings.Repeat(strings.Repeat("a", 63)+".", 3) + strings.Repeat("a", 61)
	a.Identity.RestartCount = 1<<31 - 1
	a.Fragment.ReturnedBytes = 32767
	a.Fragment.CompleteLines = 4096
	a.Fragment.HeaderLines = 3000
	a.Fragment.WarnHeaderLines = 1500
	a.Fragment.ErrorHeaderLines = 1500
	a.Fragment.UnparsedLines = 1096
	b := sreLogsObserved()
	*b = *a
	identity := *a.Identity
	b.Identity = &identity
	b.Identity.RestartCount--
	report := sreLogsTestReport(a, b, true)
	if _, e := decodeSRELogs(sreUsageTestRaw(t, report), sreUsageTestEvent()); e != nil {
		t.Fatal(e)
	}
	r := dataChildRequest{Action: sreLogsAction, Token: testWarehouseSession}
	var v map[string]any
	_ = json.Unmarshal(statusLogsTestLines(r)[1], &v)
	v["log_signals"] = report
	v["assessment"] = "healthy-observed-scope"
	raw, _ := json.Marshal(v)
	if len(raw)+1 > 4096 {
		t.Fatal("valid bounded logs exceed existing cap")
	}
	if _, e := decodeSREEvent(raw, r); e != nil {
		t.Fatal(e)
	}
}
