package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"syscall"
	"time"

	"github.com/spf13/cobra"
)

const warehouseRequestTimeout = 90 * time.Second
const warehouseResponseLimit = 16 * 1024

var warehouseSessionPattern = regexp.MustCompile(`^[0-9a-f]{32}$`)
var warehouseUUIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var warehouseHashPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

type warehouseTransitionRequest struct {
	SchemaVersion int    `json:"schema_version"`
	SessionID     string `json:"session_id"`
	RequestID     string `json:"request_id"`
	Warehouse     string `json:"warehouse"`
	Action        string `json:"action"`
}

type warehouseState struct {
	Replicas      *int  `json:"replicas"`
	BackendActive *bool `json:"backend_active"`
}

type warehouseTransitionResponse struct {
	SchemaVersion int            `json:"schema_version"`
	SessionID     string         `json:"session_id"`
	RequestID     string         `json:"request_id"`
	Warehouse     string         `json:"warehouse"`
	Action        string         `json:"action"`
	Result        string         `json:"result"`
	Before        warehouseState `json:"before"`
	After         warehouseState `json:"after"`
	OperationID   *string        `json:"operation_id"`
	ReceiptSHA256 *string        `json:"receipt_sha256"`
}

type warehouseTransitionClient interface {
	Transition(context.Context, warehouseTransitionRequest) (warehouseTransitionResponse, error)
}

// This client contacts an already-owned bounded session. It never starts,
// kills, or invokes a process, and it has no configurable URL or command.
type unixWarehouseClient struct{}

func newWarehouseCommand(action string, client warehouseTransitionClient) *cobra.Command {
	var sessionID, requestID, profile string
	command := &cobra.Command{
		Use:   action + " XS",
		Short: "Request a verified " + action + " transition in a bounded local warehouse session",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if profile != localExperimentProfile || args[0] != "XS" || !warehouseSessionPattern.MatchString(sessionID) {
				return errors.New("warehouse transitions require XS, --profile local-experiment, and a 32-character lowercase hexadecimal --session")
			}
			if requestID == "" {
				var err error
				requestID, err = newWarehouseRequestID(rand.Reader)
				if err != nil {
					return errors.New("could not generate warehouse request identity")
				}
			}
			request := warehouseTransitionRequest{SchemaVersion: 1, SessionID: sessionID,
				RequestID: requestID, Warehouse: "XS", Action: action}
			if err := validateWarehouseRequest(request); err != nil {
				return err
			}
			// Interrupting this CLI cancels HTTP only. The session retains its
			// operation journal and owns completion, recovery, and cleanup.
			signalContext, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()
			ctx, cancel := context.WithTimeout(signalContext, warehouseRequestTimeout)
			defer cancel()
			report, err := client.Transition(ctx, request)
			if err != nil {
				return warehouseUnconfirmed(request)
			}
			if err := validateWarehouseResponse(report, request); err != nil {
				return warehouseUnconfirmed(request)
			}
			if err := json.NewEncoder(cmd.OutOrStdout()).Encode(report); err != nil {
				return warehouseUnconfirmed(request)
			}
			if report.Result != "verified" && report.Result != "noop" {
				return fmt.Errorf("warehouse transition result=%s; session=%s request_id=%s", report.Result, sessionID, requestID)
			}
			return nil
		},
	}
	command.Flags().StringVar(&sessionID, "session", "", "bounded local session ID (32 lowercase hexadecimal characters)")
	command.Flags().StringVar(&requestID, "request-id", "", "request UUID; reuse only with the same live owner (inspect saved-request after loss)")
	command.Flags().StringVar(&profile, "profile", localExperimentProfile, "warehouse profile: local-experiment")
	return command
}

func warehouseUnconfirmed(request warehouseTransitionRequest) error {
	return fmt.Errorf("warehouse transition not confirmed; session=%s request_id=%s; inspect saved journal with warehouse saved-request --session %s --request-id %s (read-only); retry only with this ID while the same owner is alive or in replay grace; never resubmit after owner exit or under a new session", request.SessionID, request.RequestID, request.SessionID, request.RequestID)
}

