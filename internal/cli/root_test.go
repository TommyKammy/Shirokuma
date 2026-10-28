package cli

import (
	"bytes"
	"reflect"
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

func TestRootCommandGroups(t *testing.T) {
	root := NewRootCommand(nil)
	var names []string
	for _, command := range root.Commands() {
		names = append(names, command.Name())
	}
	if want := []string{"alert", "bear", "database", "doctor", "job", "lake", "mart", "schedule", "stage", "version", "warehouse", "workbench"}; !reflect.DeepEqual(names, want) {
		t.Fatalf("root commands = %v, want %v", names, want)
	}
	warehouse, _, err := root.Find([]string{"warehouse"})
	if err != nil {
		t.Fatal(err)
	}
	names = nil
	for _, command := range warehouse.Commands() {
		names = append(names, command.Name())
		for _, flag := range []string{"session", "request-id"} {
			if command.Flags().Lookup(flag) != nil {
				t.Errorf("warehouse %s still exposes --%s", command.Name(), flag)
			}
		}
	}
	if want := []string{"chill", "endpoint", "query", "status", "wake"}; !reflect.DeepEqual(names, want) {
		t.Fatalf("warehouse commands = %v, want %v", names, want)
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
