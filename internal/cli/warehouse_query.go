package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
)

const warehouseQueryTimeout = 240 * time.Second

var warehouseQueryIDPattern = regexp.MustCompile(`^[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]{5}$`)

type warehouseQueryResult struct {
	Case             string `json:"case"`
	QueryID          string `json:"query_id"`
	RowsObserved     int    `json:"rows_observed"`
	RowsSHA256       string `json:"rows_sha256"`
	PostCount        int    `json:"post_count"`
	ResponseComplete bool   `json:"response_complete"`
	Matched          bool   `json:"matched"`
}

type warehouseQueryResponse struct {
	warehouseTransitionResponse
	Query *warehouseQueryResult `json:"query"`
}

type warehouseQueryClient interface {
	Query(context.Context, warehouseTransitionRequest) (warehouseQueryResponse, error)
}

// This group is separate from the Query Doctor's existing query run/status CLI.
func newWarehouseQueryCommand(client warehouseQueryClient, runners ...residentWarehouseRunner) *cobra.Command {
	resident := residentWarehouseRunner(execResidentWarehouse{})
	if len(runners) > 0 {
		resident = runners[0]
	}
	group := &cobra.Command{Use: "warehouse", Short: "Control and query a local warehouse"}
	var sessionID, requestID, profile, sql, catalog, schema, repoRoot, mode string
	var noAutoWake bool
	var maxRows int
	command := &cobra.Command{
		Use: "query <XS|S|LOCAL> [SQL]", Short: "Query the resident Trino warehouse (XS/S/LOCAL share one backend)",
		Args: cobra.RangeArgs(1, 2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if mode != "" {
				if sessionID != "" {
					return errors.New("cannot use --mode with --session")
				}
				if mode != "read-only" && mode != "sandbox-write" && mode != "admin-write" {
					return errors.New("invalid SQL guardrail mode")
				}
			}
			if maxRows < 1 || (sessionID != "" && cmd.Flags().Changed("max-rows")) {
				return errors.New("--max-rows must be >= 1 and cannot be used with --session")
			}
			if sessionID == "" {
				if profile != localExperimentProfile || requestID != "" || !validResidentWarehouse(args[0]) ||
					(len(args) == 2 && cmd.Flags().Changed("sql")) {
					return errors.New("invalid resident warehouse query arguments")
				}
				if len(args) == 2 {
					sql = args[1]
				}
				if strings.TrimSpace(sql) == "" {
					return errors.New("resident warehouse query requires SQL")
				}
				childArgs := []string{"query", "--warehouse", args[0], "--sql=" + sql, "--max-rows", strconv.Itoa(maxRows)}
				if mode != "" {
					childArgs = append(childArgs, "--mode", mode)
				}
				for _, option := range []struct{ name, value string }{{"catalog", catalog}, {"schema", schema}} {
					if option.value != "" {
						childArgs = append(childArgs, "--"+option.name, option.value)
					}
				}
				if noAutoWake {
					childArgs = append(childArgs, "--no-auto-wake")
				}
				return runResidentWarehouse(cmd, resident, repoRoot, childArgs)
			}
			if len(args) != 1 || sql != "" || catalog != "" || schema != "" || noAutoWake || args[0] != "XS" || profile != localExperimentProfile || !warehouseSessionPattern.MatchString(sessionID) {
				return errors.New("warehouse query requires XS, --profile local-experiment, and a 32-character lowercase hexadecimal --session")
			}
			if requestID == "" {
				var err error
				requestID, err = newWarehouseRequestID(rand.Reader)
				if err != nil {
					return errors.New("could not generate warehouse query identity")
				}
			}
			request := warehouseTransitionRequest{SchemaVersion: 1, SessionID: sessionID,
				RequestID: requestID, Warehouse: "XS", Action: "query"}
			if err := validateWarehouseQueryRequest(request); err != nil {
				return err
			}
			// Cancelling the CLI stops this HTTP wait, never the owner or its query.
			interrupt, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			ctx, cancel := context.WithTimeout(interrupt, warehouseQueryTimeout)
			defer cancel()
			report, err := client.Query(ctx, request)
			if err != nil || validateWarehouseQueryResponse(report, request) != nil {
				return warehouseQueryUnconfirmed(request)
			}
			if err := json.NewEncoder(cmd.OutOrStdout()).Encode(report); err != nil {
				return warehouseQueryUnconfirmed(request)
			}
			if report.Result != "verified" {
				return fmt.Errorf("warehouse query result=%s; session=%s request_id=%s", report.Result, sessionID, requestID)
			}
			return nil
		},
	}
	command.Flags().StringVar(&sessionID, "session", "", "existing query-mode session ID (32 lowercase hexadecimal characters)")
	command.Flags().StringVar(&requestID, "request-id", "", "request UUID; reuse only with the same live owner (inspect saved-request after loss)")
	command.Flags().StringVar(&profile, "profile", localExperimentProfile, "warehouse profile: local-experiment")
	command.Flags().StringVar(&sql, "sql", "", "SQL to execute against resident Trino")
	command.Flags().StringVar(&catalog, "catalog", "", "Trino catalog")
	command.Flags().StringVar(&schema, "schema", "", "Trino schema")
	command.Flags().StringVar(&mode, "mode", "", "SQL guardrail mode: read-only, sandbox-write, admin-write")
	command.Flags().StringVar(&repoRoot, "repo-root", "", "repository root")
	command.Flags().BoolVar(&noAutoWake, "no-auto-wake", false, "fail if the resident warehouse is chilled")
	command.Flags().IntVar(&maxRows, "max-rows", 1000, "maximum resident query result rows")
	group.AddCommand(command)
	for _, action := range []string{"status", "chill", "wake"} {
		group.AddCommand(newResidentWarehouseCommand(action, resident))
	}
	return group
}