func newWarehouseRequestID(reader io.Reader) (string, error) {
	var value [16]byte
	if _, err := io.ReadFull(reader, value[:]); err != nil {
		return "", err
	}
	value[6] = value[6]&0x0f | 0x40
	value[8] = value[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", value[:4], value[4:6], value[6:8], value[8:10], value[10:]), nil
}

func validateWarehouseRequest(request warehouseTransitionRequest) error {
	if request.SchemaVersion != 1 || !warehouseSessionPattern.MatchString(request.SessionID) ||
		!warehouseUUIDPattern.MatchString(request.RequestID) || request.Warehouse != "XS" ||
		(request.Action != "chill" && request.Action != "wake") {
		return errors.New("invalid warehouse transition request identity or action")
	}
	return nil
}

func validateWarehouseResponse(report warehouseTransitionResponse, request warehouseTransitionRequest) error {
	invalid := errors.New("warehouse response failed its identity, state, or evidence contract")
	if report.SchemaVersion != 1 || report.SessionID != request.SessionID || report.RequestID != request.RequestID ||
		report.Warehouse != request.Warehouse || report.Action != request.Action {
		return invalid
	}
	for _, state := range []warehouseState{report.Before, report.After} {
		if state.Replicas != nil && *state.Replicas != 0 && *state.Replicas != 1 {
			return invalid
		}
	}
	if report.OperationID != nil && !warehouseUUIDPattern.MatchString(*report.OperationID) {
		return invalid
	}
	if report.ReceiptSHA256 != nil && !warehouseHashPattern.MatchString(*report.ReceiptSHA256) {
		return invalid
	}
	switch report.Result {
	case "busy", "unknown", "failed":
		return nil
	case "verified", "noop":
		if report.OperationID == nil || report.ReceiptSHA256 == nil {
			return invalid
		}
		want := 0
		if request.Action == "wake" {
			want = 1
		}
		before := 1 - want
		if report.Result == "noop" {
			before = want
		}
		matches := func(state warehouseState, replicas int) bool {
			return state.Replicas != nil && state.BackendActive != nil && *state.Replicas == replicas && *state.BackendActive == (replicas == 1)
		}
		if !matches(report.Before, before) || !matches(report.After, want) {
			return invalid
		}
		return nil
	default:
		return invalid
	}
}

func warehouseSocketPath(sessionID string) string {
	return "/private/tmp/shirokuma-wl-" + sessionID + "/control.sock"
}

func (unixWarehouseClient) Transition(ctx context.Context, request warehouseTransitionRequest) (warehouseTransitionResponse, error) {
	if err := validateWarehouseRequest(request); err != nil {
		return warehouseTransitionResponse{}, err
	}
	return transitionAtWarehouseSocket(ctx, request, warehouseSocketPath(request.SessionID))
}

// checkWarehouseSocket also returns inode identities so a replacement between
// preflight and connect cannot silently select a different local endpoint.
func checkWarehouseSocket(path string) ([]os.FileInfo, error) {
	invalid := errors.New("warehouse session socket is not a private owned Unix socket")
	if !filepath.IsAbs(path) || filepath.Clean(path) != path || len(path) >= 104 {
		return nil, invalid
	}
	parent := filepath.Dir(path)
	for current := parent; ; current = filepath.Dir(current) {
		info, err := os.Lstat(current)
		if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return nil, invalid
		}
		if current == filepath.Dir(current) {
			break
		}
	}
	directory, err := os.Lstat(parent)
	if err != nil || directory.Mode().Perm() != 0700 || directory.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return nil, invalid
	}
	socket, err := os.Lstat(path)
	if err != nil || socket.Mode()&os.ModeSocket == 0 || socket.Mode()&os.ModeSymlink != 0 || socket.Mode().Perm() != 0600 ||
		socket.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return nil, invalid
	}
	for _, info := range []os.FileInfo{directory, socket} {
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || stat.Uid != uint32(os.Getuid()) {
			return nil, invalid
		}
	}
	return []os.FileInfo{directory, socket}, nil
}

func transitionAtWarehouseSocket(ctx context.Context, request warehouseTransitionRequest, path string) (warehouseTransitionResponse, error) {
	var empty warehouseTransitionResponse
	if err := validateWarehouseRequest(request); err != nil {
		return empty, err
	}
	status, data, err := exchangeWarehouseSocket(ctx, request, path, warehouseRequestTimeout)
	if err != nil {
		return empty, err
	}
	report, err := decodeWarehouseResponse(data, request)
	if err != nil {
		return empty, err
	}
	expectedStatus := map[string]int{"verified": 200, "noop": 200, "busy": 409, "unknown": 409, "failed": 500}[report.Result]
	if status != expectedStatus {
		return empty, errors.New("warehouse response status and result disagree")
	}
	return report, nil
}

