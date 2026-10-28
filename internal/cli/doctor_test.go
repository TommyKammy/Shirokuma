package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

type fakeResponse struct {
	output string
	err    error
}

type fakeRunner struct {
	responses []fakeResponse
	calls     [][]string
}

func (runner *fakeRunner) Run(_ context.Context, name string, args ...string) ([]byte, error) {
	runner.calls = append(runner.calls, append([]string{name}, args...))
	response := runner.responses[len(runner.calls)-1]
	return []byte(response.output), response.err
}

func TestDoctorJSONHealthy(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{
		{output: "ok\n"},
		{output: `{"items":[{"kind":"Node","metadata":{"name":"colima"},"status":{"conditions":[{"type":"Ready","status":"True"}]}}]}`},
		{output: `{"items":[{"metadata":{"name":"source-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"kustomize-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"helm-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"notification-controller"},"status":{"replicas":1,"availableReplicas":1}}]}`},
		{output: `{"items":[{"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}},{"kind":"Kustomization","metadata":{"name":"flux-system","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}]}`},
		{output: "policy ok"},
	}}
	var output bytes.Buffer
	command := newRootCommand(&output, runner)
	command.SetArgs([]string{"doctor", "--output", "json"})
	if err := command.Execute(); err != nil {
		t.Fatalf("Execute() error = %v", err)
	}

	var report doctorReport
	if err := json.Unmarshal(output.Bytes(), &report); err != nil {
		t.Fatalf("doctor output is not JSON: %v", err)
	}
	if report.Status != "healthy" || len(report.Checks) != 3 {
		t.Fatalf("report = %#v", report)
	}
	wantCommands := []string{"kubectl", "kubectl", "kubectl", "kubectl", "make"}
	var gotCommands []string
	for _, call := range runner.calls {
		gotCommands = append(gotCommands, call[0])
	}
	if !reflect.DeepEqual(gotCommands, wantCommands) {
		t.Fatalf("commands = %v, want %v", gotCommands, wantCommands)
	}
	policyCall := runner.calls[4]
	if len(policyCall) != 4 || policyCall[1] != "-C" || policyCall[3] != "verify-security" {
		t.Fatalf("policy call = %v", policyCall)
	}
	resourceCall := strings.Join(runner.calls[3], " ")
	for _, source := range []string{
		"gitrepositories.source.toolkit.fluxcd.io",
		"ocirepositories.source.toolkit.fluxcd.io",
		"buckets.source.toolkit.fluxcd.io",
		"helmrepositories.source.toolkit.fluxcd.io",
		"helmcharts.source.toolkit.fluxcd.io",
	} {
		if !strings.Contains(resourceCall, source) {
			t.Fatalf("resource call %q does not include %q", resourceCall, source)
		}
	}
}

func TestDoctorRejectsInvalidOutputBeforeChecks(t *testing.T) {
	runner := &fakeRunner{}
	command := newRootCommand(&bytes.Buffer{}, runner)
	command.SetArgs([]string{"doctor", "--output", "yaml"})
	if err := command.Execute(); err == nil {
		t.Fatal("Execute() error = nil, want unsupported output error")
	}
	if len(runner.calls) != 0 {
		t.Fatalf("runner calls = %v, want none", runner.calls)
	}
}

func TestCheckFluxRequiresExactControllerNames(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{
		{output: `{"items":[{"metadata":{"name":"source-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"kustomize-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"helm-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"image-reflector-controller"},"status":{"replicas":1,"availableReplicas":1}}]}`},
	}}
	check := checkFlux(context.Background(), runner, "test")
	if check.Status != "degraded" || !strings.Contains(check.Summary, "notification-controller") {
		t.Fatalf("check = %#v, want missing notification-controller", check)
	}
}

func TestDoctorLocalFluxSourceAndChildren(t *testing.T) {
	checkFixture := func(t *testing.T, fixture localDoctorFixture, local bool) doctorCheck {
		t.Helper()
		runner := &fakeRunner{}
		for _, items := range [][]kubernetesResource{fixture.controllers, fixture.flux} {
			raw, err := json.Marshal(kubernetesResourceList{Items: items})
			if err != nil {
				t.Fatal(err)
			}
			runner.responses = append(runner.responses, fakeResponse{output: string(raw)})
		}
		check := checkFluxForProfile(context.Background(), runner, localExperimentContext, local)
		if !strings.Contains(strings.Join(runner.calls[1], " "), "buckets.source.toolkit.fluxcd.io") {
			t.Fatal("Flux inventory must fetch Buckets")
		}
		return check
	}
	for _, state := range []string{"missing", "unready", "stale", "missing observedGeneration", "suspended"} {
		t.Run("Bucket/"+state, func(t *testing.T) {
			fixture := healthyLocalDoctorFixture()
			switch state {
			case "missing":
				fixture.flux = fixture.flux[1:]
			case "unready":
				fixture.flux[0] = localTestCondition(fixture.flux[0], "Ready", "False")
			case "stale":
				fixture.flux[0].Metadata.Generation++
			case "missing observedGeneration":
				fixture.flux[0].Status.Conditions[0].ObservedGeneration = 0
			case "suspended":
				fixture.flux[0].Spec.Suspend = true
			}
			check := checkFixture(t, fixture, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, "Bucket/flux-system/shirokuma-local-source") {
				t.Fatalf("check = %#v", check)
			}
		})
	}
	for _, child := range []string{"shirokuma-dev", "shirokuma-object-storage", "shirokuma-catalog-database",
		"shirokuma-catalog-bootstrap", "shirokuma-catalog", "shirokuma-iceberg-bootstrap", "shirokuma-trino"} {
		for _, missing := range []bool{false, true} {
			t.Run(child+fmt.Sprint(missing), func(t *testing.T) {
				fixture := healthyLocalDoctorFixture()
				for i, resource := range fixture.flux {
					if resource.Metadata.Name == child {
						if missing {
							fixture.flux = append(fixture.flux[:i], fixture.flux[i+1:]...)
						} else {
							fixture.flux[i].Spec.Suspend = true
						}
						break
					}
				}
				check := checkFixture(t, fixture, true)
				if check.Status != "degraded" || !strings.Contains(check.Summary, child) {
					t.Fatalf("check = %#v", check)
				}
			})
		}
	}
	fixture := healthyLocalDoctorFixture()
	fixture.flux = append(fixture.flux, localTestCondition(localTestResource("GitRepository", "flux-system", "flux-system"), "Ready", "False"))
	if check := checkFixture(t, fixture, true); check.Status != "healthy" {
		t.Fatalf("legacy Git must not affect local source: %#v", check)
	}
	if check := checkFixture(t, fixture, false); check.Status != "degraded" || !strings.Contains(check.Summary, "GitRepository/flux-system/flux-system") {
		t.Fatalf("non-local profile must still check Git: %#v", check)
	}
}

