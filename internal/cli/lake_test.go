package cli

import (
	"bytes"
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
)

type fakeLake struct {
	args []string
	root string
	data []byte
	err  error
}

func (f *fakeLake) RunLake(_ context.Context, root string, args []string) ([]byte, error) {
	f.root, f.args = root, args
	return f.data, f.err
}

func executeLake(f *fakeLake, args ...string) (string, error) {
	cmd := newLakeCommand(f)
	cmd.SilenceErrors, cmd.SilenceUsage = true, true
	var output bytes.Buffer
	cmd.SetOut(&output)
	cmd.SetErr(&output)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return output.String(), err
}

func TestLakeIngestFlags(t *testing.T) {
	for _, tc := range []struct {
		args, want []string
		root       string
	}{
		{[]string{"ingest", "data.csv"}, []string{"--mode", "replace", "--format", "auto", "--batch-size", "500", "--", "data.csv"}, ""},
		{[]string{"ingest", "my data.json", "--table", "sandbox.custom", "--mode", "append", "--format", "jsonl", "--batch-size", "2", "--repo-root", "/tmp/repo"},
			[]string{"--mode", "append", "--format", "jsonl", "--batch-size", "2", "--table", "sandbox.custom", "--", "my data.json"}, "/tmp/repo"},
		{[]string{"ingest", "--", "--filename.csv"}, []string{"--mode", "replace", "--format", "auto", "--batch-size", "500", "--", "--filename.csv"}, ""},
		{[]string{"ingest", "data.csv", "--mode", "upsert", "--key", "id"},
			[]string{"--mode", "upsert", "--format", "auto", "--batch-size", "500", "--key", "id", "--", "data.csv"}, ""},
		{[]string{"ingest", "data.csv", "--key", ""},
			[]string{"--mode", "replace", "--format", "auto", "--batch-size", "500", "--key", "", "--", "data.csv"}, ""},
		{[]string{"ingest", "data.csv", "--schema-hints", `{"id":"BIGINT","name":"VARCHAR"}`, "--encoding", "shift_jis"},
			[]string{"--mode", "replace", "--format", "auto", "--batch-size", "500", "--schema-hints", `{"id":"BIGINT","name":"VARCHAR"}`, "--encoding", "shift_jis", "--", "data.csv"}, ""},
		{[]string{"ingest", "data.csv", "--schema-hints", ""},
			[]string{"--mode", "replace", "--format", "auto", "--batch-size", "500", "--schema-hints", "", "--", "data.csv"}, ""},
		{[]string{"ingest", "data.csv", "--encoding", ""},
			[]string{"--mode", "replace", "--format", "auto", "--batch-size", "500", "--encoding", "", "--", "data.csv"}, ""},
	} {
		f := &fakeLake{data: []byte(`{"action":"ingest","rows_ingested":7}`)}
		out, err := executeLake(f, tc.args...)
		if err != nil || !reflect.DeepEqual(f.args, tc.want) || f.root != tc.root || !strings.Contains(out, `"rows_ingested":7`) {
			t.Fatalf("args=%v forwarded=%v output=%s error=%v", tc.args, f.args, out, err)
		}
	}
}

func TestLakeRejectsInvalidArgumentsBeforeRunner(t *testing.T) {
	for _, args := range [][]string{{"ingest"}, {"ingest", "a", "b"}, {"ingest", "a", "--mode", "bad"},
		{"ingest", "a", "--format", "bad"}, {"ingest", "a", "--batch-size", "0"}, {"ingest", "a", "--batch-size", "-1"},
		{"ingest", "a", "--mode", "upsert"}, {"ingest", "a", "--mode", "upsert", "--key", ""},
		{"ingest", "a", "--mode", "upsert", "--key", " \t "}} {
		f := &fakeLake{}
		if _, err := executeLake(f, args...); err == nil || f.args != nil {
			t.Fatalf("accepted %v: %v", args, err)
		}
	}
}

func TestLakeRejectsInvalidReportsAndRunnerErrors(t *testing.T) {
	for _, data := range []string{"null", "[]", "{}", "noise", `{"action":"query"}`, `{"action":1}`, `{"action":"ingest"} {}`} {
		out, err := executeLake(&fakeLake{data: []byte(data)}, "ingest", "a.csv")
		if err == nil || out != "" {
			t.Fatalf("accepted %s: %q %v", data, out, err)
		}
	}
	want := errors.New("offline")
	if _, err := executeLake(&fakeLake{err: want}, "ingest", "a.csv"); !errors.Is(err, want) {
		t.Fatalf("runner error lost: %v", err)
	}
}

func TestRootContainsLakeIngest(t *testing.T) {
	cmd, _, err := NewRootCommand(&bytes.Buffer{}).Find([]string{"lake", "ingest"})
	if err != nil || cmd.Name() != "ingest" || cmd.Parent().Name() != "lake" {
		t.Fatalf("missing lake ingest: %v", err)
	}
}

