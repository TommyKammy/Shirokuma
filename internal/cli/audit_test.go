package cli

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
)

func TestAuditHTTPForwarding(t *testing.T) {
	for _, tc := range []struct {
		action string
		extra  []string
	}{
		{"list", []string{"--database=db", "--role=data_engineer", "--search=-literal", "--limit=3", "--cursor=signed"}},
		{"show", []string{"--scope=platform", "--id=opaque"}},
		{"export", []string{"--database=db", "--id=opaque", "--output=-metadata.json"}},
		{"telemetry", []string{"--role=admin", "--limit=100"}},
	} {
		t.Run(tc.action, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{output: "{\"safe\":true}\n"}}}
			cmd := newAuditCommand(runner)
			var output bytes.Buffer
			cmd.SetOut(&output)
			cmd.SetArgs(append([]string{tc.action}, tc.extra...))
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			root, err := resolveRepositoryRoot("")
			if err != nil {
				t.Fatal(err)
			}
			if len(runner.calls) != 1 {
				t.Fatal(runner.calls)
			}
			call := runner.calls[0]
			if call[0] != databaseTestPython(t, root) || call[1] != filepath.Join(root, "tools", "local_experiment", "audit_client.py") || call[2] != tc.action {
				t.Fatal(call)
			}
			for _, arg := range tc.extra {
				found := false
				for _, actual := range call {
					if actual == arg {
						found = true
					}
				}
				if !found {
					t.Fatalf("missing %q in %#v", arg, call)
				}
			}
			if !strings.Contains(output.String(), "safe") {
				t.Fatal(output.String())
			}
		})
	}
}

func TestAuditValidationBeforeHTTP(t *testing.T) {
	for _, args := range [][]string{
		{"list"}, {"show", "--database=db"}, {"export", "--database=db", "--id=x"},
		{"list", "--database=db", "--limit=0"}, {"telemetry", "--limit=101"},
		{"list", "--database=db", "--role=owner"}, {"list", "--scope=all"},
		{"list", "--scope=platform", "--search= "}, {"list", "--database=db", "unexpected"},
	} {
		runner := &fakeRunner{}
		cmd := newAuditCommand(runner)
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil || len(runner.calls) != 0 {
			t.Fatalf("accepted invalid args %#v", args)
		}
	}
}
