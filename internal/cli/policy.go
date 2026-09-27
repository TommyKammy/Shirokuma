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
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const policyScript = "tools/local_experiment/bears/policy_cli.py"
const policyInputLimit = 16 * 1024

type policyEvent struct {
	SchemaVersion        int      `json:"schema_version"`
	Event                string   `json:"event"`
	Command              string   `json:"command"`
	EvidenceToken        string   `json:"evidence_token"`
	WorkflowToken        *string  `json:"workflow_token"`
	Result               string   `json:"result"`
	Execution            string   `json:"execution"`
	Verification         string   `json:"verification"`
	Admission            string   `json:"admission"`
	ResponseContentCheck string   `json:"response_content_check"`
	ExpectedViolationIDs []string `json:"expected_violation_ids"`
	ReportedViolationIDs []string `json:"reported_violation_ids"`
	AuthorityGranted     bool     `json:"authority_granted"`
	PersistedWorkloads   int      `json:"persisted_workloads"`
	ErrorCode            *string  `json:"error_code"`
	Evidence             *string  `json:"evidence"`
}

func newPolicyCommand(child dataChildRunner) *cobra.Command {
	var repositoryRoot, profile string
	parent := &cobra.Command{Use: "policy", Short: "Check a bounded Pod candidate with advisory policy classification and server dry-run", Args: cobra.NoArgs, SilenceErrors: true, SilenceUsage: true}
	parent.RunE = func(cmd *cobra.Command, _ []string) error { return cmd.Help() }
	parent.PersistentFlags().StringVar(&repositoryRoot, "repo-root", "", "checkout root (auto-detected from the current directory)")
	parent.PersistentFlags().StringVar(&profile, "profile", localExperimentProfile, "policy profile: local-experiment")
	for _, action := range []string{"check", "status"} {
		action := action
		var podStdin bool
		use := "status TOKEN"
		if action == "check" {
			use = "check --pod-stdin"
		}
		command := &cobra.Command{Use: use, Short: map[string]string{"check": "Classify one bounded Pod JSON from stdin and verify native admission", "status": "Inspect saved evidence without model or API calls"}[action],
			Args: func(_ *cobra.Command, args []string) error {
				want := 1
				if action == "check" {
					want = 0
				}
				if len(args) != want {
					return errors.New("policy command arguments are invalid")
				}
				return nil
			},
			RunE: func(cmd *cobra.Command, args []string) error {
				if profile != localExperimentProfile {
					return errors.New("policy requires --profile local-experiment")
				}
				request := dataChildRequest{Action: action}
				if action == "check" {
					if !podStdin {
						return errors.New("policy check requires --pod-stdin")
					}
					var err error
					request.Request, err = readPolicyPod(cmd.InOrStdin())
					if err != nil {
						return err
					}
					var token [16]byte
					if _, err := io.ReadFull(rand.Reader, token[:]); err != nil {
						return errors.New("policy evidence identity could not be generated")
					}
					request.Token = hex.EncodeToString(token[:])
				} else {
					request.Token = args[0]
					if !warehouseSessionPattern.MatchString(request.Token) {
						return errors.New("policy token must be 32 lowercase hexadecimal characters")
					}
				}
				root, err := resolvePolicyRoot(repositoryRoot)
				if err != nil {
					return err
				}
				request.Root = root
				ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
				defer stop()
				ctx, cancel := context.WithTimeout(ctx, dataExecutionTimeout)
				defer cancel()
				return runPolicyCommand(ctx, child, request, cmd.OutOrStdout())
			},
		}
		if action == "check" {
			command.Flags().BoolVar(&podStdin, "pod-stdin", false, "read one Pod JSON from stdin, at most 16 KiB; raw input is not retained")
		}
		parent.AddCommand(command)
	}
	return parent
}

func readPolicyPod(input io.Reader) ([]byte, error) {
	raw, err := io.ReadAll(io.LimitReader(input, policyInputLimit+1))
	if err != nil || len(raw) > policyInputLimit || !utf8.Valid(raw) || len(bytes.TrimSpace(raw)) == 0 {
		return nil, errors.New("policy Pod input must be nonblank UTF-8, at most 16 KiB")
	}
	// Preserve exact bytes for the Python parser's input hash. Its bounded Pod
	// validator runs before model or API access; Go does not normalize the JSON.
	return raw, nil
}

