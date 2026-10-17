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