func warehouseQueryUnconfirmed(request warehouseTransitionRequest) error {
	return fmt.Errorf("warehouse query not confirmed; session=%s request_id=%s; inspect saved journal with warehouse saved-request --session %s --request-id %s (read-only); retry only with this ID while the same owner is alive or in its 15-second replay grace; never resubmit after owner exit or under a new session", request.SessionID, request.RequestID, request.SessionID, request.RequestID)
}

func validateWarehouseQueryRequest(request warehouseTransitionRequest) error {
	if request.SchemaVersion != 1 || !warehouseSessionPattern.MatchString(request.SessionID) ||
		!warehouseUUIDPattern.MatchString(request.RequestID) || request.Warehouse != "XS" || request.Action != "query" {
		return errors.New("invalid fixed warehouse query request")
	}
	return nil
}

func validateWarehouseQueryResponse(report warehouseQueryResponse, request warehouseTransitionRequest) error {
	invalid := errors.New("warehouse query response failed its identity, outcome, or evidence contract")
	if validateWarehouseQueryRequest(request) != nil || report.Action != "query" || report.Result == "noop" ||
		(report.OperationID == nil) != (report.ReceiptSHA256 == nil) {
		return invalid
	}
	// Query success requires the same 0/inactive -> 1/active transition as wake,
	// plus independently verified caller-query evidence below. Legacy stays exact.
	transition, wake := report.warehouseTransitionResponse, request
	transition.Action, wake.Action = "wake", "wake"
	if validateWarehouseResponse(transition, wake) != nil {
		return invalid
	}
	if report.Result != "verified" {
		if report.Query != nil {
			return invalid
		}
		return nil
	}
	q := report.Query
	if q == nil || q.Case != "xs_caller" || !warehouseQueryIDPattern.MatchString(q.QueryID) ||
		q.RowsObserved != 25 || !warehouseHashPattern.MatchString(q.RowsSHA256) ||
		q.PostCount != 1 || !q.ResponseComplete || !q.Matched {
		return invalid
	}
	return nil
}

func (unixWarehouseClient) Query(ctx context.Context, request warehouseTransitionRequest) (warehouseQueryResponse, error) {
	return queryAtWarehouseSocket(ctx, request, warehouseSocketPath(request.SessionID))
}

func queryAtWarehouseSocket(ctx context.Context, request warehouseTransitionRequest, path string) (warehouseQueryResponse, error) {
	var empty warehouseQueryResponse
	if err := validateWarehouseQueryRequest(request); err != nil {
		return empty, err
	}
	status, data, err := exchangeWarehouseSocket(ctx, request, path, warehouseQueryTimeout)
	if err != nil {
		return empty, err
	}
	report, err := decodeWarehouseQueryResponse(data, request)
	if err != nil {
		return empty, err
	}
	if status != map[string]int{"verified": 200, "busy": 409, "unknown": 409, "failed": 500}[report.Result] {
		return empty, errors.New("warehouse query HTTP status and result disagree")
	}
	return report, nil
}

func decodeWarehouseQueryResponse(data []byte, request warehouseTransitionRequest) (warehouseQueryResponse, error) {
	var report warehouseQueryResponse
	invalid := errors.New("warehouse query response JSON invalid")
	if len(data) > warehouseResponseLimit || uniqueWarehouseJSON(data) != nil {
		return report, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(data, &fields) != nil || !warehouseKeys(fields, "schema_version", "session_id", "request_id", "warehouse", "action", "result", "before", "after", "operation_id", "receipt_sha256", "query") {
		return report, invalid
	}
	for _, key := range []string{"before", "after"} {
		var state map[string]json.RawMessage
		if json.Unmarshal(fields[key], &state) != nil || !warehouseKeys(state, "replicas", "backend_active") {
			return report, invalid
		}
	}
	if !bytes.Equal(fields["query"], []byte("null")) {
		var query map[string]json.RawMessage
		if json.Unmarshal(fields["query"], &query) != nil || !warehouseKeys(query, "case", "query_id", "rows_observed", "rows_sha256", "post_count", "response_complete", "matched") {
			return report, invalid
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&report) != nil || validateWarehouseQueryResponse(report, request) != nil {
		return warehouseQueryResponse{}, invalid
	}
	return report, nil
}
