package cli

import (
	"bytes"
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestDatabaseCreationInvocation(t *testing.T) {
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	for _, action := range []string{"create", "principals", "list", "show", "plan", "approve", "replan", "reconcile", "withdraw", "resume"} {
		args := []string{"creation", action}
		want := []string{"python3", filepath.Join(root, "tools", "local_experiment", "database_creation_client.py"), action}
		if action == "create" {
			args = append(args, "--sql-catalog", "sales", "--grant", "alice:admin", "--grant", "bob:bi_viewer")
			want = append(want, "--sql-catalog", "sales", "--grant", "alice:admin", "--grant", "bob:bi_viewer")
		} else if action != "principals" && action != "list" {
			args = append(args, "--database", "sales")
			want = append(want, "--database", "sales")
		}
		if action == "plan" || action == "approve" || action == "replan" {
			want = append(want, "--phase", "bootstrap")
		}
		runner := &fakeRunner{responses: []fakeResponse{{output: `{"ok":true,"data":{"progress_code":"READY"},"message":"READY"}`}}}
		cmd := newDatabaseCommand(runner)
		var out bytes.Buffer
		cmd.SetOut(&out)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(runner.calls, [][]string{want}) {
			t.Fatalf("%s: %#v != %#v", action, runner.calls, want)
		}
		if out.String() != "READY\n" {
			t.Fatal(out.String())
		}
	}
}

func TestDatabaseCreationValidationNeverExecutes(t *testing.T) {
	for _, args := range [][]string{
		{"creation", "--help"}, {"creation", "approve", "--help"},
		{"creation", "create"}, {"creation", "approve"},
		{"creation", "show", "--database", "../private"},
		{"creation", "plan", "--database", "sales", "--phase", "other"},
		{"creation", "approve", "--database", "sales", "--recovery"},
		{"creation", "create", "--sql-catalog", "sales", "--grant", "alice:superuser"},
		{"creation", "create", "--sql-catalog", "sales", "--operation-id", "x"},
		{"creation", "show", "--database", "sales", "--release"},
		{"creation", "resume"},
		{"creation", "resume", "--database", "sales", "--phase", "full"},
		{"creation", "resume", "--database", "sales", "--recovery"},
		{"creation", "resume", "--database", "sales", "--expected-revision", "2"},
	} {
		runner := &fakeRunner{}
		cmd := newDatabaseCommand(runner)
		cmd.SetArgs(args)
		var out bytes.Buffer
		cmd.SetOut(&out)
		cmd.SetErr(&out)
		err := cmd.Execute()
		if !strings.Contains(strings.Join(args, " "), "--help") && err == nil {
			t.Fatalf("accepted %v", args)
		}
		if len(runner.calls) != 0 {
			t.Fatalf("executed %v", args)
		}
	}
}

func TestDatabaseCreationEnvelopeAndSafeFailure(t *testing.T) {
	for _, test := range []struct {
		reply     string
		failure   error
		json, bad bool
		want      string
	}{
		{reply: `{"ok":true,"data":{"progress_code":"READY"},"message":"READY"}`, json: true, want: `"progress_code":"READY"`},
		{reply: `{"ok":false,"error":"STALE_REVIEW"}`, bad: true, want: "STALE_REVIEW"},
		{reply: `{"ok":false,"error":"ADMIN_REQUIRED"}`, json: true, bad: true, want: `"error":"ADMIN_REQUIRED"`},
		{reply: `raw private password`, bad: true},
		{failure: errors.New("private password"), bad: true},
	} {
		runner := &fakeRunner{responses: []fakeResponse{{output: test.reply, err: test.failure}}}
		cmd := newDatabaseCommand(runner)
		args := []string{"creation", "show", "--database", "sales"}
		if test.json {
			args = append(args, "--json")
		}
		var out bytes.Buffer
		cmd.SetOut(&out)
		cmd.SetErr(&out)
		cmd.SetArgs(args)
		err := cmd.Execute()
		if (err != nil) != test.bad {
			t.Fatalf("%v", err)
		}
		if !strings.Contains(out.String(), test.want) {
			t.Fatal(out.String())
		}
		if strings.Contains(out.String(), "password") || err != nil && strings.Contains(err.Error(), "password") {
			t.Fatal("unsafe output")
		}
	}
}

func TestDatabaseCreationRecoveryAndProgress(t *testing.T) {
	for _, args := range [][]string{
		{"creation", "approve", "--database", "sales", "--phase", "full", "--recovery"},
		{"creation", "progress", "--database", "sales"},
	} {
		runner := &fakeRunner{responses: []fakeResponse{{output: `{"ok":true,"message":"accepted"}`}}}
		cmd := newDatabaseCommand(runner)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatal(err)
		}
		call := strings.Join(runner.calls[0], " ")
		if args[1] == "approve" && !strings.Contains(call, "--phase full --recovery") {
			t.Fatal(call)
		}
		if args[1] == "progress" && !strings.Contains(call, " show ") {
			t.Fatal(call)
		}
	}
}