func TestCheckFluxRejectsReadyConditionWithoutObservedGeneration(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{
		{output: `{"items":[{"metadata":{"name":"source-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"kustomize-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"helm-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"notification-controller"},"status":{"replicas":1,"availableReplicas":1}}]}`},
		{output: `{"items":[{"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":2},"status":{"conditions":[{"type":"Ready","status":"True"}]}},{"kind":"Kustomization","metadata":{"name":"flux-system","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}]}`},
	}}
	check := checkFlux(context.Background(), runner, "test")
	if check.Status != "degraded" || !strings.Contains(check.Summary, "GitRepository/flux-system/flux-system") {
		t.Fatalf("check = %#v, want stale GitRepository", check)
	}
}

func TestCheckFluxRejectsSuspendedResourceWithStaleReadyCondition(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{
		{output: `{"items":[{"metadata":{"name":"source-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"kustomize-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"helm-controller"},"status":{"replicas":1,"availableReplicas":1}},{"metadata":{"name":"notification-controller"},"status":{"replicas":1,"availableReplicas":1}}]}`},
		{output: `{"items":[{"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}},{"kind":"Kustomization","metadata":{"name":"shirokuma-dev","namespace":"flux-system","generation":1},"spec":{"suspend":true},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}]}`},
	}}
	check := checkFlux(context.Background(), runner, "test")
	if check.Status != "degraded" || !strings.Contains(check.Summary, "Kustomization/flux-system/shirokuma-dev (suspended)") {
		t.Fatalf("check = %#v, want suspended Kustomization", check)
	}
}

func TestExecRunnerParsesStdoutWithoutStderrWarnings(t *testing.T) {
	output, err := (execRunner{}).Run(context.Background(), "sh", "-c", "printf ok; printf warning >&2")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := string(output), "ok"; got != want {
		t.Fatalf("output = %q, want %q", got, want)
	}
}

func TestExecRunnerRejectsOversizedStdoutWithoutPartialResult(t *testing.T) {
	output, err := (execRunner{}).Run(context.Background(), "sh", "-c", "head -c 1048577 /dev/zero")
	if err == nil || output != nil {
		t.Fatalf("oversized output accepted: bytes=%d err=%v", len(output), err)
	}
	if !strings.Contains(err.Error(), "exceeded limit") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestExecRunnerBoundsTimedOutProcessTree(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	started := time.Now()
	_, err := (execRunner{waitDelay: 50 * time.Millisecond}).Run(ctx, "sh", "-c", "sleep 10 & wait")
	if err == nil {
		t.Fatal("Run() error = nil, want timeout")
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("Run() elapsed = %s, want at most 1s", elapsed)
	}
}

func TestResolveRepositoryRootFromNestedDirectory(t *testing.T) {
	root, err := resolveRepositoryRoot("")
	if err != nil {
		t.Fatal(err)
	}
	if !regularFile(filepath.Join(root, "security", "resident-images.json")) {
		t.Fatalf("resolved root %q is missing the security ledger", root)
	}
}

func TestRunDoctorBoundsFailureOutput(t *testing.T) {
	runner := &fakeRunner{responses: []fakeResponse{
		{err: errors.New("token=secret-value raw prompt contents")},
		{err: errors.New("kubeconfig contents")},
		{err: errors.New("resource output")},
		{err: errors.New("repository output")},
	}}
	report := runDoctor(context.Background(), runner, "local-lite", "test", "/repo", time.Unix(0, 0).UTC())
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range [][]byte{[]byte("secret-value"), []byte("raw prompt"), []byte("kubeconfig contents")} {
		if bytes.Contains(encoded, forbidden) {
			t.Fatalf("report leaked %q: %s", forbidden, encoded)
		}
	}
	if report.Status != "degraded" {
		t.Fatalf("status = %q, want degraded", report.Status)
	}
}
