package cli

import (
	"bytes"
	"strings"
	"testing"
)

func execute(t *testing.T, args ...string) string {
	t.Helper()

	var output bytes.Buffer
	command := NewRootCommand(&output)
	command.SetArgs(args)
	if err := command.Execute(); err != nil {
		t.Fatalf("Execute() error = %v", err)
	}
	return output.String()
}

func TestHelp(t *testing.T) {
	output := execute(t, "--help")
	for _, expected := range []string{"Operate the Shirokuma data cloud lab", "version"} {
		if !strings.Contains(output, expected) {
			t.Errorf("help output %q does not contain %q", output, expected)
		}
	}
}

func TestVersionCommand(t *testing.T) {
	if got, want := execute(t, "version"), "shirokuma dev\n"; got != want {
		t.Errorf("version output = %q, want %q", got, want)
	}
}

func TestVersionFlag(t *testing.T) {
	if got, want := execute(t, "--version"), "shirokuma version dev\n"; got != want {
		t.Errorf("version flag output = %q, want %q", got, want)
	}
}
