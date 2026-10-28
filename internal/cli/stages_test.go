package cli

import (
	"bytes"
	"errors"
	"path/filepath"
	"strings"
	"testing"
)

func TestStageForwarding(t *testing.T) {
	cases := []struct {
		action  string
		options []string
	}{
		{"list", nil}, {"get", nil}, {"files", nil}, {"runs", nil}, {"exports", nil},
		{"save", []string{"--definition=stage.json", "--expected-revision=2", "--idempotency-key=-key"}},
		{"preview", []string{"--file=a.csv", "--file-version=v", "--expected-revision=2", "--idempotency-key=-key"}},
		{"import", []string{"--file=a.csv", "--file-version=v", "--expected-revision=2", "--idempotency-key=-key", "--expected-preview-fingerprint=confirmation"}},
		{"export", []string{"--file=out.jsonl", "--format=jsonl", "--source=@ref.json", "--expected-revision=2", "--idempotency-key=-key"}},
		{"inspect", []string{"--run-id=-run"}}, {"cancel", []string{"--run-id=-run", "--idempotency-key=-key"}},
		{"receipt", []string{"--export-id=e"}}, {"download", []string{"--export-id=e", "--output=out.csv"}},
		{"scan", []string{"--expected-revision=2", "--idempotency-key=-key"}}, {"scans", nil},
		{"scan-inspect", []string{"--scan-id=scan/id"}},
		{"scan-retry", []string{"--scan-id=scan/id", "--item-id=item/id", "--expected-attempt=3", "--expected-revision=2", "--idempotency-key=-key"}},
	}
	for _, tc := range cases {
		t.Run(tc.action, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{}}}
			cmd := newStageCommand(runner)
			args := []string{tc.action, "--context-file=-original.json"}
			if tc.action != "list" {
				args = append(args, "--stage-id=stage/id")
			}
			args = append(args, tc.options...)
			cmd.SetArgs(args)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			if len(runner.calls) != 1 {
				t.Fatal("expected one HTTP client invocation")
			}
			call := runner.calls[0]
			root, err := resolveRepositoryRoot("")
			if err != nil {
				t.Fatal(err)
			}
			if call[0] != databaseTestPython(t, root) || call[1] != filepath.Join(root, "tools", "local_experiment", "stages_client.py") || call[2] != tc.action {
				t.Fatal("wrong client")
			}
			joined := strings.Join(call[3:], "\n")
			for _, want := range append([]string{"--api-url=http://127.0.0.1:8080", "--context-file=-original.json"}, tc.options...) {
				if !strings.Contains(joined, want) {
					t.Errorf("missing intact argument %q", want)
				}
			}
		})
	}
}

func TestStageLocalValidation(t *testing.T) {
	for _, args := range [][]string{
		{"list"}, {"get", "--context-file=c"}, {"cancel", "--context-file=c", "--stage-id=s", "--run-id=r"},
		{"save", "--context-file=c", "--definition=d", "--expected-revision=1", "--idempotency-key=k"},
		{"preview", "--context-file=c", "--stage-id=s", "--file=../x", "--file-version=v", "--expected-revision=1", "--idempotency-key=k"},
		{"export", "--context-file=c", "--stage-id=s", "--file=out", "--source={}", "--format=parquet", "--expected-revision=1", "--idempotency-key=k"},
		{"preview", "--context-file=c", "--stage-id=s", "--file=x", "--file-version=v", "--expected-revision=0", "--idempotency-key=k"},
		{"list", "--context-file=c", "--definition=d"},
		{"scan", "--context-file=c", "--stage-id=s", "--expected-revision=1"},
		{"scan", "--context-file=c", "--stage-id=s", "--expected-revision=0", "--idempotency-key=k"},
		{"scans", "--context-file=c"},
		{"scan-inspect", "--context-file=c", "--stage-id=s"},
		{"scan-retry", "--context-file=c", "--stage-id=s", "--scan-id=a", "--item-id=i", "--expected-attempt=0", "--expected-revision=1", "--idempotency-key=k"},
		{"scan-retry", "--context-file=c", "--stage-id=s", "--scan-id=a", "--item-id=i", "--expected-revision=1", "--idempotency-key=k"},
		{"scan-retry", "--context-file=c", "--stage-id=s", "--scan-id=a", "--expected-attempt=1", "--expected-revision=1", "--idempotency-key=k"},
	} {
		runner := &fakeRunner{}
		cmd := newStageCommand(runner)
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})
		cmd.SetArgs(args)
		if cmd.Execute() == nil {
			t.Errorf("accepted %v", args)
		}
		if len(runner.calls) != 0 {
			t.Error("invalid input invoked client")
		}
	}
}

func TestStageRegisteredExplicitOriginAndRecoveryOutput(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{output: "{\"run\":{\"status\":\"unknown\"}}\n", err: errors.New("private-password")}}}
	out := &bytes.Buffer{}
	cmd := newRootCommand(out, runner)
	cmd.SetArgs([]string{"stage", "list", "--database=sales", "--schema=raw", "--api-url=http://127.0.0.1:8888"})
	err := cmd.Execute()
	if err == nil || strings.Contains(err.Error(), "private-password") {
		t.Fatal("unsafe/missing error")
	}
	if !strings.Contains(out.String(), "unknown") {
		t.Fatal("recovery stdout discarded")
	}
	if !strings.Contains(strings.Join(runner.calls[0], " "), "--api-url=http://127.0.0.1:8888") {
		t.Fatal("explicit origin discarded")
	}
}
