package cli

import (
	"bytes"
	"errors"
	"path/filepath"
	"strings"
	"testing"
)

func TestDbtForwarding(t *testing.T) {
	for _, action := range []string{"projects", "compile", "plans", "plan", "run", "runs", "status", "cancel", "reconcile", "publish", "publication"} {
		t.Run(action, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{output: "{}"}}}
			cmd := newRootCommand(&bytes.Buffer{}, runner)
			args := []string{"dbt", action, "--context-file=-context.json"}
			if action == "run" || action == "plan" || action == "runs" {
				args = append(args, "--plan-id=plan")
			}
			if action == "status" || action == "cancel" || action == "reconcile" || action == "publish" {
				args = append(args, "--run-id=run")
			}
			if action == "publish" {
				args = append(args, "--node-id=model.lake.orders")
			}
			if action == "publication" {
				args = append(args, "--publication-id=publication")
			}
			if action == "compile" {
				args = append(args, "--project=other", "--source-schema=raw", "--target-schema=marts", "--staging-materialization=table", "--idempotency-key=original")
			}
			cmd.SetArgs(args)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			if len(runner.calls) != 1 {
				t.Fatal("expected one HTTP client invocation")
			}
			call := runner.calls[0]
			root, _ := resolveRepositoryRoot("")
			if call[1] != filepath.Join(root, "tools", "local_experiment", "dbt_client.py") || call[2] != action {
				t.Fatal("wrong HTTP client")
			}
			joined := strings.Join(call[3:], "\n")
			for _, want := range args[2:] {
				if !strings.Contains(joined, want) {
					t.Fatalf("missing %s", want)
				}
			}
		})
	}
}

func TestDbtRejectsInvalidFlags(t *testing.T) {
	for _, args := range [][]string{
		{"projects"}, {"projects", "--database="}, {"projects", "--context=c", "--database=db"},
		{"projects", "--context=c", "--context-file=f"}, {"compile", "--database=db", "--staging-materialization=view"},
		{"publish", "--database=db", "--run-id=r"}, {"run", "--database=db"},
		{"status", "--database=db", "--run-id=r", "--sql=select 1"}, {"projects", "--database=db", "extra"},
	} {
		runner := &fakeRunner{}
		cmd := newDbtCommand(runner)
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})
		cmd.SetArgs(args)
		if cmd.Execute() == nil || len(runner.calls) != 0 {
			t.Errorf("accepted %v", args)
		}
	}
}

func TestDbtFailureWithholdsPrivateOutput(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{output: "private-path password", err: errors.New("backend-url credential")}}}
	out := &bytes.Buffer{}
	cmd := newDbtCommand(runner)
	cmd.SetOut(out)
	cmd.SetArgs([]string{"projects", "--database=sales"})
	err := cmd.Execute()
	if err == nil || strings.Contains(err.Error(), "credential") || out.Len() != 0 {
		t.Fatal("unsafe error output")
	}
}