func TestDatabaseCreationRetryInvocation(t *testing.T) {
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	for _, action := range []string{"create", "plan", "approve", "replan", "reconcile", "withdraw", "resume"} {
		for _, recovery := range []bool{false, true} {
			if recovery && action != "approve" {
				continue
			}
			args := []string{"creation", "retry", "--database", "sales", "--action", action}
			want := []string{"python3", filepath.Join(root, "tools", "local_experiment", "database_creation_client.py"), "retry", "--database", "sales"}
			if action == "plan" || action == "approve" || action == "replan" {
				args = append(args, "--phase", "full")
				want = append(want, "--phase", "full")
			}
			want = append(want, "--action", action)
			if recovery {
				args = append(args, "--recovery")
				want = append(want, "--recovery")
			}
			runner := &fakeRunner{responses: []fakeResponse{{output: `{"ok":true,"data":{"acknowledgment":{"accepted_revision":3},"current":{"progress_code":"STOPPED"}},"message":"Historical acknowledgment recovered; current progress: STOPPED"}`}}}
			cmd := newDatabaseCommand(runner)
			cmd.SetArgs(args)
			var out bytes.Buffer
			cmd.SetOut(&out)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(runner.calls, [][]string{want}) {
				t.Fatalf("%#v != %#v", runner.calls, want)
			}
			if !strings.Contains(out.String(), "Historical acknowledgment") || !strings.Contains(out.String(), "STOPPED") {
				t.Fatal(out.String())
			}
		}
	}
}

func TestDatabaseCreationRetryInvalidFlagsNeverExecute(t *testing.T) {
	for _, extra := range [][]string{
		{}, {"--action", "release"}, {"--action", "approve"}, {"--action", "plan"},
		{"--action", "create", "--phase", "full"},
		{"--action", "resume", "--phase", "full"},
		{"--action", "resume", "--recovery"},
		{"--action", "replan", "--phase", "full", "--recovery"},
		{"--action", "approve", "--phase", "bootstrap", "--recovery"},
		{"--action", "approve", "--phase", "full", "--idempotency-key", "key"},
	} {
		runner := &fakeRunner{}
		cmd := newDatabaseCommand(runner)
		cmd.SetArgs(append([]string{"creation", "retry", "--database", "sales"}, extra...))
		var out bytes.Buffer
		cmd.SetOut(&out)
		cmd.SetErr(&out)
		if cmd.Execute() == nil || len(runner.calls) != 0 {
			t.Fatalf("accepted/executed %v", extra)
		}
	}
	for _, machine := range []bool{false, true} {
		runner := &fakeRunner{responses: []fakeResponse{{output: `{"ok":false,"error":"NO_FROZEN_INTENT"}`}}}
		cmd := newDatabaseCommand(runner)
		args := []string{"creation", "retry", "--database", "sales", "--action", "create"}
		if machine {
			args = append(args, "--json")
		}
		cmd.SetArgs(args)
		var out bytes.Buffer
		cmd.SetOut(&out)
		cmd.SetErr(&out)
		if cmd.Execute() == nil || !strings.Contains(out.String(), "NO_FROZEN_INTENT") {
			t.Fatal(out.String())
		}
	}
}
