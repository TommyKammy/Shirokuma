package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

func countRoot(t *testing.T) string {
	t.Helper()
	root := queryReadTestRoot(t)
	path := filepath.Join(root, queryCountScript)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func countRequest(historical, filtered bool) dataChildRequest {
	value := queryCountRequest{Command: "count", Table: "smoke.fixture_v1"}
	if historical {
		id := snapshotOld
		value.SnapshotID = &id
	}
	if filtered {
		value.Where = &queryFilterWhere{Column: "id", Literal: "9007199254740993"}
	}
	raw, _ := json.Marshal(value)
	return dataChildRequest{Action: "count", Case: queryCountCase, Token: testWarehouseSession, Request: raw}
}

func countObservation(request dataChildRequest) map[string]any {
	input, _ := decodeQueryCountRequest(request.Request, "count")
	selected := snapshotHead
	record := snapshotRecord(snapshotHead, snapshotOld, "2", true, true)
	if input.SnapshotID != nil {
		selected = *input.SnapshotID
		record = snapshotRecord(selected, nil, "1", false, false)
	}
	var where any
	if input.Where != nil {
		where = map[string]any{"column": input.Where.Column, "type": "bigint", "operator": "eq"}
	}
	return map[string]any{"schema_version": 4, "table": "iceberg.smoke.fixture_v1", "table_uuid": testWarehouseRequest,
		"schema_sha256": strings.Repeat("a", 64), "snapshot_id": selected, "current_snapshot_id": snapshotHead,
		"selected_snapshot": record, "count": "2", "where": where, "complete": true}
}

func countLines(request dataChildRequest, outcome string) [][]byte {
	a, _ := json.Marshal(map[string]any{"schema_version": 4, "event": "allocated", "command": "query count", "evidence_token": request.Token})
	v := map[string]any{"schema_version": 4, "event": "result", "command": "query count", "evidence_token": request.Token,
		"result": outcome, "observation": nil, "error_code": "COUNT_RESULT_INVALID", "cleanup_verified": false,
		"evidence": "tmp/local-experiment/query-count/" + request.Token + "/summary.json"}
	if outcome == "passed" {
		v["observation"], v["error_code"], v["cleanup_verified"] = countObservation(request), nil, true
	}
	if outcome == "interrupted" {
		v["error_code"] = "INTERRUPTED"
	}
	b, _ := json.Marshal(v)
	return [][]byte{a, b}
}

func countExecute(t *testing.T, child dataChildRunner, args ...string) (string, error) {
	t.Helper()
	root, profile := "", localExperimentProfile
	p := &cobra.Command{Use: "query", SilenceErrors: true, SilenceUsage: true}
	p.PersistentFlags().StringVar(&root, "repo-root", "", "")
	p.PersistentFlags().StringVar(&profile, "profile", profile, "")
	p.AddCommand(newQueryCountCommand(child, &root, &profile))
	var out bytes.Buffer
	p.SetOut(&out)
	p.SetErr(io.Discard)
	p.SetArgs(args)
	err := p.Execute()
	return out.String(), err
}

func TestQueryCountCommandUsesOnlyFixedCountProfile(t *testing.T) {
	root := countRoot(t)
	for _, historical := range []bool{false, true} {
		for _, filtered := range []bool{false, true} {
			calls := 0
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				calls++
				value, err := decodeQueryCountRequest(r.Request, "count")
				if err != nil || r.Case != queryCountCase || (value.SnapshotID != nil) != historical || (value.Where != nil) != filtered {
					t.Fatal("count route differs")
				}
				for _, line := range countLines(r, "passed") {
					if err := emit(line); err != nil {
						return dataChildResult{Started: true}, err
					}
				}
				return dataChildResult{Started: true, ExitCode: 0}, nil
			})
			args := []string{"count", "--table", "smoke.fixture_v1", "--repo-root", root}
			if historical {
				args = append(args, "--snapshot-id", snapshotOld)
			}
			if filtered {
				args = append(args, "--where-column", "id", "--equals", "9007199254740993")
			}
			out, err := countExecute(t, child, args...)
			if err != nil || calls != 1 || !strings.Contains(out, `"schema_version":4`) || strings.Contains(out, "9007199254740993") {
				t.Fatalf("%v %s", err, out)
			}
		}
	}
}