func resolvePolicyRoot(explicit string) (string, error) {
	root, err := resolveRepositoryRoot(explicit)
	if err != nil {
		return "", errors.New("policy checkout was not found; specify --repo-root")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", errors.New("policy checkout could not be resolved")
	}
	for _, relative := range []string{policyScript, "tools/local_experiment/llm/.venv/pyvenv.cfg"} {
		path := filepath.Join(root, relative)
		resolved, e := filepath.EvalSymlinks(path)
		if e != nil || resolved != path || !regularFile(path) {
			return "", errors.New("policy checkout or existing dedicated environment is not ready; no installation was attempted")
		}
	}
	info, err := os.Stat(filepath.Join(root, dataPython))
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return "", errors.New("existing dedicated policy Python is unavailable; no installation was attempted")
	}
	return root, nil
}

func runPolicyCommand(ctx context.Context, child dataChildRunner, request dataChildRequest, output io.Writer) error {
	allocated := false
	var final *policyEvent
	var finalLine []byte
	result, err := child.Run(ctx, request, func(line []byte) error {
		event, e := decodePolicyEvent(line, request)
		if e != nil {
			return e
		}
		if final != nil || (event.Event == "allocated" && allocated) {
			return errors.New("policy child event order invalid")
		}
		if event.Event == "allocated" {
			allocated = true
			_, e = output.Write(append(append([]byte(nil), line...), '\n'))
			return e
		}
		if !allocated && event.Execution != "not-started" {
			return errors.New("policy child allocation missing")
		}
		final = &event
		finalLine = append([]byte(nil), line...)
		return nil // Completion requires the matching child exit, including deny=2.
	})
	if !result.Started {
		return errors.New("policy child did not start; no workflow invocation was confirmed")
	}
	unknown := fmt.Sprintf("policy outcome unknown; evidence_token=%s child_pid=%d; use policy status %s before any further action (allocation does not prove durable intent)", request.Token, result.PID, request.Token)
	if (result.ExitCode == 130 || result.ExitCode == 143) && result.Interrupted && errors.Is(err, errDataInterrupted) {
		return &dataExitError{code: result.ExitCode, message: unknown}
	}
	if err != nil || result.Unconfirmed || final == nil {
		return errors.New(unknown)
	}
	if (result.ExitCode == 130 || result.ExitCode == 143) &&
		(final.Result == "unknown" || (final.Result == "not_started" && final.Execution == "not-started")) &&
		final.ErrorCode != nil && *final.ErrorCode == "INTERRUPTED" {
		if _, err := output.Write(append(finalLine, '\n')); err != nil {
			return errors.New(unknown)
		}
		return &dataExitError{code: result.ExitCode, message: unknown}
	}
	want := 1
	if request.Action == "status" {
		if final.Verification == "journal-only" || final.Verification == "saved-evidence" {
			want = 0
		}
	} else if final.Result == "passed" {
		want = 0
		if final.Admission == "denied" {
			want = 2
		}
	}
	if result.ExitCode != want {
		return fmt.Errorf("policy exit and receipt disagree; outcome unknown; evidence_token=%s", request.Token)
	}
	if _, err := output.Write(append(finalLine, '\n')); err != nil {
		return fmt.Errorf("policy result delivery unconfirmed; evidence_token=%s", request.Token)
	}
	if want == 0 {
		return nil
	}
	return &dataExitError{code: want, message: fmt.Sprintf("policy result=%s admission=%s; evidence_token=%s", final.Result, final.Admission, request.Token)}
}