func TestLakeDatabaseForwarding(t *testing.T) {
	f := &fakeLake{data: []byte(`{"action":"ingest","context":{}}`)}
	_, err := executeLake(f, "ingest", "rows.csv", "--database", "sales", "--schema", "raw", "--mode", "create", "--table", "orders", "--idempotency-key", "once", "--atomic-append", "--delimiter", ";")
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(f.args, " ")
	for _, expected := range []string{"--database sales", "--schema raw", "--mode create", "--idempotency-key once", "--atomic-append", "--delimiter ;", "-- rows.csv"} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("missing %s in %v", expected, f.args)
		}
	}
	for _, args := range [][]string{
		{"ingest", "rows.csv", "--database", "sales", "--schema", "raw", "--table", "orders", "--idempotency-key", "once"},
		{"ingest", "rows.csv", "--database", "sales", "--mode", "create", "--table", "orders"},
		{"export", "orders", "--output", "out.csv", "--database", "sales", "--schema", "raw", "--idempotency-key", "once", "--max-rows", "1", "--full"},
		{"export", "orders", "--output", "out.parquet", "--database", "sales", "--schema", "raw", "--idempotency-key", "once", "--format", "parquet"},
	} {
		f = &fakeLake{}
		if _, err := executeLake(f, args...); err == nil || f.args != nil {
			t.Fatalf("accepted %v: %v", args, err)
		}
	}
}

func TestLakeNestedDataOps(t *testing.T) {
	for _, tc := range []struct {
		group, action string
		flags         []string
	}{
		{"wap", "save", []string{"--model-id", "m", "--sql", "SELECT * FROM source", "--sources", `{}`, "--target", `{}`, "--expected-version", "null"}},
		{"wap", "build", []string{"--model-id", "m", "--version", "2", "--idempotency-key", "once"}},
		{"wap", "models", nil},
		{"wap", "get-publication", []string{"--id", "pub"}},
		{"wap", "model", []string{"--model-id", "m"}},
	} {
		f := &fakeLake{data: []byte(`{"action":"` + tc.group + `-` + tc.action + `","context":{},"model":{},"items":[],"publication":{},"operation":{"state":"FINISHED"}}`)}
		args := append([]string{tc.group, tc.action, "--database", "sales", "--schema", "raw"}, tc.flags...)
		if _, err := executeLake(f, args...); err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if !reflect.DeepEqual(f.args[:2], []string{"--" + tc.group, tc.action}) {
			t.Fatal(f.args)
		}
		for _, flag := range tc.flags {
			if !strings.Contains(strings.Join(f.args, " "), flag) {
				t.Fatal(f.args)
			}
		}
	}
	f := &fakeLake{}
	if _, err := executeLake(f, "wap", "build", "--database", "sales", "--schema", "raw"); err == nil || f.args != nil {
		t.Fatal("missing submission key accepted")
	}
}

func TestLakeNestedRejectsMissingFieldsAndWrongReply(t *testing.T) {
	for _, data := range []string{`{"context":{}}`, `{"context":{},"operation":null}`, `null`, `[]`} {
		f := &fakeLake{data: []byte(data)}
		out, err := executeLake(f, "operation", "get", "op", "--context-file", "saved.json")
		if err == nil || out != "" {
			t.Fatalf("accepted %s: %s %v", data, out, err)
		}
	}
	for _, args := range [][]string{
		{"operation", "cancel", "--database", "sales", "--schema", "raw", "--id", "op"},
		{"wap", "build", "--database", "sales", "--schema", "raw", "--idempotency-key", "once"},
	} {
		f := &fakeLake{}
		if _, err := executeLake(f, args...); err == nil || f.args != nil {
			t.Fatalf("accepted %v: %v", args, err)
		}
	}
}

func TestLakeOperationFrozenContextFile(t *testing.T) {
	f := &fakeLake{data: []byte(`{"action":"operation-cancel","context":{},"operation":{"state":"CANCELED"}}`)}
	if _, err := executeLake(f, "operation", "cancel", "op", "--context-file", "saved.json"); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(f.args, []string{"--operation", "cancel", "--id", "op", "--context-file", "saved.json"}) {
		t.Fatal(f.args)
	}
}

func TestLakeAcceptedFailurePreservesRecoveryJSON(t *testing.T) {
	want := errors.New("accepted but uncertain")
	for _, tc := range []struct {
		action string
		args   []string
	}{
		{"ingest", []string{"ingest", "rows.csv"}},
		{"export", []string{"export", "orders", "--output", "out.csv"}},
		{"wap-build", []string{"wap", "build", "--database", "sales", "--schema", "raw", "--model-id", "m", "--version", "1", "--idempotency-key", "once"}},
		{"operation-cancel", []string{"operation", "cancel", "op", "--context-file", "saved.json"}},
	} {
		f := &fakeLake{data: []byte(`{"action":"` + tc.action + `","context":{},"operation":{"id":"saved","state":"UNKNOWN"},"error":{"code":"OPERATION_UNKNOWN"}}`), err: want}
		out, err := executeLake(f, tc.args...)
		if !errors.Is(err, want) || !strings.Contains(out, `"id":"saved"`) {
			t.Fatalf("%s: %s %v", tc.action, out, err)
		}
		f.data = []byte(`{"action":"wrong","context":{},"operation":{}}`)
		if out, _ := executeLake(f, tc.args...); out != "" {
			t.Fatalf("mismatched recovery printed: %s", out)
		}
	}
}

