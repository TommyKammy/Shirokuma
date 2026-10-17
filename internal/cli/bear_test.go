package cli

import (
	"bytes"
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
)

type fakeBear struct {
	args []string
	root string
	data []byte
	err  error
}

func (f *fakeBear) RunBear(_ context.Context, root string, args []string) ([]byte, error) {
	f.root, f.args = root, args
	return f.data, f.err
}

func executeBear(f *fakeBear, args ...string) (string, error) {
	cmd := newBearCommand(f)
	cmd.SilenceErrors, cmd.SilenceUsage = true, true
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

func TestBearForwardsAllResidentOperations(t *testing.T) {
	for _, tc := range []struct {
		args []string
		bear string
	}{
		{[]string{"sre", "inspect"}, "sre"},
		{[]string{"query", "latest"}, "query_doctor"},
		{[]string{"query", "explain", "--sql", "SELECT * FROM arbitrary"}, "query_doctor"},
		{[]string{"data", "aggregate any customer data"}, "data_engineer"},
		{[]string{"catalog", "lineage", "custom.orders"}, "catalog_curator"},
		{[]string{"policy", "check", "/tmp/custom.yaml"}, "policy_gatekeeper"},
	} {
		f := &fakeBear{data: []byte(`{"bear":"` + tc.bear + `","telemetry":"recorded"}`)}
		out, err := executeBear(f, append(tc.args, "--repo-root", "/tmp/repo")...)
		if err != nil || !strings.Contains(out, tc.bear) || f.root != "/tmp/repo" || !reflect.DeepEqual(f.args, tc.args) {
			t.Fatalf("forward %v: %s %v %+v", tc.args, out, err, f)
		}
	}
}

func TestBearRejectsInvalidResponses(t *testing.T) {
	for _, data := range []string{"null", "[]", "{}", "noise", `{"bear":"sre"} {}`, `{"bear":"query_doctor"}`} {
		out, err := executeBear(&fakeBear{data: []byte(data)}, "sre")
		if err == nil || out != "" {
			t.Fatalf("accepted %s: %q %v", data, out, err)
		}
	}
	if _, err := executeBear(&fakeBear{err: errors.New("offline")}, "sre"); err == nil {
		t.Fatal("lost runner error")
	}
}

func TestRootContainsBearGroup(t *testing.T) {
	root := NewRootCommand(&bytes.Buffer{})
	cmd, _, err := root.Find([]string{"bear", "query"})
	if err != nil || cmd.Name() != "query" || cmd.Parent().Name() != "bear" {
		t.Fatalf("missing bear group: %v", err)
	}
}

func TestDataBearWAPFlags(t *testing.T) {
	for _, args := range [][]string{
		{"data", "--sql", "SELECT 42", "--stage-table", "custom"},
		{"data", "--publish", "iceberg.sandbox.custom"},
		{"data", "--rollback", "marts.custom", "--snapshot-id", "123"},
		{"data", "intent", "--snapshot-id", "456", "--rollback", "t", "--publish", "t", "--stage-table", "t", "--sql", "SELECT 1"},
		{"data", "--sql", ""},
	} {
		f := &fakeBear{data: []byte(`{"bear":"data_engineer"}`)}
		if _, err := executeBear(f, args...); err != nil {
			t.Fatal(err)
		}
		want := args
		if len(args) > 2 && args[1] == "intent" {
			want = []string{"data", "intent", "--sql", "SELECT 1", "--stage-table", "t", "--publish", "t", "--rollback", "t", "--snapshot-id", "456"}
		}
		if !reflect.DeepEqual(f.args, want) {
			t.Fatalf("WAP flags: got %v want %v", f.args, want)
		}
	}
}