func TestQueryCountInvalidFlagsNeverStartChildOrExposeValues(t *testing.T) {
	for _, args := range [][]string{
		{"count"}, {"count", "extra"}, {"count", "--table", "smoke.fixture_v1", "--limit", "10"},
		{"count", "--table", "smoke.fixture_v1", "--columns", "id"},
		{"count", "--table", "smoke.fixture_v1", "--snapshot-id", ""},
		{"count", "--table", "smoke.fixture_v1", "--where-column", "id"},
		{"count", "--table", "smoke.fixture_v1", "--equals", "1"},
		{"count", "--table", "smoke.fixture_v1", "--where-column", "id", "--equals", "PRIVATE_INPUT"},
		{"count", "--table", "smoke.fixture_v1", "--profile", "production"},
	} {
		out, err := countExecute(t, filterNever(t), args...)
		if err == nil || out != "" || strings.Contains(err.Error(), "PRIVATE_INPUT") {
			t.Fatalf("%v %s", err, out)
		}
	}
}

func TestQueryCountRequestExactTypesAndBounds(t *testing.T) {
	r := countRequest(false, false)
	if _, err := decodeQueryCountRequest(r.Request, "count"); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(map[string]any){
		func(v map[string]any) { v["limit"] = 1 }, func(v map[string]any) { delete(v, "where") },
		func(v map[string]any) { v["command"] = "select" }, func(v map[string]any) { v["table"] = nil },
		func(v map[string]any) { v["snapshot_id"] = 1 }, func(v map[string]any) { v["snapshot_id"] = "01" },
		func(v map[string]any) { v["snapshot_id"] = "9223372036854775808" },
		func(v map[string]any) { v["where"] = false },
		func(v map[string]any) { v["where"] = map[string]any{"column": "id", "literal": "1", "type": "bigint"} },
		func(v map[string]any) { v["where"] = map[string]any{"column": "id", "literal": nil} },
	} {
		var v map[string]any
		_ = json.Unmarshal(r.Request, &v)
		change(v)
		raw, _ := json.Marshal(v)
		if _, err := decodeQueryCountRequest(raw, "count"); err == nil {
			t.Fatal("invalid request accepted")
		}
	}
	for _, raw := range [][]byte{[]byte(`{"command":"count","command":"count"}`), []byte("\xff"), bytes.Repeat([]byte(" "), 4097)} {
		if _, err := decodeQueryCountRequest(raw, "count"); err == nil {
			t.Fatal("invalid JSON accepted")
		}
	}
}

func TestQueryCountCanonicalExactNonnegativeInt64(t *testing.T) {
	r := countRequest(false, false)
	input, _ := decodeQueryCountRequest(r.Request, "count")
	for _, count := range []string{"0", "2", "3", "9007199254740993", "9223372036854775807"} {
		v := countObservation(r)
		v["count"] = count
		raw, _ := json.Marshal(v)
		if !queryCountObservation(raw, input) {
			t.Fatalf("valid count %s refused", count)
		}
	}
	for _, count := range []any{nil, true, 0, 1.5, "-0", "-1", "+1", "00", "01", "1.0", "1e0", "1\n", "9223372036854775808"} {
		v := countObservation(r)
		v["count"] = count
		raw, _ := json.Marshal(v)
		if queryCountObservation(raw, input) {
			t.Fatal("inexact count accepted")
		}
	}
}

func TestQueryCountProjectionBindsTableHeadSnapshotAndSchema(t *testing.T) {
	for _, historical := range []bool{false, true} {
		r := countRequest(historical, false)
		input, _ := decodeQueryCountRequest(r.Request, "count")
		for _, change := range []func(map[string]any){
			func(v map[string]any) { v["schema_version"] = 3 }, func(v map[string]any) { v["complete"] = 1 },
			func(v map[string]any) { v["rows"] = []any{} }, func(v map[string]any) { v["table"] = "iceberg.smoke.other" },
			func(v map[string]any) { v["table_uuid"] = "invalid" }, func(v map[string]any) { v["schema_sha256"] = "invalid" },
			func(v map[string]any) { v["snapshot_id"] = "1" }, func(v map[string]any) { v["current_snapshot_id"] = nil },
			func(v map[string]any) { v["selected_snapshot"].(map[string]any)["is_current"] = historical },
			func(v map[string]any) {
				v["selected_snapshot"].(map[string]any)["schema_compatibility"] = "schema-mismatch"
			},
			func(v map[string]any) {
				v["where"] = map[string]any{"column": "id", "type": "bigint", "operator": "eq"}
			},
		} {
			v := countObservation(r)
			change(v)
			raw, _ := json.Marshal(v)
			if queryCountObservation(raw, input) {
				t.Fatal("projection identity mismatch accepted")
			}
		}
	}
}

