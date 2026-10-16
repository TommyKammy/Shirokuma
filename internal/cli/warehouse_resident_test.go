package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
)

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
	return json.Marshal(map[string]string{"warehouse": args[2], "action": args[0], "result": "verified", "state": "active"})
}

func residentCommand(f *fakeResidentWarehouse, args ...string) (string, error) {
	cmd := newWarehouseQueryCommand(&fakeWarehouseQueryClient{}, f)
	cmd.SilenceErrors, cmd.SilenceUsage = true, true
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

func TestResidentLifecycleCommands(t *testing.T) {
	for _, action := range []string{"status", "chill", "wake"} {
		for _, size := range []string{"", "XS", "S", "LOCAL"} {
			args := []string{action}
			if size != "" {
				args = append(args, size)
			} else {
				size = "XS"
			}
			args = append(args, "--repo-root", "/tmp/repo")
			f := &fakeResidentWarehouse{}
			out, err := residentCommand(f, args...)
			if err != nil || !json.Valid([]byte(out)) || f.root != "/tmp/repo" ||
				!reflect.DeepEqual(f.args, []string{action, "--warehouse", size}) {
				t.Fatalf("resident command: %v %s %+v", err, out, f)
			}
		}
	}
}

func TestResidentGeneralSQL(t *testing.T) {
	for _, args := range [][]string{
		{"query", "S", "SELECT * FROM any_table", "--catalog", "iceberg", "--schema", "public", "--no-auto-wake"},
		{"query", "LOCAL", "--sql", "CREATE TABLE other AS SELECT 42"},
	} {
		f := &fakeResidentWarehouse{}
		if _, err := residentCommand(f, args...); err != nil {
			t.Fatal(err)
		}
		if len(f.args) < 4 || f.args[0] != "query" || f.args[2] != args[1] || !strings.HasPrefix(f.args[3], "--sql=") {
			t.Fatalf("SQL not forwarded: %v", f.args)
		}
		if args[1] == "S" && !reflect.DeepEqual(f.args[3:], []string{"--sql=SELECT * FROM any_table", "--catalog", "iceberg", "--schema", "public", "--no-auto-wake"}) {
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
