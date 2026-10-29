package cli

import (
	"bytes"
	"path/filepath"
	"testing"
)

func TestWarehouseRuntimeHTTPForwarding(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{{output: "{\"operation_id\":\"safe\"}\n"}}}
	cmd := newWarehouseRuntimeCommand(runner)
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetArgs([]string{"autochill", "--minutes=0", "--expected-revision=2", "--key=disable", "--role=admin"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	if len(runner.calls) != 1 || runner.calls[0][1] != filepath.Join(root, "tools", "local_experiment", "warehouse_runtime_client.py") {
		t.Fatal(runner.calls)
	}
}
func TestWarehouseRuntimeRejectsBeforeHTTP(t *testing.T) {
	for _, args := range [][]string{{"wake"}, {"autochill", "--minutes=1441", "--expected-revision=0", "--key=x"}, {"status", "--role=owner"}} {
		runner := &fakeRunner{}
		cmd := newWarehouseRuntimeCommand(runner)
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil || len(runner.calls) != 0 {
			t.Fatal(args, err, runner.calls)
		}
	}
}
