package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"syscall"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const catalogReadCase = "catalog-description-v1"
const catalogReadResponseLimit = 256 * 1024
const catalogTable = "smoke.fixture_v1"
const catalogFQN = "shirokuma_local.iceberg." + catalogTable

type catalogReadRequest struct {
	Command string `json:"command"`
	Table   string `json:"table"`
}

func newCatalogCommand(child dataChildRunner) *cobra.Command {
	var root, profile, table string
	parent := &cobra.Command{Use: "catalog", Short: "Read bounded local catalog metadata", Args: cobra.NoArgs, SilenceErrors: true, SilenceUsage: true}
	parent.RunE = func(cmd *cobra.Command, _ []string) error { return cmd.Help() }
	parent.PersistentFlags().StringVar(&root, "repo-root", "", "checkout root (auto-detected from the current directory)")
	parent.PersistentFlags().StringVar(&profile, "profile", localExperimentProfile, "catalog profile: local-experiment")
	command := &cobra.Command{Use: "describe --table smoke.fixture_v1", Short: "Read the retained fixture's catalog description, owner, columns and one-hop lineage", Args: func(_ *cobra.Command, args []string) error {
		if len(args) != 0 {
			return errors.New("catalog describe does not accept positional arguments")
		}
		return nil
	}, RunE: func(cmd *cobra.Command, _ []string) error {
		if profile != localExperimentProfile {
			return errors.New("catalog describe requires --profile local-experiment")
		}
		raw, _ := json.Marshal(catalogReadRequest{Command: "describe", Table: table})
		if _, err := decodeCatalogRequest(raw, "describe"); err != nil {
			return err
		}
		resolved, err := resolveCatalogRoot(root)
		if err != nil {
			return err
		}
		var token [16]byte
		if _, err = io.ReadFull(rand.Reader, token[:]); err != nil {
			return errors.New("catalog evidence identity unavailable")
		}
		request := dataChildRequest{Action: "describe", Case: catalogReadCase, Root: resolved, Token: hex.EncodeToString(token[:]), Request: raw}
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
		defer cancel()
		return runCatalogCommand(ctx, child, request, cmd.OutOrStdout())
	}}
	command.Flags().StringVar(&table, "table", "", "fixed catalog table: smoke.fixture_v1")
	command.SetFlagErrorFunc(func(_ *cobra.Command, _ error) error { return errors.New("catalog describe flags are invalid") })
	parent.AddCommand(command)
	return parent
}

func decodeCatalogRequest(raw []byte, action string) (catalogReadRequest, error) {
	var value catalogReadRequest
	invalid := errors.New("catalog input does not match the fixed fixture contract")
	if action != "describe" || len(raw) == 0 || len(raw) > 1024 || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || json.Unmarshal(raw, &value) != nil || !warehouseKeys(fields, "command", "table") || value.Command != action || value.Table != catalogTable {
		return value, invalid
	}
	return value, nil
}

func runCatalogCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	if request.Case != catalogReadCase || !warehouseSessionPattern.MatchString(request.Token) {
		return errors.New("invalid fixed catalog invocation")
	}
	if _, err := decodeCatalogRequest(request.Request, request.Action); err != nil {
		return err
	}
	allocated := false
	var final *queryReadEvent
	var finalLine []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodeCatalogEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || event.Event == "allocated" && allocated {
			return errors.New("catalog event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			return writeQueryReadLine(output, line)
		}
		if event.Result == "passed" && !allocated {
			return errors.New("catalog allocation missing")
		}
		final, finalLine = &event, append([]byte(nil), line...)
		return nil // Publish final metadata only after the matching child exit.
	})
	if !result.Started {
		return errors.New("catalog child did not start; no observation was confirmed")
	}
	unknown := fmt.Sprintf("catalog outcome unknown; evidence_token=%s child_pid=%d; inspect tmp/local-experiment/catalog-read/%s/summary.json before another observation", request.Token, result.PID, request.Token)
	if result.Interrupted && errors.Is(err, errDataInterrupted) && (result.ExitCode == 130 || result.ExitCode == 143) {
		return &dataExitError{code: result.ExitCode, message: unknown}
	}
	if err != nil || result.Unconfirmed || final == nil {
		return errors.New(unknown)
	}
	want := 1
	if final.Result == "passed" {
		want = 0
	}
	if final.Result == "interrupted" {
		if result.ExitCode != 130 && result.ExitCode != 143 {
			return errors.New(unknown)
		}
		want = result.ExitCode
	}
	if result.ExitCode != want {
		return errors.New(unknown)
	}
	if err := writeQueryReadLine(output, finalLine); err != nil {
		return errors.New(unknown)
	}
	if want == 0 {
		return nil
	}
	return &dataExitError{code: want, message: fmt.Sprintf("catalog result=%s; evidence_token=%s", final.Result, request.Token)}
}