func TestLakeExportFlags(t *testing.T) {
	for _, tc := range []struct {
		args, want []string
		root       string
	}{
		{[]string{"export", "raw.orders", "--output", "out.csv"},
			[]string{"--export", "raw.orders", "--output", "out.csv", "--format", "auto"}, ""},
		{[]string{"export", "raw.orders", "--output", "out.csv", "--full"},
			[]string{"--export", "raw.orders", "--output", "out.csv", "--format", "auto"}, ""},
		{[]string{"export", "raw.orders", "--output", "out.csv", "--max-rows", "10000"},
			[]string{"--export", "raw.orders", "--output", "out.csv", "--format", "auto", "--max-rows", "10000"}, ""},
		{[]string{"export", "raw.orders", "--output", "out.csv", "--full=false", "--max-rows", "7"},
			[]string{"--export", "raw.orders", "--output", "out.csv", "--format", "auto", "--max-rows", "7"}, ""},
		{[]string{"export", "iceberg.marts.orders", "--output", "my data.jsonl", "--format", "jsonl", "--max-rows", "7", "--repo-root", "/tmp/repo"},
			[]string{"--export", "iceberg.marts.orders", "--output", "my data.jsonl", "--format", "jsonl", "--max-rows", "7"}, "/tmp/repo"},
	} {
		f := &fakeLake{data: []byte(`{"action":"export","row_count":7}`)}
		out, err := executeLake(f, tc.args...)
		if err != nil || !reflect.DeepEqual(f.args, tc.want) || f.root != tc.root || !strings.Contains(out, `"row_count":7`) {
			t.Fatalf("args=%v forwarded=%v output=%s error=%v", tc.args, f.args, out, err)
		}
	}
	for _, maxRows := range []string{"7", "0", "-1"} {
		f := &fakeLake{data: []byte(`{"action":"export"}`)}
		_, err := executeLake(f, "export", "raw.orders", "--output", "out.csv", "--max-rows", maxRows, "--full")
		want := []string{"--export", "raw.orders", "--output", "out.csv", "--format", "auto"}
		if err != nil || !reflect.DeepEqual(f.args, want) {
			t.Fatalf("--full with --max-rows=%s forwarded=%v error=%v", maxRows, f.args, err)
		}
	}
	for _, format := range []string{"csv", "jsonl", "parquet"} {
		if _, err := executeLake(&fakeLake{data: []byte(`{"action":"export"}`)}, "export", "raw.orders", "--output", "out", "--format", format); err != nil {
			t.Fatal(err)
		}
	}
}

func TestLakeExportRejectsInvalidArgumentsBeforeRunner(t *testing.T) {
	for _, args := range [][]string{
		{"export"}, {"export", "a", "b", "--output", "out.csv"}, {"export", "raw.t"},
		{"export", "raw.t", "--output", ""}, {"export", "raw.t", "--output", " \t "},
		{"export", "raw.t", "--output", "out.csv", "--format", "xml"},
		{"export", "raw.t", "--output", "out.csv", "--max-rows", "0"},
		{"export", "raw.t", "--output", "out.csv", "--max-rows", "-1"},
		{"export", "raw.t", "--output", "out.csv", "--max-rows", "1.5"},
	} {
		f := &fakeLake{}
		if _, err := executeLake(f, args...); err == nil || f.args != nil {
			t.Fatalf("accepted %v: %v", args, err)
		}
	}
}

func TestLakeExportRejectsInvalidReportsAndRunnerErrors(t *testing.T) {
	for _, data := range []string{"null", "[]", "{}", "noise", `{"action":"ingest"}`, `{"action":1}`, `{"action":"export"} {}`} {
		out, err := executeLake(&fakeLake{data: []byte(data)}, "export", "raw.t", "--output", "out.csv")
		if err == nil || out != "" {
			t.Fatalf("accepted %s: %q %v", data, out, err)
		}
	}
	want := errors.New("offline")
	if _, err := executeLake(&fakeLake{err: want}, "export", "raw.t", "--output", "out.csv"); !errors.Is(err, want) {
		t.Fatalf("runner error lost: %v", err)
	}
	cmd, _, err := NewRootCommand(&bytes.Buffer{}).Find([]string{"lake", "export"})
	if err != nil || cmd.Name() != "export" || cmd.Parent().Name() != "lake" {
		t.Fatalf("missing lake export: %v", err)
	}
}
