package cli

import (
	"bytes"
	"path/filepath"
	"reflect"
	"testing"
)

func TestDatabaseDisplayHTTPArguments(t *testing.T) {
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		action           string
		flags, forwarded []string
	}{
		{"list", nil, nil},
		{"operation", []string{"--operation-id", "op"}, []string{"--operation-id", "op"}},
		{"set", []string{"--database-id", "db", "--display-name", " 研究 ", "--description", "", "--expected-revision", "2", "--idempotency-key", "key"},
			[]string{"--database-id", "db", "--display-name= 研究 ", "--idempotency-key=key", "--expected-revision", "2", "--description="}},
		{"schema", []string{"--schema-id", "s", "--display-name", "Raw", "--expected-revision", "1", "--idempotency-key", "key"},
			[]string{"--schema-id", "s", "--display-name=Raw", "--idempotency-key=key", "--expected-revision", "1"}},
		{"set", []string{"--database-id", "db", "--display-name=--研究", "--description=- details", "--expected-revision", "3", "--idempotency-key=-key"},
			[]string{"--database-id", "db", "--display-name=--研究", "--idempotency-key=-key", "--expected-revision", "3", "--description=- details"}},
	} {
		runner := &fakeRunner{responses: []fakeResponse{{}}}
		command := newDatabaseCommand(runner)
		command.SetArgs(append([]string{"display", test.action}, test.flags...))
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		want := append([]string{"python3", filepath.Join(root, "tools", "local_experiment", "display_client.py"), test.action, "--api-url", "http://127.0.0.1:8888"}, test.forwarded...)
		if !reflect.DeepEqual(runner.calls, [][]string{want}) {
			t.Fatalf("calls = %#v, want %#v", runner.calls, want)
		}
	}
}

func TestDatabaseDisplayValidationBeforeExecution(t *testing.T) {
	for _, args := range [][]string{
		{"display", "--help"}, {"display", "set"}, {"display", "schema"}, {"display", "operation"},
		{"display", "schema", "--schema-id", "s", "--display-name", "Raw", "--expected-revision", "0", "--idempotency-key", "key"},
		{"display", "set", "--database-id", "db", "--display-name", "DB", "--expected-revision", "1", "--idempotency-key", "key"},
		{"display", "list", "--config", "private"},
	} {
		runner := &fakeRunner{}
		command := newDatabaseCommand(runner)
		command.SetOut(&bytes.Buffer{})
		command.SetErr(&bytes.Buffer{})
		command.SetArgs(args)
		err := command.Execute()
		if args[len(args)-1] != "--help" && err == nil {
			t.Fatalf("accepted %v", args)
		}
		if len(runner.calls) != 0 {
			t.Fatalf("executed %v", args)
		}
	}
}
