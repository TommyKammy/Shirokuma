package cli

import (
	"bytes"
	"errors"
	"path/filepath"
	"strings"
	"testing"
)

func TestMartForwarding(t *testing.T) {
	for _, action := range []string{"save", "list", "get", "check", "refresh", "runs", "inspect"} {
		t.Run(action, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{output: "{}"}}}
			out := &bytes.Buffer{}
			cmd := newRootCommand(out, runner)
			args := []string{"mart", action, "--context-file=-context.json"}
			if action != "list" {
				args = append(args, "--mart-id=mart/id")
			}
			if action == "save" || action == "check" || action == "refresh" {
				args = append(args, "--expected-revision=2", "--idempotency-key=-key")
			}
			if action == "save" {
				args = append(args, "--definition=-definition.json")
			}
			if action == "inspect" {
				args = append(args, "--run-id=run/id")
			}
			if action == "refresh" {
				args = append(args, "--check-changes")
			}
			cmd.SetArgs(args)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			if len(runner.calls) != 1 {
				t.Fatal("expected one invocation")
			}
			call := runner.calls[0]
			root, _ := resolveRepositoryRoot("")
			if call[1] != filepath.Join(root, "tools", "local_experiment", "marts_client.py") || call[2] != action {
				t.Fatal("wrong client")
			}
			joined := strings.Join(call[3:], "\n")
			for _, want := range args[2:] {
				if want == "--check-changes" {
					want = "--check-changes=true"
				}
				if !strings.Contains(joined, want) {
					t.Fatalf("missing %s", want)
				}
			}
			if !strings.Contains(joined, "--api-url=http://127.0.0.1:8080") {
				t.Fatal("wrong default origin")
			}
		})
	}
}

func TestMartRejectsInvalidFlags(t *testing.T) {
	for _, args := range [][]string{
		{"list"}, {"list", "--context-file=c", "x"}, {"list", "--context-file=c", "--check-changes"},
		{"list", "--context-file=c", "--database=db"}, {"get", "--context-file=c", "--mart-id="},
		{"save", "--context-file=c", "--definition=d", "--expected-revision=1", "--idempotency-key=k"},
		{"check", "--context-file=c", "--mart-id=m", "--expected-revision=0", "--idempotency-key=k"},
		{"refresh", "--context-file=c", "--mart-id=m", "--expected-revision=2"},
		{"list", "--database= ", "--schema=marts"},
	} {
		runner := &fakeRunner{}
		cmd := newMartCommand(runner)
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})
		cmd.SetArgs(args)
		if cmd.Execute() == nil || len(runner.calls) != 0 {
			t.Errorf("accepted %v", args)
		}
	}
}

func TestMartFailureWithholdsStdout(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{output: `{"definition":"private"}`, err: errors.New("private credential")}}}
	out := &bytes.Buffer{}
	cmd := newMartCommand(runner)
	cmd.SetOut(out)
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs([]string{"list", "--database=sales", "--schema=marts"})
	err := cmd.Execute()
	if err == nil || strings.Contains(err.Error(), "credential") || out.Len() != 0 {
		t.Fatal("unsafe error output")
	}
}
