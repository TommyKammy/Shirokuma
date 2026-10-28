package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const testWarehouseSession = "11111111111111111111111111111111"
const testWarehouseRequest = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

type fakeResidentWarehouse struct {
	args []string
	root string
	data []byte
	err  error
}

func (f *fakeResidentWarehouse) Run(_ context.Context, root string, args []string) ([]byte, error) {
	f.root, f.args = root, args
	if f.data != nil || f.err != nil {
		return f.data, f.err
	}
	return json.Marshal(map[string]string{"warehouse": args[2], "backend": "trino-local", "action": args[0], "result": "verified", "state": "active"})
}

func residentCommand(f *fakeResidentWarehouse, args ...string) (string, error) {
	cmd := newWarehouseQueryCommand(f)
	cmd.SilenceErrors, cmd.SilenceUsage = true, true
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

func TestExecResidentWarehousePythonDispatch(t *testing.T) {
	for _, tc := range []struct {
		name     string
		explicit bool
		marker   string
		missing  bool
		unusable bool
	}{
		{name: "explicit profile", explicit: true},
		{name: "default profile", marker: "runtime-profile.json"},
		{name: "active marker", marker: "runtime-profile.active"},
		{name: "missing configured interpreter", explicit: true, missing: true},
		{name: "unusable configured interpreter", explicit: true, unusable: true},
		{name: "legacy unconfigured"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			write := func(path, content string, mode os.FileMode) {
				t.Helper()
				if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte(content), mode); err != nil {
					t.Fatal(err)
				}
			}
			write(filepath.Join(root, "Makefile"), "", 0644)
			write(filepath.Join(root, "security", "resident-images.json"), "{}", 0644)
			t.Setenv("SHIROKUMA_RUNTIME_PROFILE", "")
			if tc.explicit {
				t.Setenv("SHIROKUMA_RUNTIME_PROFILE", filepath.Join(root, "explicit-profile.json"))
			}
			if tc.marker != "" {
				write(filepath.Join(root, "var", "shirokuma", tc.marker), "{}", 0600)
			}
			// Both interpreters report dispatch without executing Python or starting an endpoint.
			stub := "#!/bin/sh\nprintf '%s\\n' \"$0\" \"$PWD\" \"$@\"\n"
			fallback := filepath.Join(root, "bin", "python3")
			write(fallback, stub, 0755)
			t.Setenv("PATH", filepath.Dir(fallback))
			python := filepath.Join(root, "tools", "local_experiment", "dataops", ".venv", "bin", "python3")
			if !tc.missing {
				mode := os.FileMode(0755)
				if tc.unusable {
					mode = 0644
				}
				write(python, stub, mode)
			}
			args := []string{"endpoint", "--warehouse", "LOCAL", "--port", "18080", "--bind", "127.0.0.1"}
			data, err := (execResidentWarehouse{}).Run(context.Background(), root, args)
			if tc.missing || tc.unusable {
				if err == nil || data != nil || !strings.Contains(err.Error(), "requires the repository DataOps Python environment") {
					t.Fatalf("configured interpreter failure did not fail closed: %q %v", data, err)
				}
				return
			}
			if !tc.explicit && tc.marker == "" {
				python = fallback
			}
			want := append([]string{python, root, filepath.Join(root, "tools", "local_experiment", "warehouse_resident.py")}, args...)
			if err != nil || string(data) != strings.Join(want, "\n")+"\n" {
				t.Fatalf("native dispatch: got %q, %v; want %q", data, err, want)
			}
		})
	}
}

func TestResidentLifecycleCommands(t *testing.T) {
	for _, action := range []string{"status", "chill", "wake"} {
		for _, size := range []string{"", "XS", "S", "LOCAL"} {
			args := []string{action}
			if size != "" {
				args = append(args, size)
			} else {
				size = "LOCAL"
			}
			args = append(args, "--repo-root", "/tmp/repo")
			f := &fakeResidentWarehouse{}
			out, err := residentCommand(f, args...)
			if err != nil || !json.Valid([]byte(out)) || !strings.Contains(out, `"backend":"trino-local"`) || f.root != "/tmp/repo" ||
				!reflect.DeepEqual(f.args, []string{action, "--warehouse", size}) {
				t.Fatalf("resident command: %v %s %+v", err, out, f)
			}
		}
	}
}

func TestWarehouseEndpointCommand(t *testing.T) {
	for _, options := range [][]string{nil, {"--warehouse", "S", "--port", "8090", "--bind", "localhost", "--repo-root", "/tmp/repo"}} {
		f := &fakeResidentWarehouse{}
		if _, err := residentCommand(f, append([]string{"endpoint"}, options...)...); err != nil {
			t.Fatal(err)
		}
		want := []string{"endpoint", "--warehouse", "LOCAL", "--port", "8080", "--bind", "127.0.0.1"}
		if len(options) > 0 {
			want = []string{"endpoint", "--warehouse", "S", "--port", "8090", "--bind", "localhost"}
			if f.root != "/tmp/repo" {
				t.Fatalf("repository root not forwarded: %q", f.root)
			}
		}
		if !reflect.DeepEqual(f.args, want) {
			t.Fatalf("endpoint arguments: got %v want %v", f.args, want)
		}
	}
	for _, options := range [][]string{{"--warehouse", "XL"}, {"--port", "0"}, {"--port", "65536"}, {"extra"}} {
		f := &fakeResidentWarehouse{}
		if _, err := residentCommand(f, append([]string{"endpoint"}, options...)...); err == nil || f.args != nil {
			t.Fatalf("invalid endpoint input reached runner: %v", options)
		}
	}
}

