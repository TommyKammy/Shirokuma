package cli

import (
	"bytes"
	"errors"
	"path/filepath"
	"reflect"
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

func TestDbtRunExecutorForwarding(t *testing.T) {
	for _, tc := range []struct {
		name  string
		flags []string
		want  []string
	}{
		{"implicit_sql_wap", nil, nil},
		{"explicit_sql_wap", []string{"--executor=sql_wap"}, nil},
		{"native_table", []string{"--executor=native_table", "--node-id=model.analytics.orders"}, []string{"--executor=native_table", "--node-id=model.analytics.orders"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, saved := range []bool{false, true} {
				name := "database"
				contextFlags := []string{"--database=sales", "--schema=stage"}
				wantContext := []string{"--database=sales", "--schema=stage"}
				if saved {
					name = "saved_context"
					contextFlags = []string{"--context-file=-context.json"}
					wantContext = []string{"--context-file=-context.json"}
				}
				t.Run(name, func(t *testing.T) {
					runner := &fakeRunner{responses: []fakeResponse{{output: "{}"}}}
					cmd := newRootCommand(&bytes.Buffer{}, runner)
					args := append([]string{"dbt", "run"}, contextFlags...)
					args = append(args, "--plan-id=plan", "--idempotency-key=original")
					cmd.SetArgs(append(args, tc.flags...))
					if err := cmd.Execute(); err != nil {
						t.Fatal(err)
					}
					if len(runner.calls) != 1 {
						t.Fatalf("runner calls = %d, want 1", len(runner.calls))
					}
					root, err := resolveRepositoryRoot("")
					if err != nil {
						t.Fatal(err)
					}
					want := []string{filepath.Join(root, "tools", "local_experiment", "dbt_client.py"), "run"}
					if !saved {
						want = append(want, wantContext...)
					}
					want = append(want, "--api-url=http://127.0.0.1:8080")
					if saved {
						want = append(want, wantContext...)
					}
					want = append(want, "--idempotency-key=original", "--plan-id=plan")
					want = append(want, tc.want...)
					if got := runner.calls[0][1:]; !reflect.DeepEqual(got, want) {
						t.Fatalf("Python argv = %q, want %q", got, want)
					}
				})
			}
		})
	}
}

func TestDbtRunRejectsInvalidExecutorFlags(t *testing.T) {
	for _, tc := range []struct {
		name  string
		flags []string
		want  string
	}{
		{"native_missing_node", []string{"--executor=native_table"}, "nonempty node-id"},
		{"native_empty_node", []string{"--executor=native_table", "--node-id="}, "nonempty node-id"},
		{"native_blank_node", []string{"--executor=native_table", "--node-id= \t"}, "nonempty node-id"},
		{"default_with_node", []string{"--node-id=model.analytics.orders"}, "node-id requires native_table"},
		{"sql_wap_with_node", []string{"--executor=sql_wap", "--node-id=model.analytics.orders"}, "node-id requires native_table"},
		{"default_empty_node", []string{"--node-id="}, "nonempty node-id"},
		{"sql_wap_empty_node", []string{"--executor=sql_wap", "--node-id="}, "nonempty node-id"},
		{"unknown_executor", []string{"--executor=private-unknown-value"}, "invalid executor"},
		{"empty_executor", []string{"--executor="}, "nonempty executor"},
		{"blank_executor", []string{"--executor= \t"}, "nonempty executor"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			runner := &fakeRunner{}
			out := &bytes.Buffer{}
			cmd := newRootCommand(out, runner)
			cmd.SetArgs(append([]string{"dbt", "run", "--database=sales", "--plan-id=plan"}, tc.flags...))
			err := cmd.Execute()
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("error = %v, want %q", err, tc.want)
			}
			if len(runner.calls) != 0 {
				t.Fatalf("runner calls = %d, want 0", len(runner.calls))
			}
			if out.Len() != 0 || strings.Contains(err.Error(), "private-unknown-value") {
				t.Fatal("unsafe error output")
			}
		})
	}
}

func TestDbtExecutorFlagsRunScope(t *testing.T) {
	for _, action := range []string{"projects", "compile", "plans", "plan", "runs", "status", "cancel", "reconcile", "publish", "publication"} {
		for _, flag := range []string{"executor", "node-id"} {
			if action == "publish" && flag == "node-id" {
				continue // Existing publish node forwarding is covered by TestDbtForwarding.
			}
			t.Run(action+"/"+flag, func(t *testing.T) {
				runner := &fakeRunner{}
				cmd := newRootCommand(&bytes.Buffer{}, runner)
				cmd.SetArgs([]string{"dbt", action, "--database=sales", "--" + flag + "=native_table"})
				err := cmd.Execute()
				if err == nil || !strings.Contains(err.Error(), "unknown flag: --"+flag) {
					t.Fatalf("error = %v, want unknown flag --%s", err, flag)
				}
				if len(runner.calls) != 0 {
					t.Fatalf("runner calls = %d, want 0", len(runner.calls))
				}
			})
		}
	}
}
