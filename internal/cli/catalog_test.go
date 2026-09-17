package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func catalogRequest() dataChildRequest {
	return dataChildRequest{Action: "describe", Case: catalogReadCase, Token: testWarehouseSession, Request: []byte(`{"command":"describe","table":"smoke.fixture_v1"}`)}
}

func catalogPublic() map[string]any {
	return map[string]any{
		"schema_version": 1, "table": "iceberg.smoke.fixture_v1",
		"catalog_entity": map[string]any{"id": testWarehouseRequest, "fully_qualified_name": catalogFQN, "version": "1.0", "updated_at_ms": 1000},
		"description":    "retained catalog description",
		"owners":         []any{map[string]any{"id": "11111111-1111-4111-8111-111111111111", "name": "ShirokumaLocalExperiment", "type": "team"}},
		"columns":        []any{map[string]any{"name": "id", "type": "BIGINT", "comment": nil}, map[string]any{"name": "name", "type": "VARCHAR", "comment": "名前"}},
		"lineage":        map[string]any{"scope": "one-hop", "observed": true, "nodes": []any{}, "edges": []any{}},
		"sql_presence":   "not-checked", "observation": map[string]any{"scope": "catalog-metadata-only", "stable": true},
	}
}

func catalogLines(r dataChildRequest, outcome string) [][]byte {
	a, _ := json.Marshal(map[string]any{"schema_version": 1, "event": "allocated", "command": "catalog describe", "evidence_token": r.Token})
	v := map[string]any{"schema_version": 1, "event": "result", "command": "catalog describe", "evidence_token": r.Token,
		"result": outcome, "observation": nil, "error_code": "CATALOG_CHANGED", "cleanup_verified": false,
		"evidence": "tmp/local-experiment/catalog-read/" + r.Token + "/summary.json"}
	if outcome == "passed" {
		v["observation"], v["error_code"], v["cleanup_verified"] = catalogPublic(), nil, true
	}
	if outcome == "interrupted" {
		v["error_code"] = "INTERRUPTED"
	}
	b, _ := json.Marshal(v)
	return [][]byte{a, b}
}

func catalogTestRoot(t *testing.T) string {
	t.Helper()
	root := queryReadTestRoot(t)
	path := filepath.Join(root, catalogReadScript)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func TestCatalogCommandFixedRouteAndNoInstallation(t *testing.T) {
	root := catalogTestRoot(t)
	child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
		if r.Case != catalogReadCase || r.Action != "describe" || r.Root != root {
			t.Fatal("catalog route changed")
		}
		for _, line := range catalogLines(r, "passed") {
			if err := emit(line); err != nil {
				return dataChildResult{Started: true}, err
			}
		}
		return dataChildResult{Started: true}, nil
	})
	cmd := newCatalogCommand(child)
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"describe", "--table", catalogTable, "--repo-root", root})
	if err := cmd.Execute(); err != nil || !strings.Contains(out.String(), `"sql_presence":"not-checked"`) {
		t.Fatalf("%v %s", err, out.String())
	}
}

func TestCatalogInvalidInputNeverStartsChild(t *testing.T) {
	for _, args := range [][]string{{"describe"}, {"describe", "extra"}, {"describe", "--table", "SECRET"}, {"describe", "--table", catalogTable, "--profile", "production"}, {"describe", "--table", catalogTable, "--sql", "SECRET"}} {
		cmd := newCatalogCommand(filterNever(t))
		cmd.SetOut(io.Discard)
		cmd.SetErr(io.Discard)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil || strings.Contains(err.Error(), "SECRET") {
			t.Fatal("bad input was accepted or exposed")
		}
	}
	for _, raw := range []string{`{"command":"describe","command":"describe","table":"smoke.fixture_v1"}`, `{"command":"describe","table":null}`, `{"command":"describe","table":"smoke.fixture_v1","query":"select"}`, `{"command":"describe","table":"iceberg.smoke.fixture_v1"}`, "\xff", strings.Repeat(" ", 1025)} {
		if _, err := decodeCatalogRequest([]byte(raw), "describe"); err == nil {
			t.Fatal("invalid request accepted")
		}
	}
}

