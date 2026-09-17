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

func statusUsageTestLines(request dataChildRequest) [][]byte {
	plain := request
	plain.Action = "status"
	lines := sreTestLines(plain, "passed")
	if request.Action == sreUsageAction {
		var value map[string]any
		_ = json.Unmarshal(lines[1], &value)
		value["schema_version"] = 2
		value["resource_usage"] = map[string]any{"source": "saved-evidence", "availability": "legacy-absent",
			"initial": nil, "followup": nil, "comparison": nil}
		lines[1], _ = json.Marshal(value)
	}
	return lines
}

func TestSREUsageFlagOnlyChangesOptInStatusProtocol(t *testing.T) {
	for _, flag := range []string{"", "--usage=false", "--usage"} {
		t.Run(flag, func(t *testing.T) {
			wantAction := "status"
			if flag == "--usage" {
				wantAction = sreUsageAction
			}
			calls := 0
			child := dataChildFunc(func(_ context.Context, request dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				calls++
				if request.Action != wantAction || request.Token != testWarehouseSession || request.Case != "" || request.Request != nil {
					t.Fatal("status changed the token or request scope")
				}
				for _, line := range statusUsageTestLines(request) {
					if err := emit(line); err != nil {
						return dataChildResult{Started: true}, err
					}
				}
				return dataChildResult{Started: true, ExitCode: 0}, nil
			})
			args := []string{"status", testWarehouseSession, "--repo-root", sreTestRoot(t)}
			if flag != "" {
				args = append(args, flag)
			}
			out, err := executeSRE(t, child, args...)
			if err != nil || calls != 1 || strings.Contains(out, sreUsageAction) {
				t.Fatalf("status protocol failed: %v", err)
			}
			if strings.Contains(out, `"resource_usage"`) != (flag == "--usage") {
				t.Fatal("usage output escaped the opt-in flag")
			}
		})
	}
}

func TestSREUsageRunFlagRejectedBeforeChild(t *testing.T) {
	child := dataChildFunc(func(context.Context, dataChildRequest, func([]byte) error) (dataChildResult, error) {
		t.Fatal("run --usage reached a child")
		return dataChildResult{}, nil
	})
	if out, err := executeSRE(t, child, "run", "--usage", "--repo-root", sreTestRoot(t)); err == nil || out != "" {
		t.Fatal("run accepted a status-only flag")
	}
}

func TestSREUsageFixedCommandOnlyAddsStatusFlag(t *testing.T) {
	request := dataChildRequest{Root: sreTestRoot(t), Action: sreUsageAction, Token: testWarehouseSession}
	cmd, err := sreCommand(request)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join(request.Root, dataPython), "-I", "-B", filepath.Join(request.Root, sreScript),
		"status", "--evidence-token", request.Token, "--usage"}
	if strings.Join(cmd.Args, "\n") != strings.Join(want, "\n") || cmd.Dir != request.Root || !cmd.SysProcAttr.Setpgid {
		t.Fatal("usage escaped the existing fixed status command")
	}
	if value, err := io.ReadAll(cmd.Stdin); err != nil || len(value) != 0 {
		t.Fatal("usage inherited input")
	}
}

func TestSREUsageRequiresMatchingSchemaAndClosedFields(t *testing.T) {
	request := dataChildRequest{Action: sreUsageAction, Token: testWarehouseSession}
	line := statusUsageTestLines(request)[1]
	for _, mode := range []string{"schema-one", "missing-usage", "null-usage", "extra", "duplicate", "legacy-receiver", "failed-without-workflow", "failed-journal-only"} {
		t.Run(mode, func(t *testing.T) {
			var value map[string]any
			_ = json.Unmarshal(line, &value)
			r := request
			switch mode {
			case "schema-one":
				value["schema_version"] = 1
			case "missing-usage":
				delete(value, "resource_usage")
			case "null-usage":
				value["resource_usage"] = nil
			case "extra":
				value["private_log"] = "PRIVATE"
			case "legacy-receiver":
				r.Action = "status"
			case "failed-without-workflow", "failed-journal-only":
				value["result"] = "failed"
				value["resource_usage"].(map[string]any)["availability"] = "unverified"
				if mode == "failed-without-workflow" {
					value["workflow_token"] = nil
				} else {
					value["verification"] = "journal-only"
				}
			}
			raw, _ := json.Marshal(value)
			if mode == "duplicate" {
				raw = bytes.Replace(raw, []byte(`"source":"saved-evidence"`), []byte(`"source":"saved-evidence","source":"saved-evidence"`), 1)
			}
			if _, err := decodeSREEvent(raw, r); err == nil {
				t.Fatal("invalid usage frame was accepted")
			}
		})
	}
}

func TestSREUsageFrameIncludesNewlineAndWaitsForConfirmedExit(t *testing.T) {
	request := dataChildRequest{Action: sreUsageAction, Token: testWarehouseSession}
	line := statusUsageTestLines(request)[1]
	padding := dataResponseLimit - len(line)
	if _, err := decodeSREEvent(append(append([]byte{}, line...), bytes.Repeat([]byte(" "), padding)...), request); err == nil {
		t.Fatal("accepted a frame exceeding the newline-inclusive limit")
	}
	for _, broken := range []string{"exit", "unconfirmed", "raw-error"} {
		var out bytes.Buffer
		child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			for _, v := range statusUsageTestLines(r) {
				if err := emit(v); err != nil {
					return dataChildResult{Started: true}, err
				}
			}
			if strings.Contains(out.String(), `"event":"result"`) {
				t.Fatal("usage was displayed before the child exited")
			}
			result := dataChildResult{Started: true, ExitCode: 0}
			if broken == "exit" {
				result.ExitCode = 1
			} else if broken == "unconfirmed" {
				result.Unconfirmed = true
			} else {
				return result, errors.New("PRIVATE")
			}
			return result, nil
		})
		if err := runSRECommand(context.Background(), child, request, &out); err == nil || strings.Contains(out.String(), `"event":"result"`) || strings.Contains(err.Error(), "PRIVATE") {
			t.Fatal("unconfirmed status was exposed as verified usage")
		}
	}
}