func decodePolicyEvent(line []byte, request dataChildRequest) (policyEvent, error) {
	var value policyEvent
	invalid := errors.New("policy child response failed the fixed protocol")
	if len(line) > dataResponseLimit || !utf8.Valid(line) {
		return value, invalid
	}
	fields, err := policyJSONFields(line)
	if err != nil {
		return value, invalid
	}
	var kind string
	if json.Unmarshal(fields["event"], &kind) != nil {
		return value, invalid
	}
	keys := []string{"schema_version", "event", "command", "evidence_token"}
	if kind == "result" {
		keys = append(keys, "workflow_token", "result", "execution", "verification", "admission", "response_content_check", "expected_violation_ids", "reported_violation_ids", "authority_granted", "persisted_workloads", "error_code", "evidence")
	} else if kind != "allocated" {
		return value, invalid
	}
	if !warehouseKeys(fields, keys...) {
		return value, invalid
	}
	for _, key := range []string{"schema_version", "event", "command", "evidence_token", "result", "execution", "verification", "admission", "response_content_check", "authority_granted", "persisted_workloads"} {
		if raw, ok := fields[key]; ok && bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return value, invalid
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&value) != nil || value.SchemaVersion != 1 || value.Command != request.Action || value.EvidenceToken != request.Token {
		return value, invalid
	}
	if kind == "allocated" {
		return value, nil
	}
	if !dataOneOf(value.Result, "passed", "failed", "unknown", "not_started", "not_found", "busy") ||
		!dataOneOf(value.Execution, "not-started", "invoked", "read-only") ||
		!dataOneOf(value.Verification, "not-checked", "journal-only", "saved-evidence") ||
		!dataOneOf(value.Admission, "not-observed", "unknown", "allowed", "denied") ||
		!dataOneOf(value.ResponseContentCheck, "unknown", "legacy-unrecorded", "producer-checked", "not-applicable") ||
		value.AuthorityGranted || value.PersistedWorkloads != 0 ||
		!policyRules(value.ExpectedViolationIDs) || !policyRules(value.ReportedViolationIDs) {
		return value, invalid
	}
	if value.WorkflowToken != nil && !warehouseSessionPattern.MatchString(*value.WorkflowToken) {
		return value, invalid
	}
	if value.Evidence != nil && *value.Evidence != "tmp/local-experiment/policy-candidate-bear/"+request.Token {
		return value, invalid
	}
	if value.ErrorCode != nil && !policyErrorCode(*value.ErrorCode) {
		return value, invalid
	}
	if (request.Action == "status") != (value.Execution == "read-only") {
		return value, invalid
	}
	if value.Result == "not_started" && (request.Action != "check" || value.Execution != "not-started" ||
		value.Verification != "not-checked" || value.WorkflowToken != nil || value.Evidence != nil ||
		value.Admission != "not-observed" || value.ExpectedViolationIDs != nil || value.ReportedViolationIDs != nil) {
		return value, invalid
	}
	if value.Result == "passed" {
		if value.WorkflowToken == nil || value.Evidence == nil || value.ErrorCode != nil || value.Verification != "saved-evidence" ||
			(request.Action == "check" && value.Execution != "invoked") || value.ExpectedViolationIDs == nil || value.ReportedViolationIDs == nil {
			return value, invalid
		}
		switch value.Admission {
		case "allowed":
			if len(value.ExpectedViolationIDs) != 0 || len(value.ReportedViolationIDs) != 0 ||
				!dataOneOf(value.ResponseContentCheck, "legacy-unrecorded", "producer-checked") {
				return value, invalid
			}
		case "denied":
			if len(value.ExpectedViolationIDs) == 0 || len(value.ReportedViolationIDs) == 0 || value.ResponseContentCheck != "not-applicable" {
				return value, invalid
			}
			for _, reported := range value.ReportedViolationIDs {
				if !dataOneOf(reported, value.ExpectedViolationIDs...) {
					return value, invalid
				}
			}
		default:
			return value, invalid
		}
	} else if value.ResponseContentCheck != "unknown" {
		return value, invalid
	}
	return value, nil
}

// The shared warehouse protocol intentionally forbids arrays. Policy has two
// bounded rule arrays, so check top-level duplicates here; typed decoding below
// rejects objects/nested arrays in every declared field.
func policyJSONFields(line []byte) (map[string]json.RawMessage, error) {
	invalid := errors.New("policy response object invalid")
	decoder := json.NewDecoder(bytes.NewReader(line))
	first, err := decoder.Token()
	if err != nil || first != json.Delim('{') {
		return nil, invalid
	}
	fields := make(map[string]json.RawMessage)
	for decoder.More() {
		token, err := decoder.Token()
		key, ok := token.(string)
		if err != nil || !ok {
			return nil, invalid
		}
		if _, duplicate := fields[key]; duplicate {
			return nil, invalid
		}
		var raw json.RawMessage
		if decoder.Decode(&raw) != nil {
			return nil, invalid
		}
		fields[key] = raw
	}
	last, err := decoder.Token()
	if err != nil || last != json.Delim('}') {
		return nil, invalid
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, invalid
	}
	return fields, nil
}

func policyRules(values []string) bool {
	for i, value := range values {
		if !dataOneOf(value, "P01", "P02", "P03", "P04", "P05", "P06") || (i > 0 && values[i-1] >= value) {
			return false
		}
	}
	return true
}

func policyErrorCode(value string) bool {
	return dataOneOf(value, "ARGUMENTS_INVALID", "EVIDENCE_TOKEN_INVALID", "ENVIRONMENT_NOT_READY", "INPUT_INVALID", "POLICY_NOT_FOUND", "POLICY_BUSY", "POLICY_ALREADY_EXISTS", "POLICY_JOURNAL_INVALID", "POLICY_EVIDENCE_INVALID", "POLICY_CHECK_FAILED", "INTERRUPTED")
}