func catalogErrorCode(code string) bool {
	return dataOneOf(code, "INPUT_INVALID", "REQUEST_INVALID", "TABLE_UNSUPPORTED", "CATALOG_RESPONSE_INVALID", "CATALOG_ANCHOR_INVALID", "CATALOG_SCOPE_CONFLICT", "CATALOG_LINEAGE_INVALID", "CATALOG_LIMIT", "CATALOG_CHANGED", "CATALOG_PUBLIC_INVALID", "CATALOG_RUNTIME_INVALID", "CATALOG_RUNTIME_CHANGED", "CATALOG_STATE_CHANGED", "CATALOG_SOURCE_CHANGED", "CATALOG_READ_FAILED", "CATALOG_DEADLINE", "CATALOG_REQUEST_LIMIT", "CATALOG_READ_ONLY_BOUNDARY", "EVIDENCE_ALREADY_EXISTS", "EVIDENCE_INVALID", "INTERRUPTED", "READ_FAILED", "ARGUMENTS_INVALID")
}

func decodeCatalogEvent(line []byte, request dataChildRequest) (queryReadEvent, error) {
	var value queryReadEvent
	invalid := errors.New("catalog child response failed the fixed protocol")
	if request.Action != "describe" || request.Case != catalogReadCase || !warehouseSessionPattern.MatchString(request.Token) || len(line) > catalogReadResponseLimit || !utf8.Valid(line) || uniqueQueryReadJSON(line) != nil {
		return value, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(line, &fields) != nil || json.Unmarshal(line, &value) != nil {
		return value, invalid
	}
	keys := []string{"schema_version", "event", "command", "evidence_token"}
	if value.Event == "result" {
		keys = append(keys, "result", "observation", "error_code", "evidence", "cleanup_verified")
	} else if value.Event != "allocated" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) || value.SchemaVersion != 1 || value.Command != "catalog describe" || value.EvidenceToken != request.Token {
		return value, invalid
	}
	for key, raw := range fields {
		if key != "error_code" && key != "observation" && bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return value, invalid
		}
	}
	if value.Event == "allocated" {
		return value, nil
	}
	if value.Evidence != "tmp/local-experiment/catalog-read/"+request.Token+"/summary.json" || !dataOneOf(value.Result, "passed", "failed", "interrupted") {
		return value, invalid
	}
	if value.Result == "passed" {
		if _, err := decodeCatalogRequest(request.Request, request.Action); err != nil || !value.CleanupVerified || value.ErrorCode != nil || !catalogObservation(value.Observation) {
			return value, invalid
		}
	} else if !bytes.Equal(bytes.TrimSpace(value.Observation), []byte("null")) || value.ErrorCode == nil || !catalogErrorCode(*value.ErrorCode) || value.Result == "interrupted" && *value.ErrorCode != "INTERRUPTED" {
		return value, invalid
	}
	return value, nil
}

var catalogVersion = regexp.MustCompile(`^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-][0-9]+)?$`)
var catalogGenerated = regexp.MustCompile(`^shirokuma_local\.iceberg\.local_experiment\.dataops_[0-9a-f]{32}_(stg|mart)$`)

func catalogText(value any) bool {
	if value == nil {
		return true
	}
	text, ok := value.(string)
	if !ok || len(text) > 2048 || !utf8.ValidString(text) {
		return false
	}
	for _, char := range text {
		if char == 127 || char < 32 && char != '\n' && char != '\t' {
			return false
		}
	}
	return true
}

