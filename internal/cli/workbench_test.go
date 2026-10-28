package cli

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

func TestWorkbenchRunnerInvocation(t *testing.T) {
	for _, tc := range []struct {
		name string
		args []string
		port string
	}{
		{"defaults", nil, "8888"},
		{"custom", []string{"--port", "9012", "--open=false"}, "9012"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{}, {}}}
			var out bytes.Buffer
			command := newWorkbenchCommand(runner)
			command.SetOut(&out)
			command.SetArgs(tc.args)
			if err := command.Execute(); err != nil {
				t.Fatal(err)
			}
			root, err := resolveRepositoryRoot("")
			if err != nil {
				t.Fatal(err)
			}
			python, err := workbenchPython(root)
			if err != nil {
				t.Fatal(err)
			}
			expected := [][]string{{python, filepath.Join(root, "tools", "local_experiment", "workbench_server.py"), "--port", tc.port}}
			if !reflect.DeepEqual(runner.calls, expected) {
				t.Fatalf("calls = %#v, want %#v", runner.calls, expected)
			}
		})
	}
}

type workbenchBrowserRunner struct {
	calls   [][]string
	started chan struct{}
	opened  chan struct{}
}

func (r *workbenchBrowserRunner) Run(ctx context.Context, name string, args ...string) ([]byte, error) {
	r.calls = append(r.calls, append([]string{name}, args...))
	if filepath.Base(name) == "python3" {
		close(r.started)
		select {
		case <-r.opened:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	} else {
		close(r.opened)
	}
	return nil, nil
}

func TestWorkbenchOpensAfterServerStarts(t *testing.T) {
	runner := &workbenchBrowserRunner{started: make(chan struct{}), opened: make(chan struct{})}
	command := workbenchCommand(runner, func(ctx context.Context, address string) error {
		<-runner.started
		if address != "127.0.0.1:9013" {
			t.Errorf("address = %q", address)
		}
		return nil
	})
	command.SetArgs([]string{"--port=9013", "--open"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	opener := "xdg-open"
	if runtime.GOOS == "darwin" {
		opener = "open"
	}
	if len(runner.calls) != 2 || filepath.Base(runner.calls[0][0]) != "python3" || !reflect.DeepEqual(runner.calls[1], []string{opener, "http://127.0.0.1:9013"}) {
		t.Fatalf("calls = %#v", runner.calls)
	}
}

func TestWorkbenchRejectsArguments(t *testing.T) {
	for _, args := range [][]string{{"--port", "0"}, {"--port", "65536"}, {"--port", "text"}, {"unexpected"}} {
		runner := &fakeRunner{}
		cmd := newWorkbenchCommand(runner)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil {
			t.Fatalf("accepted %v", args)
		}
		if len(runner.calls) != 0 {
			t.Fatalf("runner invoked for %v", args)
		}
	}
}

func TestWorkbenchRunnerFailure(t *testing.T) {
	want := errors.New("python failed")
	runner := &fakeRunner{responses: []fakeResponse{{err: want}}}
	cmd := newWorkbenchCommand(runner)
	cmd.SetArgs(nil)
	if err := cmd.Execute(); !errors.Is(err, want) {
		t.Fatalf("error = %v", err)
	}
}

func TestWorkbenchRegistered(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{}}}
	root := newRootCommand(&bytes.Buffer{}, runner)
	root.SetArgs([]string{"workbench", "--port", "9001"})
	if err := root.Execute(); err != nil {
		t.Fatal(err)
	}
	if len(runner.calls) != 1 || filepath.Base(runner.calls[0][0]) != "python3" {
		t.Fatalf("calls = %v", runner.calls)
	}
}

func TestWorkbenchDatabasePythonSelection(t *testing.T) {
	t.Setenv("SHIROKUMA_RUNTIME_PROFILE", "")
	root := t.TempDir()
	if got, err := workbenchPython(root); err != nil || got != "python3" {
		t.Fatalf("legacy: %s %v", got, err)
	}
	t.Setenv("SHIROKUMA_RUNTIME_PROFILE", "/explicit/profile.json")
	if _, err := workbenchPython(root); err == nil {
		t.Fatal("missing database interpreter accepted")
	}
	python := filepath.Join(root, "tools", "local_experiment", "dataops", ".venv", "bin", "python3")
	if err := os.MkdirAll(filepath.Dir(python), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(python, []byte("#!/bin/sh\n"), 0755); err != nil {
		t.Fatal(err)
	}
	if got, err := workbenchPython(root); err != nil || got != python {
		t.Fatalf("explicit: %s %v", got, err)
	}
	t.Setenv("SHIROKUMA_RUNTIME_PROFILE", "")
	profile := filepath.Join(root, "var", "shirokuma", "runtime-profile.json")
	if err := os.MkdirAll(filepath.Dir(profile), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(profile, []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if got, err := workbenchPython(root); err != nil || got != python {
		t.Fatalf("default: %s %v", got, err)
	}
}
