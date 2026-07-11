package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"
	"time"
)

type fakeResponse struct {
	output string
	err    error
}

type fakeRunner struct {
	responses []fakeResponse
	calls     [][]string
}

func (runner *fakeRunner) Run(_ context.Context, name string, args ...string) ([]byte, error) {
	runner.calls = append(runner.calls, append([]string{name}, args...))
	response := runner.responses[len(runner.calls)-1]
	return []byte(response.output), response.err
}

func TestDoctorJSONHealthy(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{
		{output: "ok\n"},
		{output: `{"items":[{"metadata":{"name":"dev-root"},"status":{"sync":{"status":"Synced"},"health":{"status":"Healthy"}}}]}`},
		{output: "policy ok"},
	}}
	var output bytes.Buffer
	command := newRootCommand(&output, runner)
	command.SetArgs([]string{"doctor", "--output", "json"})
	if err := command.Execute(); err != nil {
		t.Fatalf("Execute() error = %v", err)
	}

	var report doctorReport
	if err := json.Unmarshal(output.Bytes(), &report); err != nil {
		t.Fatalf("doctor output is not JSON: %v", err)
	}
	if report.Status != "healthy" || len(report.Checks) != 3 {
		t.Fatalf("report = %#v", report)
	}
	wantCommands := []string{"kubectl", "kubectl", "make"}
	var gotCommands []string
	for _, call := range runner.calls {
		gotCommands = append(gotCommands, call[0])
	}
	if !reflect.DeepEqual(gotCommands, wantCommands) {
		t.Fatalf("commands = %v, want %v", gotCommands, wantCommands)
	}
}

func TestRunDoctorBoundsFailureOutput(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{
		{err: errors.New("token=secret-value raw prompt contents")},
		{err: errors.New("kubeconfig contents")},
		{err: errors.New("repository output")},
	}}
	report := runDoctor(context.Background(), runner, "local-lite", "test", time.Unix(0, 0).UTC())
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range [][]byte{[]byte("secret-value"), []byte("raw prompt"), []byte("kubeconfig contents")} {
		if bytes.Contains(encoded, forbidden) {
			t.Fatalf("report leaked %q: %s", forbidden, encoded)
		}
	}
	if report.Status != "degraded" {
		t.Fatalf("status = %q, want degraded", report.Status)
	}
}