func catalogObservation(raw []byte) bool {
	if len(raw) == 0 || len(raw) > 64*1024 || !utf8.Valid(raw) || uniqueQueryReadJSON(raw) != nil {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var decoded any
	if decoder.Decode(&decoded) != nil {
		return false
	}
	value, ok := queryReadObject(decoded, "schema_version", "table", "catalog_entity", "description", "owners", "columns", "lineage", "sql_presence", "observation")
	if !ok {
		return false
	}
	_, versionOK := queryReadInt(value["schema_version"], 1, 1)
	observation, scopeOK := queryReadObject(value["observation"], "scope", "stable")
	entity, entityOK := queryReadObject(value["catalog_entity"], "id", "fully_qualified_name", "version", "updated_at_ms")
	if !versionOK || value["table"] != "iceberg."+catalogTable || value["sql_presence"] != "not-checked" || !catalogText(value["description"]) || !scopeOK || observation["scope"] != "catalog-metadata-only" || observation["stable"] != true || !entityOK || entity["fully_qualified_name"] != catalogFQN || !queryReadUUID(entity["id"]) {
		return false
	}
	version, textOK := entity["version"].(string)
	n, parseErr := strconv.ParseFloat(version, 64)
	_, timeOK := queryReadInt(entity["updated_at_ms"], 1, math.MaxInt64)
	if !textOK || len(version) > 32 || !catalogVersion.MatchString(version) || parseErr != nil || n <= 0 || math.IsInf(n, 0) || math.IsNaN(n) || !timeOK {
		return false
	}
	owners, ownersOK := value["owners"].([]any)
	if !ownersOK || len(owners) != 1 {
		return false
	}
	owner, ownerOK := queryReadObject(owners[0], "id", "name", "type")
	if !ownerOK || !queryReadUUID(owner["id"]) || owner["id"] == entity["id"] || owner["name"] != "ShirokumaLocalExperiment" || owner["type"] != "team" {
		return false
	}
	columns, columnsOK := value["columns"].([]any)
	if !columnsOK || len(columns) != 2 {
		return false
	}
	for index, expected := range [][2]string{{"id", "BIGINT"}, {"name", "VARCHAR"}} {
		column, valid := queryReadObject(columns[index], "name", "type", "comment")
		if !valid || column["name"] != expected[0] || column["type"] != expected[1] || !catalogText(column["comment"]) {
			return false
		}
	}
	return catalogLineage(value["lineage"], entity["id"].(string), owner["id"].(string))
}

func catalogLineage(value any, rootID, ownerID string) bool {
	graph, ok := queryReadObject(value, "scope", "observed", "nodes", "edges")
	if !ok || graph["scope"] != "one-hop" || graph["observed"] != true {
		return false
	}
	nodes, nodesOK := graph["nodes"].([]any)
	edges, edgesOK := graph["edges"].([]any)
	if !nodesOK || !edgesOK || len(nodes) > 64 || len(edges) > 64 {
		return false
	}
	identities := map[string]bool{rootID: true}
	names := map[string]bool{}
	previous := ""
	for _, raw := range nodes {
		node, valid := queryReadObject(raw, "id", "fully_qualified_name", "sql_presence")
		if !valid || !queryReadUUID(node["id"]) || node["sql_presence"] != "not-checked" {
			return false
		}
		id := node["id"].(string)
		name, nameOK := node["fully_qualified_name"].(string)
		if !nameOK || !catalogGenerated.MatchString(name) || identities[id] || id == ownerID || names[name] || name <= previous {
			return false
		}
		identities[id], names[name], previous = true, true, name
	}
	connected := map[string]bool{}
	previous = ""
	for _, raw := range edges {
		edge, valid := queryReadObject(raw, "from_entity", "to_entity", "source")
		if !valid {
			return false
		}
		left, leftOK := edge["from_entity"].(string)
		right, rightOK := edge["to_entity"].(string)
		key := left + ":" + right
		if !leftOK || !rightOK || !identities[left] || !identities[right] || left == right || left != rootID && right != rootID || key <= previous || edge["source"] != nil && edge["source"] != "Manual" && edge["source"] != "OpenLineage" {
			return false
		}
		connected[left], connected[right], previous = true, true, key
	}
	for id := range identities {
		if id != rootID && !connected[id] {
			return false
		}
	}
	return true
}