func TestCatalogOnlyPublishesAfterMatchingChildExit(t *testing.T) {
	for _, scenario := range []struct {
		name        string
		outcome     string
		exit        int
		unconfirmed bool
		interrupted bool
		badOrder    bool
		wantFinal   bool
	}{
		{"passed", "passed", 0, false, false, false, true},
		{"failed", "failed", 1, false, false, false, true},
		{"interrupt", "interrupted", 130, false, false, false, true},
		{"wrong-exit", "passed", 1, false, false, false, false},
		{"unconfirmed", "passed", 0, true, false, false, false},
		{"missing-allocation", "passed", 0, false, false, true, false},
		{"interrupted-process", "passed", 130, false, true, false, false},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			var out bytes.Buffer
			child := dataChildFunc(func(_ context.Context, r dataChildRequest, emit func([]byte) error) (dataChildResult, error) {
				lines := catalogLines(r, scenario.outcome)
				if scenario.badOrder {
					lines = lines[1:]
				}
				for _, line := range lines {
					if err := emit(line); err != nil {
						return dataChildResult{Started: true}, err
					}
					if strings.Contains(out.String(), `"event":"result"`) {
						t.Fatal("final emitted while child alive")
					}
				}
				var err error
				if scenario.interrupted {
					err = errDataInterrupted
				}
				return dataChildResult{Started: true, ExitCode: scenario.exit, Unconfirmed: scenario.unconfirmed, Interrupted: scenario.interrupted}, err
			})
			err := runCatalogCommand(context.Background(), child, catalogRequest(), &out)
			if strings.Contains(out.String(), `"event":"result"`) != scenario.wantFinal {
				t.Fatalf("%v %s", err, out.String())
			}
			if scenario.name == "passed" && err != nil {
				t.Fatal(err)
			}
			if scenario.name != "passed" && err == nil {
				t.Fatal("missing error")
			}
		})
	}
}

func TestCatalogProjectionRefusesClaimsAndForeignFields(t *testing.T) {
	changes := []func(map[string]any){
		func(v map[string]any) { v["connection"] = "PRIVATE" },
		func(v map[string]any) { v["sql_presence"] = "present" },
		func(v map[string]any) { v["description"] = strings.Repeat("x", 2049) },
		func(v map[string]any) { v["description"] = "bad\x1btext" },
		func(v map[string]any) { v["catalog_entity"].(map[string]any)["version"] = "NaN" },
		func(v map[string]any) {
			v["catalog_entity"].(map[string]any)["updated_at_ms"] = json.Number("9223372036854775808")
		},
		func(v map[string]any) { v["owners"].([]any)[0].(map[string]any)["type"] = "user" },
		func(v map[string]any) { v["columns"].([]any)[0].(map[string]any)["type"] = "INT" },
		func(v map[string]any) { v["observation"].(map[string]any)["stable"] = false },
		func(v map[string]any) { v["lineage"].(map[string]any)["nodes"] = nil },
		func(v map[string]any) { v["lineage"].(map[string]any)["observed"] = false },
	}
	for _, change := range changes {
		value := catalogPublic()
		change(value)
		raw, _ := json.Marshal(value)
		if catalogObservation(raw) {
			t.Fatal("invalid projection accepted")
		}
	}
	// Every unexpected JSON category is rejected without a type assertion panic.
	for key := range catalogPublic() {
		for _, mutation := range []any{nil, false, 0, []any{}, map[string]any{}} {
			if key == "description" && mutation == nil {
				continue
			}
			value := catalogPublic()
			value[key] = mutation
			raw, _ := json.Marshal(value)
			if catalogObservation(raw) {
				t.Fatal("invalid JSON category accepted")
			}
		}
	}
}