// Both fixed protocols use the same private socket checks and HTTP boundary.
// The caller validates the action-specific request and exact response schema.
func exchangeWarehouseSocket(ctx context.Context, request warehouseTransitionRequest, path string, timeout time.Duration) (int, []byte, error) {
	before, err := checkWarehouseSocket(path)
	if err != nil {
		return 0, nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	transport := &http.Transport{
		Proxy: nil, DisableKeepAlives: true, MaxResponseHeaderBytes: 8192,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			if network != "tcp" || address != "warehouse.local:80" {
				return nil, errors.New("warehouse network destination refused")
			}
			current, err := checkWarehouseSocket(path)
			if err != nil || !os.SameFile(before[0], current[0]) || !os.SameFile(before[1], current[1]) {
				return nil, errors.New("warehouse session socket changed before connection")
			}
			connection, err := (&net.Dialer{}).DialContext(ctx, "unix", path)
			if err != nil {
				return nil, err
			}
			after, err := checkWarehouseSocket(path)
			if err != nil || !os.SameFile(before[0], after[0]) || !os.SameFile(before[1], after[1]) {
				connection.Close()
				return nil, errors.New("warehouse session socket changed during connection")
			}
			return connection, nil
		},
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	body, err := json.Marshal(request)
	if err != nil {
		return 0, nil, err
	}
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://warehouse.local/v1/transition", bytes.NewReader(body))
	if err != nil {
		return 0, nil, err
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	httpRequest.Header.Set("Accept", "application/json")
	response, err := client.Do(httpRequest)
	if err != nil {
		return 0, nil, err
	}
	defer response.Body.Close()
	mediaType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		return 0, nil, errors.New("warehouse response content type invalid")
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, warehouseResponseLimit+1))
	if err != nil || len(data) > warehouseResponseLimit {
		return 0, nil, errors.New("warehouse response incomplete or too large")
	}
	return response.StatusCode, data, nil
}

func decodeWarehouseResponse(data []byte, request warehouseTransitionRequest) (warehouseTransitionResponse, error) {
	var report warehouseTransitionResponse
	invalid := errors.New("warehouse response JSON invalid")
	if len(data) > warehouseResponseLimit || uniqueWarehouseJSON(data) != nil {
		return report, invalid
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(data, &fields) != nil || !warehouseKeys(fields, "schema_version", "session_id", "request_id", "warehouse", "action", "result", "before", "after", "operation_id", "receipt_sha256") {
		return report, invalid
	}
	for _, key := range []string{"before", "after"} {
		var state map[string]json.RawMessage
		if json.Unmarshal(fields[key], &state) != nil || !warehouseKeys(state, "replicas", "backend_active") {
			return report, invalid
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&report) != nil || validateWarehouseResponse(report, request) != nil {
		return warehouseTransitionResponse{}, invalid
	}
	return report, nil
}

func warehouseKeys(value map[string]json.RawMessage, keys ...string) bool {
	if len(value) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, exists := value[key]; !exists {
			return false
		}
	}
	return true
}

func uniqueWarehouseJSON(data []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var consume func(int) error
	consume = func(depth int) error {
		if depth > 3 {
			return errors.New("warehouse response nesting invalid")
		}
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		if delimiter, ok := token.(json.Delim); ok {
			if delimiter != '{' {
				return errors.New("warehouse response arrays are not allowed")
			}
			seen := make(map[string]bool)
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return err
				}
				text, ok := key.(string)
				if !ok || seen[text] {
					return errors.New("warehouse response duplicate key")
				}
				seen[text] = true
				if err := consume(depth + 1); err != nil {
					return err
				}
			}
			end, err := decoder.Token()
			if err != nil || end != json.Delim('}') {
				return errors.New("warehouse response object incomplete")
			}
		}
		return nil
	}
	if err := consume(0); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return errors.New("warehouse response trailing JSON")
	}
	return nil
}
