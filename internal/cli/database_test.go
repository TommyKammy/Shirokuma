package cli

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func databaseTestPython(t *testing.T, root string) string {
	t.Helper()
	python, err := workbenchPython(root)
	if err != nil {
		t.Fatal(err)
	}
	return python
}

func TestDatabaseRetirementInvocation(t *testing.T) {
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	for _, action := range []string{"retire-preview", "retire", "retire-status", "retire-reconcile"} {
		idFlag := "--database-id"
		if action == "retire-status" || action == "retire-reconcile" {
			idFlag = "--operation-id"
		}
		for _, base := range []string{"http://127.0.0.1:8888", "http://127.0.0.1:8080"} {
			runner := &fakeRunner{responses: []fakeResponse{{}}}
			args := []string{action, idFlag, "id"}
			if base != "http://127.0.0.1:8888" {
				args = append(args, "--base-url", base)
			}
			want := []string{"python3", filepath.Join(root, "tools", "local_experiment", "database_retirement_client.py"), action, idFlag, "id", "--base-url", base}
			if action == "retire" || action == "retire-reconcile" {
				args = append(args, "--expected-revision", "3", "--idempotency-key", "frozen")
				want = append(want, "--expected-revision", "3", "--idempotency-key", "frozen")
			}
			command := newDatabaseCommand(runner)
			command.SetArgs(args)
			if err := command.Execute(); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(runner.calls, [][]string{want}) {
				t.Fatalf("calls = %#v", runner.calls)
			}
		}
	}
}

func TestDatabaseRetirementValidationAndHelpDoNotExecute(t *testing.T) {
	for _, args := range [][]string{
		{"retire-preview", "--help"}, {"retire", "--help"}, {"retire-status", "--help"}, {"retire-reconcile", "--help"},
		{"retire-preview"}, {"retire-status"}, {"retire", "--database-id", "db"},
		{"retire", "--database-id", "db", "--expected-revision", "0", "--idempotency-key", "key"},
		{"retire-reconcile", "--operation-id", "op", "--expected-revision", "1"},
		{"retire", "--database-id", "db", "--expected-revision", "1", "--idempotency-key", " "},
		{"retire-preview", "--purge"},
		{"retire-preview", "--database-id", "../db"},
	} {
		runner := &fakeRunner{}
		command := newDatabaseCommand(runner)
		var output bytes.Buffer
		command.SetOut(&output)
		command.SetErr(&output)
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

func TestDatabaseRetirementSafeError(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{err: errors.New("secret credentials")}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"retire-status", "--operation-id", "op"})
	err := command.Execute()
	if err == nil || strings.Contains(err.Error(), "secret") {
		t.Fatalf("unsafe error %v", err)
	}
}

