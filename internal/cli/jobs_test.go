package cli

import (
	"bytes"
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestJobForwarding(t *testing.T) {
	for _, action := range []string{"list", "save", "show", "run", "runs", "inspect", "cancel", "reconcile"} {
		t.Run(action, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{}}}
			cmd := newJobCommand(runner)
			args := []string{action, "--context-file", "original.json"}
			forward := []string{action, "--api-url=http://127.0.0.1:8888", "--context-file=original.json"}
			if action == "save" || action == "show" || action == "run" || action == "runs" {
				args = append(args, "--job-id", "job/id")
				forward = append(forward, "--job-id=job/id")
			}
			if action == "inspect" || action == "cancel" || action == "reconcile" {
				args = append(args, "--run-id", "run/id")
				forward = append(forward, "--run-id=run/id")
			}
			if action == "save" || action == "run" {
				args = append(args, "--idempotency-key", "fixed")
				forward = append(forward, "--idempotency-key=fixed")
			}
			if action == "save" {
				args = append(args, "--definition", "job.json")
				forward = append(forward, "--definition=job.json")
			}
			if action == "run" {
				args = append(args, "--arguments", "@args.json")
				forward = append(forward, "--arguments=@args.json")
			}
			if action == "save" || action == "run" {
				args = append(args, "--expected-revision", "3")
				forward = append(forward, "--expected-revision", "3")
			}
			if action == "run" {
				args = append(args, "--input", "a=a.csv", "--input", "b=b.csv")
				forward = append(forward, "--input=a=a.csv", "--input=b=b.csv")
			}
			cmd.SetArgs(args)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			root, err := resolveRepositoryRoot("")
			if err != nil {
				t.Fatal(err)
			}
			want := append([]string{databaseTestPython(t, root), filepath.Join(root, "tools", "local_experiment", "jobs_client.py")}, forward...)
			if len(runner.calls) != 1 || !reflect.DeepEqual(runner.calls[0], want) {
				t.Fatalf("calls = %#v", runner.calls)
			}
		})
	}
}

func TestJobLocalValidation(t *testing.T) {
	for _, args := range [][]string{
		{"list"}, {"show", "--context-file", "c"}, {"cancel", "--context-file", "c"},
		{"save", "--context-file", "c", "--definition", "j", "--expected-revision", "2", "--idempotency-key", "k"},
		{"run", "--context-file", "c", "--job-id", "j", "--expected-revision", "0", "--idempotency-key", "k", "--arguments", "{}"},
		{"run", "--context-file", "c", "--job-id", "j", "--expected-revision", "1", "--idempotency-key", "k"},
		{"list", "--context-file", "c", "--definition", "j"},
		{"run", "--context-file", "c", "--job-id", "j", "--expected-revision", "1", "--idempotency-key", "k", "--arguments", "{}", "--input", "bad"},
	} {
		runner := &fakeRunner{}
		cmd := newJobCommand(runner)
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil {
			t.Fatalf("accepted %v", args)
		}
		if len(runner.calls) != 0 {
			t.Fatalf("executed %v", args)
		}
	}
}

func TestJobRegisteredAndErrorsBounded(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{err: errors.New("private-password")}}}
	cmd := newRootCommand(&bytes.Buffer{}, runner)
	cmd.SetArgs([]string{"job", "list", "--database", "sales", "--schema", "raw", "--api-url", "http://127.0.0.1:9999"})
	err := cmd.Execute()
	if err == nil || strings.Contains(err.Error(), "private-password") {
		t.Fatalf("error = %v", err)
	}
	if len(runner.calls) != 1 || !strings.Contains(strings.Join(runner.calls[0], " "), "--database=sales --schema=raw") {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestJobForwardingHyphenValues(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	cmd := newJobCommand(runner)
	cmd.SetArgs([]string{"run", "--context-file=-context.json", "--job-id=-job", "--expected-revision=1", "--idempotency-key=-foo", "--arguments={}", "--input=-source=-file.csv"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"--context-file=-context.json", "--job-id=-job", "--idempotency-key=-foo", "--arguments={}", "--input=-source=-file.csv"} {
		found := false
		for _, arg := range runner.calls[0] {
			found = found || arg == want
		}
		if !found {
			t.Fatalf("missing intact argument %q in %v", want, runner.calls[0])
		}
	}
}