func TestQueryCountWhereUsesSharedTypedLiteralAndLeaksNoLiteral(t *testing.T) {
	r := countRequest(false, true)
	input, _ := decodeQueryCountRequest(r.Request, "count")
	for _, tc := range []struct{ kind, literal string }{
		{"integer", "-2147483648"}, {"bigint", "9007199254740993"}, {"decimal(38,6)", "12345678901234567890123456789012.123456"},
		{"date", "1900-01-01"}, {"boolean", "false"},
	} {
		input.Where = &queryFilterWhere{Column: "id", Literal: tc.literal}
		v := countObservation(r)
		v["where"] = map[string]any{"column": "id", "type": tc.kind, "operator": "eq"}
		raw, _ := json.Marshal(v)
		if !queryCountObservation(raw, input) {
			t.Fatal("typed count predicate refused")
		}
	}
	input, _ = decodeQueryCountRequest(r.Request, "count")
	for _, descriptor := range []any{nil, map[string]any{"column": "id", "type": "varchar", "operator": "eq"},
		map[string]any{"column": "id", "type": "bigint", "operator": "ne"},
		map[string]any{"column": "other", "type": "bigint", "operator": "eq"},
		map[string]any{"column": "id", "type": "bigint", "operator": "eq", "literal": "PRIVATE"},
		map[string]any{"column": "id", "type": "integer", "operator": "eq"},
	} {
		v := countObservation(r)
		v["where"] = descriptor
		raw, _ := json.Marshal(v)
		if queryCountObservation(raw, input) {
			t.Fatal("invalid predicate accepted")
		}
	}
}

func TestQueryCountEnvelopeRejectsInvalidStateAndRawFields(t *testing.T) {
	r := countRequest(false, false)
	for _, change := range []func(map[string]any){
		func(v map[string]any) { v["schema_version"] = 3 }, func(v map[string]any) { v["command"] = "query select" },
		func(v map[string]any) { v["evidence_token"] = strings.Repeat("a", 32) }, func(v map[string]any) { v["cleanup_verified"] = false },
		func(v map[string]any) { v["evidence"] = "tmp/local-experiment/query-read/" + r.Token + "/summary.json" },
		func(v map[string]any) { v["literal"] = "PRIVATE" },
		func(v map[string]any) { v["result"] = "failed"; v["error_code"] = "COUNT_RESULT_INVALID" },
		func(v map[string]any) { v["result"] = "failed"; v["observation"] = nil; v["error_code"] = "PRIVATE" },
		func(v map[string]any) {
			v["result"] = "interrupted"
			v["observation"] = nil
			v["error_code"] = "COUNT_RESULT_INVALID"
		},
	} {
		var v map[string]any
		_ = json.Unmarshal(countLines(r, "passed")[1], &v)
		change(v)
		raw, _ := json.Marshal(v)
		if _, err := decodeQueryCountEvent(raw, r); err == nil || strings.Contains(err.Error(), "PRIVATE") {
			t.Fatal("invalid envelope accepted")
		}
	}
	for _, raw := range [][]byte{[]byte(`{"schema_version":4,"schema_version":4}`), bytes.Repeat([]byte(" "), queryCountResponseLimit+1), []byte("\xff")} {
		if _, err := decodeQueryCountEvent(raw, r); err == nil {
			t.Fatal("invalid protocol JSON accepted")
		}
	}
}