func TestDatabaseInvocation(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"apply", "--config", "/explicit/operator.json", "--operation-id", "op", "--expected-revision", "3"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	want := [][]string{{databaseTestPython(t, root), filepath.Join(root, "tools", "local_experiment", "catalog_admin.py"), "apply", "--config", "/explicit/operator.json", "--operation-id", "op", "--expected-revision", "3"}}
	if !reflect.DeepEqual(runner.calls, want) {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestDatabaseReadonlyVerifyFlags(t *testing.T) {
	base := []string{"reconcile", "--config", "/private/config", "--operation-id", "op", "--step", "verify"}
	digest := strings.Repeat("a", 64)
	flags := []string{"--verify-readonly", "--expected-revision", "9", "--delivery-digest", digest, "--receipt-digest", digest}
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	command := newDatabaseCommand(runner)
	command.SetArgs(append(append([]string{}, base...), flags...))
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	call := runner.calls[0]
	if !reflect.DeepEqual(call[len(call)-len(flags):], flags) {
		t.Fatalf("missing explicit forwarding: %v", call)
	}
	for _, extra := range [][]string{
		{"--verify-readonly=false"},
		{"--verify-readonly"}, {"--expected-revision", "9"}, {"--delivery-digest", digest}, {"--receipt-digest", digest},
		{"--verify-readonly", "--expected-revision", "0", "--delivery-digest", digest, "--receipt-digest", digest},
		{"--verify-readonly", "--expected-revision", "9", "--delivery-digest", "bad", "--receipt-digest", digest},
	} {
		runner := &fakeRunner{}
		command := newDatabaseCommand(runner)
		command.SetArgs(append(append([]string{}, base...), extra...))
		if command.Execute() == nil || len(runner.calls) != 0 {
			t.Fatalf("accepted incomplete flags %v", extra)
		}
	}
	runner = &fakeRunner{}
	command = newDatabaseCommand(runner)
	command.SetArgs(append([]string{"reconcile", "--config", "c", "--operation-id", "op", "--step", "apply_secrets"}, flags...))
	if command.Execute() == nil || len(runner.calls) != 0 {
		t.Fatal("accepted non-verify recovery")
	}
}

func TestDatabaseHelpAndValidationDoNotRun(t *testing.T) {
	for _, args := range [][]string{{"--help"}, {"plan", "--help"}, {"apply"}, {"apply", "--config", "c", "--operation-id", "op", "--expected-revision", "0"}, {"reconcile", "--config", "c", "--operation-id", "op", "--step", "shell"}, {"plan", "--is-admin"}} {
		runner := &fakeRunner{}
		command := newDatabaseCommand(runner)
		var output bytes.Buffer
		command.SetOut(&output)
		command.SetErr(&output)
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

func TestDatabaseSafeRunnerError(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{err: errors.New("private-password")}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"inspect-operation", "--auth-headers", "/private/headers", "--operation-id", "op"})
	err := command.Execute()
	if err == nil || strings.Contains(err.Error(), "private-password") {
		t.Fatalf("error = %v", err)
	}
}

func TestDatabaseQueryReconcileThinInvocation(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"reconcile-query", "--query-id", "q", "--auth-headers", "/explicit/headers"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	want := [][]string{{databaseTestPython(t, root), filepath.Join(root, "tools", "local_experiment", "catalog_admin.py"), "reconcile-query", "--query-id", "q", "--auth-headers", "/explicit/headers"}}
	if !reflect.DeepEqual(runner.calls, want) {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestDatabaseInspectAuthOnlyInvocation(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"inspect-operation", "--operation-id", "op", "--auth-headers", "/explicit/headers"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	want := [][]string{{databaseTestPython(t, root), filepath.Join(root, "tools", "local_experiment", "catalog_admin.py"), "inspect-operation", "--operation-id", "op", "--auth-headers", "/explicit/headers"}}
	if !reflect.DeepEqual(runner.calls, want) {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestDatabaseCreateThinAuthenticatedInvocation(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"create", "--sql-catalog", "customer_research", "--idempotency-key", "create-one", "--auth-headers", "/explicit/headers"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	want := [][]string{{databaseTestPython(t, root), filepath.Join(root, "tools", "local_experiment", "catalog_admin.py"), "create", "--idempotency-key", "create-one", "--auth-headers", "/explicit/headers", "--sql-catalog", "customer_research", "--profile-id", "local-polaris-1.8.0"}}
	if !reflect.DeepEqual(runner.calls, want) {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestDatabaseRegisterSpecThinAuthenticatedInvocation(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"register-spec", "--spec", "/public/approved.json", "--idempotency-key", "restore-one", "--auth-headers", "/private/headers"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	want := [][]string{{databaseTestPython(t, root), filepath.Join(root, "tools", "local_experiment", "catalog_admin.py"), "register-spec", "--idempotency-key", "restore-one", "--spec", "/public/approved.json", "--auth-headers", "/private/headers"}}
	if !reflect.DeepEqual(runner.calls, want) {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestDatabaseGrantHTTPInvocationAndValidation(t *testing.T) {
	for _, action := range []string{"set", "revoke", "list", "operation"} {
		runner := &fakeRunner{responses: []fakeResponse{{}}}
		command := newDatabaseCommand(runner)
		args := []string{"grant", action}
		if action == "operation" {
			args = append(args, "--operation-id", "op")
		} else {
			args = append(args, "--database-id", "db")
		}
		if action == "set" || action == "revoke" {
			args = append(args, "--principal-id", "alice", "--expected-revision", "0", "--idempotency-key", "key")
		}
		if action == "set" {
			args = append(args, "--maximum-role", "bi_viewer")
		}
		command.SetArgs(args)
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		if len(runner.calls) != 1 || !strings.HasSuffix(runner.calls[0][1], "grant_client.py") || runner.calls[0][2] != action {
			t.Fatalf("unexpected invocation: %#v", runner.calls)
		}
		if strings.Contains(strings.Join(runner.calls[0], " "), "--config") {
			t.Fatal("ordinary grant command requested private operator configuration")
		}
	}
	for _, flags := range [][]string{
		{"grant", "--help"},
		{"grant", "set", "--database-id", "db", "--principal-id", "alice", "--maximum-role", "bi_viewer", "--idempotency-key", "key"},
		{"grant", "set", "--database-id", "db", "--principal-id", "alice", "--maximum-role", "unknown", "--idempotency-key", "key", "--expected-revision", "0"},
		{"grant", "revoke", "--database-id", "db", "--principal-id", "alice", "--idempotency-key", "key", "--expected-revision", "-1"},
	} {
		runner := &fakeRunner{}
		command := newDatabaseCommand(runner)
		command.SetOut(&bytes.Buffer{})
		command.SetErr(&bytes.Buffer{})
		command.SetArgs(flags)
		err := command.Execute()
		if flags[len(flags)-1] != "--help" && err == nil {
			t.Fatalf("accepted %v", flags)
		}
		if len(runner.calls) != 0 {
			t.Fatalf("executed invalid command %v", flags)
		}
	}
}

func TestDatabaseGrantAdminUsesOrdinaryHTTPClient(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	command := newDatabaseCommand(runner)
	command.SetArgs([]string{"grant", "set", "--database-id", "db", "--principal-id", "db-owner", "--maximum-role", "admin", "--idempotency-key", "key", "--expected-revision", "0"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	if len(runner.calls) != 1 || !strings.HasSuffix(runner.calls[0][1], "grant_client.py") || !strings.Contains(strings.Join(runner.calls[0], " "), "--maximum-role admin") {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestDatabaseOperatorPythonSelection(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"Makefile", "security/resident-images.json", "tools/local_experiment/dataops/.venv/bin/python3"} {
		path := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, nil, 0755); err != nil {
			t.Fatal(err)
		}
	}
	previous, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(previous); err != nil {
			t.Error(err)
		}
	})
	python := filepath.Join(root, "tools", "local_experiment", "dataops", ".venv", "bin", "python3")
	for _, configured := range []bool{false, true} {
		t.Run(fmt.Sprint(configured), func(t *testing.T) {
			profile := ""
			wantPython := "python3"
			if configured {
				profile = filepath.Join(root, "test-profile.json")
				wantPython = python
			}
			t.Setenv("SHIROKUMA_RUNTIME_PROFILE", profile)
			runner := &fakeRunner{responses: []fakeResponse{{}}}
			command := newDatabaseCommand(runner)
			command.SetArgs([]string{"plan", "--config", filepath.Join(root, "operator.json"), "--idempotency-key", "plan-one"})
			if err := command.Execute(); err != nil {
				t.Fatal(err)
			}
			if len(runner.calls) != 1 || runner.calls[0][0] != wantPython {
				t.Fatalf("calls = %#v, want interpreter %q", runner.calls, wantPython)
			}
			grantRunner := &fakeRunner{responses: []fakeResponse{{}}}
			grant := newDatabaseCommand(grantRunner)
			grant.SetArgs([]string{"grant", "list", "--database-id", "db"})
			if err := grant.Execute(); err != nil {
				t.Fatal(err)
			}
			if len(grantRunner.calls) != 1 || grantRunner.calls[0][0] != "python3" {
				t.Fatalf("grant HTTP interpreter changed: %#v", grantRunner.calls)
			}
		})
	}
}
