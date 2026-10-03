package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/spf13/cobra"
)

const doctorSchemaVersion = "1"

const (
	kubernetesCheckTimeout = 15 * time.Second
	policyCheckTimeout     = 2 * time.Minute
	commandWaitDelay       = 2 * time.Second
	maxReportedResources   = 10
	maxDoctorCommandOutput = 1 << 20
)

var localFluxGitRevision = regexp.MustCompile(`^main@sha1:[0-9a-f]{40}$`)
var localFluxDNSLabel = regexp.MustCompile(`^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$`)
var localDeletionTimestamp = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$`)

func localFluxValidName(name string, maxLength int, subdomain bool) bool {
	if len(name) == 0 || len(name) > maxLength {
		return false
	}
	labels := strings.Split(name, ".")
	if !subdomain && len(labels) != 1 {
		return false
	}
	for _, label := range labels {
		if len(label) > 63 || !localFluxDNSLabel.MatchString(label) {
			return false
		}
	}
	return true
}

type commandRunner interface {
	Run(context.Context, string, ...string) ([]byte, error)
}

type execRunner struct {
	waitDelay time.Duration
}

type doctorOutput struct {
	buffer    bytes.Buffer
	overLimit bool
}

func (output *doctorOutput) Write(data []byte) (int, error) {
	if output.buffer.Len()+len(data) > maxDoctorCommandOutput {
		output.overLimit = true
		return 0, fmt.Errorf("doctor command output exceeded limit")
	}
	return output.buffer.Write(data)
}

func (runner execRunner) Run(ctx context.Context, name string, args ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, name, args...)
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	command.Cancel = func() error {
		if command.Process == nil {
			return os.ErrProcessDone
		}
		err := syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
		if errors.Is(err, syscall.ESRCH) {
			return os.ErrProcessDone
		}
		return err
	}
	command.WaitDelay = runner.waitDelay
	if command.WaitDelay <= 0 {
		command.WaitDelay = commandWaitDelay
	}
	var output doctorOutput
	command.Stdout = &output
	command.Stderr = io.Discard
	err := command.Run()
	if output.overLimit {
		return nil, fmt.Errorf("doctor command output exceeded limit")
	}
	if err != nil {
		return nil, err
	}
	return output.buffer.Bytes(), nil
}

type doctorCheck struct {
	Name    string `json:"name"`
	Status  string `json:"status"`
	Summary string `json:"summary"`
}

type doctorReport struct {
	SchemaVersion string        `json:"schema_version"`
	GeneratedAt   string        `json:"generated_at"`
	Profile       string        `json:"profile"`
	Context       string        `json:"context"`
	Status        string        `json:"status"`
	Checks        []doctorCheck `json:"checks"`
}

type kubernetesResourceList struct {
	Kind       string `json:"kind"`
	APIVersion string `json:"apiVersion"`
	Metadata   struct {
		Continue           string  `json:"continue"`
		RemainingItemCount *int64  `json:"remainingItemCount"`
		ResourceVersion    *string `json:"resourceVersion"`
	} `json:"metadata"`
	Items []struct {
		Kind       string `json:"kind"`
		APIVersion string `json:"apiVersion"`
		Metadata   struct {
			Name              string `json:"name"`
			Namespace         string `json:"namespace"`
			UID               string `json:"uid"`
			Generation        int64  `json:"generation"`
			DeletionTimestamp string `json:"deletionTimestamp"`
		} `json:"metadata"`
		Spec struct {
			Suspend bool   `json:"suspend"`
			Paused  bool   `json:"paused"`
			Path    string `json:"path"`
			Prune   bool   `json:"prune"`
			Wait    bool   `json:"wait"`
			Ref     struct {
				Branch string `json:"branch"`
				Tag    string `json:"tag"`
				SemVer string `json:"semver"`
				Commit string `json:"commit"`
				Name   string `json:"name"`
			} `json:"ref"`
			URL       string `json:"url"`
			SecretRef struct {
				Name string `json:"name"`
			} `json:"secretRef"`
			Replicas  *int `json:"replicas"`
			SourceRef struct {
				Kind      string `json:"kind"`
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"sourceRef"`
		} `json:"spec"`
		Status struct {
			Replicas            int    `json:"replicas"`
			AvailableReplicas   int    `json:"availableReplicas"`
			UnavailableReplicas *int   `json:"unavailableReplicas"`
			ReadyReplicas       int    `json:"readyReplicas"`
			UpdatedReplicas     int    `json:"updatedReplicas"`
			ObservedGeneration  *int64 `json:"observedGeneration"`
			LastAppliedRevision string `json:"lastAppliedRevision"`
			Artifact            struct {
				Revision string `json:"revision"`
			} `json:"artifact"`
			Conditions []struct {
				Type               string `json:"type"`
				Status             string `json:"status"`
				ObservedGeneration int64  `json:"observedGeneration"`
			} `json:"conditions"`
		} `json:"status"`
	} `json:"items"`
}