func TestCatalogLineageChecksIdentityConnectivityAndScope(t *testing.T) {
	root := testWarehouseRequest
	nodeID := "22222222-2222-4222-8222-222222222222"
	graph := map[string]any{"scope": "one-hop", "observed": true,
		"nodes": []any{map[string]any{"id": nodeID, "fully_qualified_name": "shirokuma_local.iceberg.local_experiment.dataops_" + strings.Repeat("a", 32) + "_stg", "sql_presence": "not-checked"}},
		"edges": []any{map[string]any{"from_entity": root, "to_entity": nodeID, "source": "Manual"}}}
	if !catalogLineage(graph, root, "11111111-1111-4111-8111-111111111111") {
		t.Fatal("valid one hop rejected")
	}
	for _, change := range []func(map[string]any){
		func(g map[string]any) { g["edges"] = []any{} },
		func(g map[string]any) {
			g["nodes"].([]any)[0].(map[string]any)["fully_qualified_name"] = "foreign.table"
		},
		func(g map[string]any) { g["nodes"].([]any)[0].(map[string]any)["sql_presence"] = "present" },
		func(g map[string]any) { g["edges"].([]any)[0].(map[string]any)["source"] = "SQL" },
		func(g map[string]any) { g["edges"].([]any)[0].(map[string]any)["from_entity"] = nodeID },
		func(g map[string]any) { g["edges"] = append(g["edges"].([]any), g["edges"].([]any)[0]) },
	} {
		raw, _ := json.Marshal(graph)
		var copy map[string]any
		_ = json.Unmarshal(raw, &copy)
		change(copy)
		if catalogLineage(copy, root, "11111111-1111-4111-8111-111111111111") {
			t.Fatal("invalid graph accepted")
		}
	}
}

func TestCatalogEnvelopeRefusesUnknownAndFalseSuccess(t *testing.T) {
	r := catalogRequest()
	for _, change := range []func(map[string]any){
		func(v map[string]any) { v["cleanup_verified"] = false },
		func(v map[string]any) { v["observation"] = nil },
		func(v map[string]any) { v["error_code"] = "PRIVATE" },
		func(v map[string]any) { v["evidence"] = "/tmp/outside" },
		func(v map[string]any) { v["command"] = "query describe" },
		func(v map[string]any) { v["schema_version"] = 2 },
		func(v map[string]any) { v["password"] = "PRIVATE" },
	} {
		var value map[string]any
		_ = json.Unmarshal(catalogLines(r, "passed")[1], &value)
		change(value)
		raw, _ := json.Marshal(value)
		if _, err := decodeCatalogEvent(raw, r); err == nil || strings.Contains(err.Error(), "PRIVATE") {
			t.Fatal("bad envelope accepted or leaked")
		}
	}
}

func TestCatalogChildUsesFixedScriptAndJSONStdin(t *testing.T) {
	r := catalogRequest()
	r.Root = catalogTestRoot(t)
	t.Setenv("HTTP_PROXY", "PRIVATE")
	t.Setenv("OPENAI_API_KEY", "PRIVATE")
	t.Setenv("PYTHONPATH", "PRIVATE")
	cmd, err := catalogReadCommand(r)
	if err != nil {
		t.Fatal(err)
	}
	if cmd.Dir != r.Root || cmd.Path != filepath.Join(r.Root, dataPython) || strings.Join(cmd.Args[1:3], " ") != "-I -B" || cmd.SysProcAttr == nil || !cmd.SysProcAttr.Setpgid {
		t.Fatal("child boundary differs")
	}
	for _, arg := range cmd.Args {
		if strings.Contains(arg, catalogTable) {
			t.Fatal("request escaped stdin")
		}
	}
	for _, env := range cmd.Env {
		if strings.Contains(env, "PRIVATE") {
			t.Fatal("environment leaked")
		}
	}
	raw, err := io.ReadAll(cmd.Stdin)
	if err != nil || !bytes.Equal(raw, r.Request) {
		t.Fatal("stdin differs")
	}
	r.Case = "other"
	if _, err := catalogReadCommand(r); err == nil {
		t.Fatal("wrong case accepted")
	}
	if err := os.Remove(filepath.Join(r.Root, catalogReadScript)); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveCatalogRoot(r.Root); err == nil {
		t.Fatal("missing script installed or accepted")
	}
}

func TestCatalogUnknownProcessNeverPublishes(t *testing.T) {
	var out bytes.Buffer
	child := dataChildFunc(func(_ context.Context, _ dataChildRequest, _ func([]byte) error) (dataChildResult, error) {
		return dataChildResult{}, errors.New("PRIVATE")
	})
	err := runCatalogCommand(context.Background(), child, catalogRequest(), &out)
	if err == nil || out.Len() != 0 || strings.Contains(err.Error(), "PRIVATE") {
		t.Fatal("unstarted process claimed observation")
	}
}