func TestResidentGeneralSQL(t *testing.T) {
	for _, args := range [][]string{
		{"query", "S", "SELECT * FROM any_table", "--catalog", "iceberg", "--schema", "public", "--no-auto-wake"},
		{"query", "LOCAL", "--sql", "CREATE TABLE other AS SELECT 42", "--max-rows", "7"},
	} {
		f := &fakeResidentWarehouse{}
		if _, err := residentCommand(f, args...); err != nil {
			t.Fatal(err)
		}
		if len(f.args) < 4 || f.args[0] != "query" || f.args[2] != args[1] || !strings.HasPrefix(f.args[3], "--sql=") {
			t.Fatalf("SQL not forwarded: %v", f.args)
		}
		if args[1] == "LOCAL" && !reflect.DeepEqual(f.args[4:], []string{"--max-rows", "7"}) {
			t.Fatal(f.args)
		}
		if args[1] == "S" && !reflect.DeepEqual(f.args[3:], []string{"--sql=SELECT * FROM any_table", "--max-rows", "1000", "--catalog", "iceberg", "--schema", "public", "--no-auto-wake"}) {
			t.Fatal(f.args)
		}
	}
}

func TestResidentInvalidArgumentsNeverRun(t *testing.T) {
	for _, args := range [][]string{
		{"status", "XL"}, {"wake", "S", "extra"}, {"chill", "--profile", "production"},
		{"query", "XS"}, {"query", "XS", " "}, {"query", "xs", "SELECT 1"},
		{"query", "XS", "SELECT 1", "--sql", "SELECT 2"},
		{"query", "XS", "SELECT 1", "--sql", ""},
		{"query", "XS", "--sql", "SELECT 1", "--request-id", testWarehouseRequest},
		{"query", "XS", "--sql", "SELECT 1", "--profile", "production"},
		{"query", "XS", "--session", testWarehouseSession, "--catalog", "iceberg"},
		{"query", "XS", "--session", testWarehouseSession, "--schema", "public"},
		{"query", "LOCAL", "SELECT 1", "--max-rows", "0"},
		{"query", "LOCAL", "SELECT 1", "--max-rows", "-1"},
		{"query", "XS", "--session", testWarehouseSession, "--max-rows", "1000"},
	} {
		f := &fakeResidentWarehouse{}
		if _, err := residentCommand(f, args...); err == nil || f.args != nil {
			t.Fatalf("invalid input reached runner: %v", args)
		}
	}
}

func TestResidentRunnerFailureAndInvalidJSON(t *testing.T) {
	for _, f := range []*fakeResidentWarehouse{
		{err: errors.New("runner failed")}, {data: []byte(`null`)}, {data: []byte(`{} {}`)},
		{data: []byte(`{"warehouse":"S","state":"active"}`)},
		{data: []byte(`{"warehouse":"XS","state":"unknown"}`)},
	} {
		out, err := residentCommand(f, "status")
		if err == nil || out != "" {
			t.Fatalf("invalid result accepted: %q %v", out, err)
		}
	}
}

func TestResidentSQLModeForwarding(t *testing.T) {
	for _, mode := range []string{"read-only", "sandbox-write", "admin-write"} {
		f := &fakeResidentWarehouse{}
		if _, err := residentCommand(f, "query", "LOCAL", "SELECT 1", "--mode", mode); err != nil {
			t.Fatal(err)
		}
		want := []string{"query", "--warehouse", "LOCAL", "--sql=SELECT 1", "--max-rows", "1000", "--mode", mode}
		if !reflect.DeepEqual(f.args, want) {
			t.Fatalf("mode not forwarded: %v", f.args)
		}
	}
}

func TestResidentRejectsInvalidSQLModes(t *testing.T) {
	for _, args := range [][]string{
		{"query", "LOCAL", "SELECT 1", "--mode", "unsafe"},
		{"query", "LOCAL", "SELECT 1", "--mode", "ADMIN-WRITE"},
		{"query", "XS", "--session", testWarehouseSession, "--mode", "read-only"},
		{"query", "XS", "--session", testWarehouseSession, "--mode", "sandbox-write"},
		{"query", "XS", "--session", testWarehouseSession, "--mode", "admin-write"},
	} {
		f := &fakeResidentWarehouse{}
		if _, err := residentCommand(f, args...); err == nil || f.args != nil {
			t.Fatalf("invalid mode reached runner: %v", args)
		}
	}
}