// A local read is a single, complete Kubernetes v1 List, not an arbitrary
// projection that happens to include the expected objects.
func localInventoryIncomplete(list kubernetesResourceList, raw []byte) bool {
	// encoding/json silently replaces malformed UTF-8. Do not turn a corrupt
	// Kubernetes response into an apparently complete fixed-profile inventory.
	if !utf8.Valid(raw) {
		return true
	}
	// Keep omitted metadata compatible with projected fixtures, but an explicit
	// null envelope cannot establish a complete Kubernetes list.
	var envelope struct {
		Metadata   json.RawMessage `json:"metadata"`
		Kind       json.RawMessage `json:"kind"`
		APIVersion json.RawMessage `json:"apiVersion"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil || string(envelope.Metadata) == "null" ||
		string(envelope.Kind) == "null" || string(envelope.APIVersion) == "null" || !localInventoryUniqueFields(raw) {
		return true
	}
	// encoding/json matches struct field names case-insensitively. A Kubernetes
	// List's canonical envelope must not be satisfied by aliases such as Items.
	var topLevel map[string]json.RawMessage
	if err := json.Unmarshal(raw, &topLevel); err != nil {
		return true
	}
	if _, ok := topLevel["items"]; !ok {
		return true
	}
	for key := range topLevel {
		for _, canonical := range []string{"items", "metadata", "kind", "apiVersion"} {
			if key != canonical && strings.EqualFold(key, canonical) {
				return true
			}
		}
	}
	if len(envelope.Metadata) != 0 {
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(envelope.Metadata, &fields); err != nil {
			return true
		}
		// A differently cased pagination/version field is not an omitted field:
		// encoding/json would still populate the corresponding struct member.
		for field := range fields {
			for _, canonical := range []string{"resourceVersion", "remainingItemCount", "continue"} {
				if field != canonical && strings.EqualFold(field, canonical) {
					return true
				}
			}
		}
		for _, key := range []string{"resourceVersion", "remainingItemCount", "continue"} {
			if string(fields[key]) == "null" {
				return true
			}
		}
	}
	// The same decoder also accepts aliases in item identities. A fixed-profile
	// inventory must not treat Name/Generation as canonical Kubernetes metadata.
	var items struct {
		Items []map[string]json.RawMessage `json:"items"`
	}
	if err := json.Unmarshal(raw, &items); err != nil {
		return true
	}
	seenUIDs := make(map[string]struct{})
	for _, item := range items.Items {
		if item == nil {
			return true // A null member is not a Kubernetes resource.
		}
		// An escaped unpaired surrogate can become U+FFFD even when the raw
		// response is valid UTF-8. Do not accept lossy object identities.
		if localInventoryLossyStrings(item, []string{"kind", "apiVersion"}) {
			return true
		}
		// Explicit null is not an omitted field in a Kubernetes object. In
		// particular, a null spec/status on an unrelated item must not be
		// mistaken for a valid member of a complete namespace inventory.
		for _, key := range []string{"kind", "apiVersion", "metadata", "spec", "status"} {
			if string(item[key]) == "null" {
				return true
			}
		}
		for field := range item {
			for _, canonical := range []string{"kind", "apiVersion", "metadata", "spec", "status"} {
				if field != canonical && strings.EqualFold(field, canonical) {
					return true
				}
			}
		}
		var metadata map[string]json.RawMessage
		if err := json.Unmarshal(item["metadata"], &metadata); err != nil && len(item["metadata"]) != 0 {
			return true
		}
		// Distinct Kubernetes objects cannot share a UID in one complete list.
		// Older projected fixtures omit UIDs, so only bind reported values.
		if rawUID, ok := metadata["uid"]; ok {
			var uid string
			if err := json.Unmarshal(rawUID, &uid); err != nil || uid == "" || len(uid) > 1024 ||
				strings.IndexFunc(uid, func(r rune) bool { return r == utf8.RuneError || unicode.IsSpace(r) || unicode.Is(unicode.C, r) }) >= 0 {
				return true
			}
			if _, exists := seenUIDs[uid]; exists {
				return true
			}
			seenUIDs[uid] = struct{}{}
		}
		for field := range metadata {
			for _, canonical := range []string{"name", "namespace", "generation", "deletionTimestamp", "uid"} {
				if field != canonical && strings.EqualFold(field, canonical) {
					return true
				}
			}
		}
		if localInventoryNullFields(metadata, []string{"name", "namespace", "generation", "deletionTimestamp"}) {
			return true
		}
		// An escaped unpaired surrogate decodes to U+FFFD in Go. Neither a
		// Kubernetes resource name nor namespace can have that lossy identity.
		for _, key := range []string{"name", "namespace"} {
			if value, ok := metadata[key]; ok {
				var identity string
				if err := json.Unmarshal(value, &identity); err != nil || strings.ContainsRune(identity, utf8.RuneError) {
					return true
				}
			}
		}
		var identity struct {
			Generation int64 `json:"generation"`
		}
		if err := json.Unmarshal(item["metadata"], &identity); err != nil && len(item["metadata"]) != 0 {
			return true
		}
		// Even an unrelated list member cannot have a negative Kubernetes
		// generation. Zero remains compatible with projected fixture members.
		if identity.Generation < 0 {
			return true
		}
		// A reported deletion timestamp is a Kubernetes nanosecond-precision
		// RFC3339 time, not arbitrary text. An unrelated terminating member is still part of
		// the supposedly complete inventory.
		if rawTimestamp, ok := metadata["deletionTimestamp"]; ok {
			var timestamp string
			if err := json.Unmarshal(rawTimestamp, &timestamp); err != nil {
				return true
			}
			if timestamp != "" {
				if !localDeletionTimestamp.MatchString(timestamp) {
					return true
				}
				if _, err := time.Parse(time.RFC3339Nano, timestamp); err != nil {
					return true
				}
			}
		}
		// Nested declared fields must also be canonical: encoding/json accepts
		// Spec.Ref.Branch or Status.Conditions as if they were Kubernetes keys.
		for _, nested := range []struct {
			parent string
			keys   []string
		}{
			{"spec", []string{"suspend", "paused", "path", "prune", "wait", "ref", "url", "secretRef", "replicas", "sourceRef"}},
			{"status", []string{"replicas", "availableReplicas", "unavailableReplicas", "readyReplicas", "updatedReplicas", "observedGeneration", "lastAppliedRevision", "artifact", "conditions"}},
		} {
			if !localInventoryCanonicalFields(item[nested.parent], nested.keys) {
				return true
			}
		}
		var spec, status map[string]json.RawMessage
		_ = json.Unmarshal(item["spec"], &spec)
		_ = json.Unmarshal(item["status"], &status)
		// Replica counts cannot be negative even on unrelated list members.
		// Without this check a complete inventory can contain contradictory
		// workloads while the expected objects alone appear healthy.
		for _, fields := range []struct {
			object map[string]json.RawMessage
			keys   []string
		}{
			{spec, []string{"replicas"}},
			{status, []string{"replicas", "availableReplicas", "unavailableReplicas", "readyReplicas", "updatedReplicas"}},
		} {
			for _, key := range fields.keys {
				if rawCount, ok := fields.object[key]; ok && string(rawCount) != "null" {
					var count int64
					if err := json.Unmarshal(rawCount, &count); err != nil || count < 0 {
						return true
					}
				}
			}
		}
		// Rolling updates may temporarily exceed desired replicas, but a
		// reported ready/updated/available count cannot exceed the reported
		// total. Omitted counters in older projected fixtures stay optional.
		if rawTotal, ok := status["replicas"]; ok {
			var total int64
			if err := json.Unmarshal(rawTotal, &total); err != nil {
				return true
			}
			for _, key := range []string{"readyReplicas", "updatedReplicas", "availableReplicas"} {
				if rawCount, present := status[key]; present {
					var count int64
					if err := json.Unmarshal(rawCount, &count); err != nil || count > total {
						return true
					}
				}
			}
		}
		// Available pods are a subset of ready pods in one reported status.
		// Only compare explicitly reported fields; omitted fixture counters are
		// not equivalent to an observed zero.
		if rawAvailable, ok := status["availableReplicas"]; ok {
			if rawReady, present := status["readyReplicas"]; present {
				var available, ready int64
				if err := json.Unmarshal(rawAvailable, &available); err != nil {
					return true
				}
				if err := json.Unmarshal(rawReady, &ready); err != nil || available > ready {
					return true
				}
			}
		}
		// Unavailable replicas are the pods still required to reach the
		// declared capacity; rolling updates can add total pods but cannot
		// require more unavailable pods than the desired count.
		if rawDesired, ok := spec["replicas"]; ok {
			if rawUnavailable, present := status["unavailableReplicas"]; present {
				var desired, unavailable int64
				if err := json.Unmarshal(rawDesired, &desired); err != nil {
					return true
				}
				if err := json.Unmarshal(rawUnavailable, &unavailable); err != nil || unavailable > desired {
					return true
				}
				// Kubernetes counts unavailable against desired, not the rolling
				// total. Available pods and unavailable desired capacity cannot
				// overlap, even while a rollout has surplus ready pods.
				if rawAvailable, present := status["availableReplicas"]; present {
					var available int64
					if err := json.Unmarshal(rawAvailable, &available); err != nil ||
						(available >= desired && unavailable != 0) ||
						(available < desired && unavailable > desired-available) {
						return true
					}
				}
			}
		}
		var observed struct {
			Generation *int64 `json:"observedGeneration"`
		}
		if len(item["status"]) != 0 {
			if err := json.Unmarshal(item["status"], &observed); err != nil {
				return true
			}
		}
		if observed.Generation != nil && (*observed.Generation < 0 ||
			(identity.Generation > 0 && *observed.Generation > identity.Generation)) {
			return true
		}
		if localInventoryNullFields(spec, []string{"suspend", "paused", "path", "prune", "wait", "url", "replicas"}) ||
			localInventoryNullFields(status, []string{"replicas", "availableReplicas", "unavailableReplicas", "readyReplicas", "updatedReplicas", "observedGeneration", "lastAppliedRevision", "conditions"}) {
			return true
		}
		if localInventoryLossyStrings(status, []string{"lastAppliedRevision"}) {
			return true
		}
		for _, nested := range []struct {
			raw  json.RawMessage
			keys []string
		}{
			{spec["ref"], []string{"branch", "tag", "semver", "commit", "name"}},
			{spec["secretRef"], []string{"name"}},
			{spec["sourceRef"], []string{"kind", "name", "namespace"}},
			{status["artifact"], []string{"revision"}},
		} {
			// An explicitly null nested object decodes like an omitted struct in
			// encoding/json. Only omission is compatible with projected fixtures.
			if string(nested.raw) == "null" || !localInventoryCanonicalFields(nested.raw, nested.keys) {
				return true
			}
			var fields map[string]json.RawMessage
			if len(nested.raw) != 0 {
				if err := json.Unmarshal(nested.raw, &fields); err != nil || localInventoryNullFields(fields, nested.keys) {
					return true
				}
			}
		}
		// References on unrelated members are still part of the claimed
		// complete list. Preserve their reported identity without lossy decode.
		for _, reference := range []struct {
			raw  json.RawMessage
			keys []string
		}{
			{spec["ref"], []string{"branch", "tag", "semver", "commit", "name"}},
			{spec["secretRef"], []string{"name"}},
			{spec["sourceRef"], []string{"kind", "name", "namespace"}},
			{status["artifact"], []string{"revision"}},
		} {
			if len(reference.raw) == 0 {
				continue
			}
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(reference.raw, &fields); err != nil || localInventoryLossyStrings(fields, reference.keys) {
				return true
			}
		}
		var conditions []json.RawMessage
		_ = json.Unmarshal(status["conditions"], &conditions)
		conditionTypes := make(map[string]struct{}, len(conditions))
		for _, condition := range conditions {
			if string(condition) == "null" {
				return true // A null entry cannot describe a condition.
			}
			if !localInventoryCanonicalFields(condition, []string{"type", "status", "observedGeneration"}) {
				return true
			}
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(condition, &fields); err != nil ||
				localInventoryNullFields(fields, []string{"type", "status", "observedGeneration"}) {
				return true
			}
			var shape struct {
				Type   string `json:"type"`
				Status string `json:"status"`
			}
			if err := json.Unmarshal(condition, &shape); err != nil || shape.Type == "" ||
				(shape.Status != "True" && shape.Status != "False" && shape.Status != "Unknown") {
				return true
			}
			if _, duplicate := conditionTypes[shape.Type]; duplicate {
				return true // An unrelated member also belongs to this complete inventory.
			}
			conditionTypes[shape.Type] = struct{}{}
			// A condition on an unrelated item still belongs to this claimed
			// complete inventory. Do not accept invisible or unbounded types, or
			// a generation that could not yet belong to its resource.
			if len(shape.Type) > 316 || strings.IndexFunc(shape.Type, func(r rune) bool {
				return r == utf8.RuneError || unicode.IsSpace(r) || unicode.Is(unicode.C, r)
			}) >= 0 {
				return true
			}
			var generation struct {
				ObservedGeneration *int64 `json:"observedGeneration"`
			}
			if err := json.Unmarshal(condition, &generation); err != nil ||
				(generation.ObservedGeneration != nil && (*generation.ObservedGeneration < 0 ||
					(identity.Generation > 0 && *generation.ObservedGeneration > identity.Generation) ||
					(observed.Generation != nil && *generation.ObservedGeneration > *observed.Generation))) {
				return true
			}
		}
	}
	return list.Items == nil || list.Metadata.Continue != "" ||
		(list.Metadata.RemainingItemCount != nil && *list.Metadata.RemainingItemCount != 0) ||
		(list.Metadata.ResourceVersion != nil && (len(*list.Metadata.ResourceVersion) == 0 ||
			len(*list.Metadata.ResourceVersion) > 1024 || strings.IndexFunc(*list.Metadata.ResourceVersion, func(r rune) bool {
			return r == utf8.RuneError || unicode.IsSpace(r) || unicode.Is(unicode.C, r)
		}) >= 0)) ||
		(list.Kind != "" && list.Kind != "List") ||
		(list.APIVersion != "" && list.APIVersion != "v1")
}

func localInventoryNullFields(fields map[string]json.RawMessage, keys []string) bool {
	for _, key := range keys {
		if string(fields[key]) == "null" {
			return true
		}
	}
	return false
}

func localInventoryLossyStrings(fields map[string]json.RawMessage, keys []string) bool {
	for _, key := range keys {
		if raw, ok := fields[key]; ok {
			var value string
			if err := json.Unmarshal(raw, &value); err != nil || strings.ContainsRune(value, utf8.RuneError) {
				return true
			}
		}
	}
	return false
}

func localInventoryCanonicalFields(raw json.RawMessage, keys []string) bool {
	if len(raw) == 0 || string(raw) == "null" {
		return true // Existing optional-field and null handling remains with the caller.
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return false
	}
	for field := range fields {
		for _, key := range keys {
			if field != key && strings.EqualFold(field, key) {
				return false
			}
		}
	}
	return true
}

// encoding/json accepts duplicate object members and keeps the later value.
// A diagnostic inventory must not interpret two conflicting versions of an
// identity, condition, or pagination field as a complete observation.
func localInventoryUniqueFields(raw []byte) bool {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	var value func() bool
	value = func() bool {
		token, err := decoder.Token()
		if err != nil {
			return false
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return true
		}
		switch delim {
		case '{':
			seen := make(map[string]struct{})
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return false
				}
				name, ok := key.(string)
				if !ok {
					return false
				}
				if _, exists := seen[name]; exists {
					return false
				}
				seen[name] = struct{}{}
				if !value() {
					return false
				}
			}
		case '[':
			for decoder.More() {
				if !value() {
					return false
				}
			}
		default:
			return false
		}
		end, err := decoder.Token()
		closing := json.Delim('}')
		if delim == '[' {
			closing = ']'
		}
		return err == nil && end == closing
	}
	if !value() {
		return false
	}
	_, err := decoder.Token()
	return err == io.EOF
}

func newDoctorCommand(runner commandRunner) *cobra.Command {
	var outputFormat string
	var profile string
	var kubeContext string
	var repositoryRoot string

	command := &cobra.Command{
		Use:   "doctor",
		Short: "Report bounded cluster, Flux, and policy diagnostics",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if outputFormat != "json" && outputFormat != "markdown" {
				return fmt.Errorf("unsupported output format %q (use json or markdown)", outputFormat)
			}
			if profile == localExperimentProfile && kubeContext != localExperimentContext {
				return fmt.Errorf("profile %s requires context %s", localExperimentProfile, localExperimentContext)
			}
			var resolvedRoot string
			if profile != localExperimentProfile {
				var err error
				resolvedRoot, err = resolveRepositoryRoot(repositoryRoot)
				if err != nil {
					return err
				}
			}
			report := runDoctor(cmd.Context(), runner, profile, kubeContext, resolvedRoot, time.Now().UTC())
			if outputFormat == "json" {
				encoder := json.NewEncoder(cmd.OutOrStdout())
				encoder.SetIndent("", "  ")
				return encoder.Encode(report)
			}
			return writeDoctorMarkdown(cmd.OutOrStdout(), report)
		},
	}
	command.Flags().StringVar(&outputFormat, "output", "markdown", "output format: json or markdown")
	command.Flags().StringVar(&profile, "profile", "local-lite", "diagnostic profile: local-lite or local-experiment")
	command.Flags().StringVar(&kubeContext, "context", "colima-mac-studio-solo", "Kubernetes context")
	command.Flags().StringVar(&repositoryRoot, "repo-root", "", "repository root (auto-detected from the current directory)")
	return command
}

func resolveRepositoryRoot(explicit string) (string, error) {
	start := explicit
	if start == "" {
		var err error
		start, err = os.Getwd()
		if err != nil {
			return "", fmt.Errorf("determine current directory: %w", err)
		}
	}
	current, err := filepath.Abs(start)
	if err != nil {
		return "", fmt.Errorf("resolve repository root %q: %w", start, err)
	}
	for {
		if regularFile(filepath.Join(current, "Makefile")) && regularFile(filepath.Join(current, "security", "resident-images.json")) {
			return current, nil
		}
		parent := filepath.Dir(current)
		if parent == current || explicit != "" {
			break
		}
		current = parent
	}
	return "", fmt.Errorf("Shirokuma repository root not found from %q; use --repo-root", start)
}

func regularFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func runDoctor(ctx context.Context, runner commandRunner, profile, kubeContext, repositoryRoot string, now time.Time) doctorReport {
	if profile == localExperimentProfile {
		return runLocalExperimentDoctor(ctx, runner, kubeContext, now)
	}
	checks := []doctorCheck{
		checkCluster(ctx, runner, kubeContext),
		checkFlux(ctx, runner, kubeContext),
		checkPolicy(ctx, runner, repositoryRoot),
	}
	status := "healthy"
	for _, check := range checks {
		if check.Status != "healthy" {
			status = "degraded"
			break
		}
	}
	return doctorReport{
		SchemaVersion: doctorSchemaVersion,
		GeneratedAt:   now.Format(time.RFC3339),
		Profile:       profile,
		Context:       kubeContext,
		Status:        status,
		Checks:        checks,
	}
}

func checkCluster(ctx context.Context, runner commandRunner, kubeContext string) doctorCheck {
	return checkClusterForProfile(ctx, runner, kubeContext, false)
}

func checkClusterForProfile(ctx context.Context, runner commandRunner, kubeContext string, localExperiment bool) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	output, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "get", "--raw=/readyz")
	if err != nil {
		return failedCheck("cluster", err)
	}
	if (localExperiment && string(output) != "ok" && string(output) != "ok\n") ||
		(!localExperiment && strings.TrimSpace(string(output)) != "ok") {
		return doctorCheck{Name: "cluster", Status: "degraded", Summary: "readiness endpoint did not return ok"}
	}
	return doctorCheck{Name: "cluster", Status: "healthy", Summary: "Kubernetes readiness endpoint returned ok"}
}

func checkFlux(ctx context.Context, runner commandRunner, kubeContext string) doctorCheck {
	return checkFluxForProfile(ctx, runner, kubeContext, false)
}

func checkFluxForProfile(ctx context.Context, runner commandRunner, kubeContext string, localExperiment bool) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, kubernetesCheckTimeout)
	defer cancel()
	controllerOutput, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "-n", "flux-system", "get", "deployments.apps", "-l", "app.kubernetes.io/part-of=flux", "-o", "json")
	if err != nil {
		return failedCheck("flux", err)
	}
	var controllers kubernetesResourceList
	if err := json.Unmarshal(controllerOutput, &controllers); err != nil {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "controller response was not valid JSON"}
	}
	if localExperiment && localInventoryIncomplete(controllers, controllerOutput) {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "controller response was incomplete"}
	}
	if len(controllers.Items) != 4 {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("expected 4 Flux controllers, found %d", len(controllers.Items))}
	}
	requiredControllers := []string{"source-controller", "kustomize-controller", "helm-controller", "notification-controller"}
	foundControllers := make(map[string]bool, len(controllers.Items))
	for _, controller := range controllers.Items {
		foundControllers[controller.Metadata.Name] = true
	}
	var missingControllers []string
	for _, name := range requiredControllers {
		if !foundControllers[name] {
			missingControllers = append(missingControllers, name)
		}
	}
	if len(missingControllers) > 0 {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("missing required Flux controllers: %s", strings.Join(missingControllers, ", "))}
	}
	var unhealthy []string
	for _, controller := range controllers.Items {
		unready := controller.Status.Replicas < 1 || controller.Status.AvailableReplicas < controller.Status.Replicas
		if localExperiment {
			unready = unready || (controller.APIVersion != "" && controller.APIVersion != "apps/v1") ||
				controller.Kind != "Deployment" || controller.Metadata.Namespace != "flux-system" ||
				controller.Spec.Paused ||
				controller.Metadata.DeletionTimestamp != "" ||
				controller.Metadata.Generation < 1 || controller.Status.ObservedGeneration == nil || *controller.Status.ObservedGeneration != controller.Metadata.Generation ||
				controller.Spec.Replicas == nil || *controller.Spec.Replicas < 1 ||
				controller.Status.Replicas != *controller.Spec.Replicas ||
				controller.Status.AvailableReplicas != *controller.Spec.Replicas ||
				controller.Status.ReadyReplicas != *controller.Spec.Replicas ||
				controller.Status.UpdatedReplicas != *controller.Spec.Replicas
			if controller.Status.UnavailableReplicas != nil && *controller.Status.UnavailableReplicas != 0 {
				unready = true
			}
			available := false
			progressing := false
			conditionTypes := make(map[string]bool)
			for _, condition := range controller.Status.Conditions {
				if condition.Type == "" || (condition.Status != "True" && condition.Status != "False" && condition.Status != "Unknown") {
					unready = true
				}
				if conditionTypes[condition.Type] {
					unready = true
				}
				conditionTypes[condition.Type] = true
				if condition.Type == "Available" && condition.Status == "True" {
					available = true
				}
				if condition.Type == "Progressing" && condition.Status == "True" {
					progressing = true
				}
				if (condition.Type == "ReplicaFailure" && (condition.Status == "True" || (localExperiment && condition.Status != "False"))) ||
					(condition.Type == "Progressing" && condition.Status != "True") ||
					(condition.Type == "Available" && condition.Status != "True") {
					unready = true
				}
			}
			unready = unready || !available || !progressing
		}
		if unready {
			unhealthy = append(unhealthy, "Deployment/"+controller.Metadata.Name)
		}
	}

	resourceOutput, err := runner.Run(checkContext, "kubectl", "--context", kubeContext, "get", "gitrepositories.source.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io,buckets.source.toolkit.fluxcd.io,helmrepositories.source.toolkit.fluxcd.io,helmcharts.source.toolkit.fluxcd.io,kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json")
	if err != nil {
		return failedCheck("flux", err)
	}
	var resources kubernetesResourceList
	if err := json.Unmarshal(resourceOutput, &resources); err != nil {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "resource response was not valid JSON"}
	}
	if localExperiment && localInventoryIncomplete(resources, resourceOutput) {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "resource response was incomplete"}
	}
	if localExperiment {
		controllerUIDs := make(map[string]bool)
		for _, controller := range controllers.Items {
			if controller.Metadata.UID != "" {
				controllerUIDs[controller.Metadata.UID] = true
			}
		}
		for _, resource := range resources.Items {
			if resource.Metadata.UID != "" && controllerUIDs[resource.Metadata.UID] {
				return doctorCheck{Name: "flux", Status: "degraded", Summary: "controller and resource responses reused a resource UID"}
			}
		}
	}
	kinds := map[string]int{}
	seenResources := map[string]bool{}
	rootCount, rootSourceCount, devCount := 0, 0, 0
	rootSuspended := false
	sourceRevision, devRevision, rootRevision := "", "", ""
	rootActive := false
	for _, resource := range resources.Items {
		kinds[resource.Kind]++
		resourceID := fmt.Sprintf("%s/%s/%s", resource.Kind, resource.Metadata.Namespace, resource.Metadata.Name)
		if localExperiment {
			// Do not echo arbitrary API fields in a human-facing diagnostic.
			// Even malformed resource names may contain credentials or controls.
			validIdentity := localFluxValidName(resource.Metadata.Name, 253, true) &&
				localFluxValidName(resource.Metadata.Namespace, 63, false)
			switch resource.Kind {
			case "GitRepository", "OCIRepository", "Bucket", "HelmRepository", "HelmChart", "Kustomization", "HelmRelease":
			default:
				unhealthy = append(unhealthy, "Flux resource (unexpected kind)")
				validIdentity = false
			}
			if !validIdentity {
				unhealthy = append(unhealthy, "Flux resource (identity unverified)")
				resourceID = "Flux resource (identity unverified)"
			} else if seenResources[resourceID] {
				unhealthy = append(unhealthy, resourceID+" (duplicated)")
			}
			// The queried CRDs are pinned to v1, except HelmRelease (v2).
			// An omitted version is compatible with projected test inventories.
			expectedVersion := "source.toolkit.fluxcd.io/v1"
			switch resource.Kind {
			case "Kustomization":
				expectedVersion = "kustomize.toolkit.fluxcd.io/v1"
			case "HelmRelease":
				expectedVersion = "helm.toolkit.fluxcd.io/v2"
			}
			if validIdentity && resource.APIVersion != "" && resource.APIVersion != expectedVersion {
				unhealthy = append(unhealthy, resourceID+" (declared API version mismatch)")
			}
			seenResources[resourceID] = true
			if resource.Metadata.Generation < 1 {
				unhealthy = append(unhealthy, resourceID+" (generation unverified)")
			}
			if resource.Status.ObservedGeneration != nil && *resource.Status.ObservedGeneration != resource.Metadata.Generation {
				unhealthy = append(unhealthy, resourceID+" (status generation mismatch)")
			}
		}
		isRoot := resource.Kind == "Kustomization" && resource.Metadata.Namespace == "flux-system" && resource.Metadata.Name == "flux-system"
		isRootSource := resource.Kind == "GitRepository" && resource.Metadata.Namespace == "flux-system" && resource.Metadata.Name == "flux-system"
		isDev := resource.Kind == "Kustomization" && resource.Metadata.Namespace == "flux-system" && resource.Metadata.Name == "shirokuma-dev"
		if isRoot {
			rootCount++
			rootRevision = resource.Status.LastAppliedRevision
			rootActive = !resource.Spec.Suspend
			if localExperiment && !resource.Spec.Prune {
				unhealthy = append(unhealthy, resourceID+" (prune disabled)")
			}
			if localExperiment && resource.Spec.Path != "./deploy/gitops/clusters/local-lite" {
				unhealthy = append(unhealthy, resourceID+" (path mismatch)")
			}
			if localExperiment && (resource.Spec.SourceRef.Kind != "GitRepository" || resource.Spec.SourceRef.Name != "flux-system" ||
				(resource.Spec.SourceRef.Namespace != "" && resource.Spec.SourceRef.Namespace != "flux-system")) {
				unhealthy = append(unhealthy, resourceID+" (source reference mismatch)")
			}
		}
		if isRootSource {
			rootSourceCount++
			sourceRevision = resource.Status.Artifact.Revision
			if localExperiment && resource.Spec.Suspend {
				unhealthy = append(unhealthy, resourceID+" (declared source suspended)")
			}
			if localExperiment && resource.Spec.Ref.Branch != "main" {
				unhealthy = append(unhealthy, resourceID+" (declared branch mismatch)")
			}
			if localExperiment && (resource.Spec.Ref.Tag != "" || resource.Spec.Ref.SemVer != "" || resource.Spec.Ref.Commit != "" || resource.Spec.Ref.Name != "") {
				unhealthy = append(unhealthy, resourceID+" (competing source revision selector)")
			}
			if localExperiment && resource.Spec.URL != "ssh://git@github.com/TommyKammy/Shirokuma" {
				unhealthy = append(unhealthy, resourceID+" (declared origin mismatch)")
			}
			if localExperiment && resource.Spec.SecretRef.Name != "flux-system" {
				unhealthy = append(unhealthy, resourceID+" (declared credentials reference mismatch)")
			}
			if localExperiment && !localFluxGitRevision.MatchString(resource.Status.Artifact.Revision) {
				unhealthy = append(unhealthy, resourceID+" (revision unverified)")
			}
		}
		if localExperiment && isDev {
			devCount++
			devRevision = resource.Status.LastAppliedRevision
			if !resource.Spec.Wait {
				unhealthy = append(unhealthy, resourceID+" (wait disabled)")
			}
			if !resource.Spec.Prune {
				unhealthy = append(unhealthy, resourceID+" (prune disabled)")
			}
			if resource.Spec.Path != "./deploy/gitops/dev" {
				unhealthy = append(unhealthy, resourceID+" (path mismatch)")
			}
			if resource.Spec.SourceRef.Kind != "GitRepository" || resource.Spec.SourceRef.Name != "flux-system" ||
				(resource.Spec.SourceRef.Namespace != "" && resource.Spec.SourceRef.Namespace != "flux-system") {
				unhealthy = append(unhealthy, resourceID+" (source reference mismatch)")
			}
		}
		if localExperiment && resource.Metadata.DeletionTimestamp != "" {
			unhealthy = append(unhealthy, resourceID+" (terminating)")
		}
		// Suspension relaxes Ready freshness, not a current-generation failure.
		if localExperiment {
			conditionTypes := make(map[string]bool)
			for _, condition := range resource.Status.Conditions {
				if condition.Type == "" || (condition.Status != "True" && condition.Status != "False" && condition.Status != "Unknown") {
					unhealthy = append(unhealthy, resourceID+" (incomplete condition)")
				}
				// Kubernetes conditions are keyed by type. A duplicated type is
				// ambiguous even when the reported statuses happen to agree.
				if conditionTypes[condition.Type] {
					unhealthy = append(unhealthy, resourceID+" (duplicated condition type)")
				}
				conditionTypes[condition.Type] = true
				if condition.ObservedGeneration == resource.Metadata.Generation &&
					(condition.Type == "Stalled" || condition.Type == "Reconciling") && condition.Status != "False" {
					unhealthy = append(unhealthy, resourceID+" (unresolved reconciliation condition)")
				}
			}
		}
		if localExperiment && isRoot && resource.Spec.Suspend && resource.Metadata.DeletionTimestamp == "" {
			// A stale Ready condition is expected while suspended, but an
			// explicitly failed current generation is not healthy history.
			for _, condition := range resource.Status.Conditions {
				if condition.Type == "Ready" && condition.ObservedGeneration == resource.Metadata.Generation && condition.Status != "True" {
					unhealthy = append(unhealthy, resourceID+" (conflicting Ready condition)")
				}
			}
			rootSuspended = true
			continue
		}
		if resource.Spec.Suspend {
			unhealthy = append(unhealthy, resourceID+" (suspended)")
			continue
		}
		ready := false
		for _, condition := range resource.Status.Conditions {
			if condition.Type == "Ready" && condition.ObservedGeneration == resource.Metadata.Generation {
				if condition.Status == "True" {
					ready = true
				} else if localExperiment {
					unhealthy = append(unhealthy, resourceID+" (conflicting Ready condition)")
				}
			}
		}
		if !ready {
			unhealthy = append(unhealthy, resourceID)
		}
	}
	if kinds["GitRepository"] == 0 || kinds["Kustomization"] == 0 {
		return doctorCheck{Name: "flux", Status: "degraded", Summary: "Flux GitRepository and Kustomization resources are required"}
	}
	if localExperiment && rootCount != 1 {
		unhealthy = append(unhealthy, "Kustomization/flux-system/flux-system (missing or duplicated)")
	}
	if localExperiment && rootSourceCount != 1 {
		unhealthy = append(unhealthy, "GitRepository/flux-system/flux-system (missing or duplicated)")
	}
	if localExperiment && devCount != 1 {
		unhealthy = append(unhealthy, "Kustomization/flux-system/shirokuma-dev (missing or duplicated)")
	}
	if localExperiment && devCount == 1 && rootSourceCount == 1 && (devRevision == "" || devRevision != sourceRevision) {
		unhealthy = append(unhealthy, "Kustomization/flux-system/shirokuma-dev (applied revision differs from source)")
	}
	if localExperiment && rootActive && rootCount == 1 && rootSourceCount == 1 && (rootRevision == "" || rootRevision != sourceRevision) {
		unhealthy = append(unhealthy, "Kustomization/flux-system/flux-system (applied revision differs from source)")
	}
	if len(unhealthy) > 0 {
		sort.Strings(unhealthy)
		reported := unhealthy
		if len(reported) > maxReportedResources {
			reported = reported[:maxReportedResources]
		}
		return doctorCheck{Name: "flux", Status: "degraded", Summary: fmt.Sprintf("%d Flux resource(s) failed readiness or fixed-source checks; first %d: %s", len(unhealthy), len(reported), strings.Join(reported, ", "))}
	}
	if rootSuspended {
		return doctorCheck{Name: "flux", Status: "informational", Summary: fmt.Sprintf("4 controllers and %d other reconciled resource(s) are Ready; root Kustomization/flux-system/flux-system is suspended in local-experiment (intent and source revision provenance not independently verified)", len(resources.Items)-1)}
	}
	return doctorCheck{Name: "flux", Status: "healthy", Summary: fmt.Sprintf("4 controllers and %d reconciled resource(s) are Ready", len(resources.Items))}
}

func checkPolicy(ctx context.Context, runner commandRunner, repositoryRoot string) doctorCheck {
	checkContext, cancel := context.WithTimeout(ctx, policyCheckTimeout)
	defer cancel()
	if _, err := runner.Run(checkContext, "make", "-C", repositoryRoot, "verify-security"); err != nil {
		return failedCheck("policy", err)
	}
	return doctorCheck{Name: "policy", Status: "healthy", Summary: "repository supply-chain policy passed"}
}

func failedCheck(name string, err error) doctorCheck {
	summary := "command could not be executed"
	if exitError, ok := err.(*exec.ExitError); ok {
		summary = fmt.Sprintf("command exited with status %d", exitError.ExitCode())
	}
	return doctorCheck{Name: name, Status: "degraded", Summary: summary}
}

func writeDoctorMarkdown(output io.Writer, report doctorReport) error {
	if _, err := fmt.Fprintf(output, "# Shirokuma doctor\n\n- Status: **%s**\n- Profile: `%s`\n- Context: `%s`\n- Generated: `%s`\n\n", report.Status, report.Profile, report.Context, report.GeneratedAt); err != nil {
		return err
	}
	for _, check := range report.Checks {
		if _, err := fmt.Fprintf(output, "- **%s**: %s — %s\n", check.Name, check.Status, check.Summary); err != nil {
			return err
		}
	}
	return nil
}