func TestQueryCountFinalRequiresCoherentChildExit(t *testing.T) {
	r := countRequest(false, false)
	lines := countLines(r, "passed")
	for _, tc := range []struct {
		name        string
		lines       [][]byte
		exit        int
		err         error
		unconfirmed bool
	}{
		{"passed", lines, 0, nil, false}, {"wrong-exit", lines, 1, nil, false}, {"late-error", lines, 0, errors.New("PRIVATE"), false},
		{"unconfirmed", lines, 0, nil, true}, {"no-allocation", lines[1:], 0, nil, false}, {"double-final", append(append([][]byte{}, lines...), lines[1]), 0, nil, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var out bytes.Buffer
			child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				for _, line := range tc.lines {
					if err := emit(line); err != nil {
						return dataChildResult{Started: true}, err
					}
				}
				if strings.Contains(out.String(), `"event":"result"`) {
					t.Fatal("result released before child exit")
				}
				return dataChildResult{Started: true, ExitCode: tc.exit, Unconfirmed: tc.unconfirmed}, tc.err
			})
			err := runQueryCountCommand(context.Background(), child, r, &out)
			if (err == nil) != (tc.name == "passed") || strings.Contains(out.String(), `"result":"passed"`) != (tc.name == "passed") || strings.Contains(out.String(), "PRIVATE") {
				t.Fatalf("%v %s", err, &out)
			}
		})
	}
}

func TestQueryCountFailedInterruptedAndUnknownAreNotPassed(t *testing.T) {
	r := countRequest(false, false)
	for _, tc := range []struct {
		outcome string
		exit    int
	}{{"failed", 1}, {"interrupted", 130}, {"interrupted", 143}} {
		var out bytes.Buffer
		child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
			for _, line := range countLines(r, tc.outcome) {
				if err := emit(line); err != nil {
					return dataChildResult{Started: true}, err
				}
			}
			return dataChildResult{Started: true, ExitCode: tc.exit}, nil
		})
		if err := runQueryCountCommand(context.Background(), child, r, &out); ExitCode(err) != tc.exit || strings.Contains(out.String(), `"count":`) {
			t.Fatalf("%v %s", err, &out)
		}
	}
	var out bytes.Buffer
	child := dataChildFunc(func(_ context.Context, _ dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		_ = emit(countLines(r, "passed")[0])
		return dataChildResult{Started: true, ExitCode: 130, Interrupted: true}, errDataInterrupted
	})
	if err := runQueryCountCommand(context.Background(), child, r, &out); ExitCode(err) != 130 || strings.Contains(out.String(), `"event":"result"`) {
		t.Fatal("unknown cancellation became a result")
	}
}

func TestQueryCountChildPipesRequestAndKeepsFixedArgvEnvironment(t *testing.T) {
	r := countRequest(true, true)
	r.Root = countRoot(t)
	t.Setenv("PRIVATE_SENTINEL", "forbidden")
	cmd, err := queryCountCommand(r)
	if err != nil {
		t.Fatal(err)
	}
	args := strings.Join(cmd.Args, " ")
	if !strings.Contains(args, queryCountScript) || !strings.Contains(args, "count --evidence-token") || !strings.Contains(args, "--request-stdin") || strings.Contains(args, "fixture_v1") || strings.Contains(args, "9007199254740993") || strings.Contains(strings.Join(cmd.Env, " "), "PRIVATE_SENTINEL") || cmd.SysProcAttr == nil || !cmd.SysProcAttr.Setpgid {
		t.Fatal("count child boundary differs")
	}
	raw, err := io.ReadAll(cmd.Stdin)
	if err != nil || !bytes.Equal(raw, r.Request) {
		t.Fatal("stdin request differs")
	}
	for _, which := range []string{"case", "action", "token"} {
		bad := r
		switch which {
		case "case":
			bad.Case = queryFilterCase
		case "action":
			bad.Action = "select"
		case "token":
			bad.Token = "INVALID"
		}
		if _, err := queryCountCommand(bad); err == nil {
			t.Fatal("invalid child accepted")
		}
	}
	path := filepath.Join(r.Root, queryCountScript)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if _, err := queryCountCommand(r); err == nil {
		t.Fatal("missing count entrypoint accepted")
	}
	if err := os.Symlink(filepath.Join(r.Root, queryReadScript), path); err != nil {
		t.Fatal(err)
	}
	if _, err := queryCountCommand(r); err == nil {
		t.Fatal("symlink entrypoint accepted")
	}
}

func TestQueryCountTransportRetainsOwnedCancellation(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "cleaned")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	child := execQueryCountChild{grace: time.Second, command: func(dataChildRequest) (*exec.Cmd, error) { return queryTestProcess("cancel", marker), nil }}
	result, err := child.Run(ctx, dataChildRequest{}, func([]byte) error { cancel(); return nil })
	if !errors.Is(err, errDataInterrupted) || !result.Interrupted || result.ExitCode != 130 {
		t.Fatalf("owned cancellation lost: %v %+v", err, result)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatal("cleanup marker absent")
	}
}
