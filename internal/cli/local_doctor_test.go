package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

func healthyLocalWorkload(kind, namespace, name string) string {
	conditions := ""
	if kind == "Deployment" {
		conditions = `,"conditions":[{"type":"Available","status":"True"}]`
	}
	return fmt.Sprintf(`{"kind":%q,"metadata":{"name":%q,"namespace":%q,"generation":2},"spec":{"replicas":1},"status":{"observedGeneration":2,"replicas":1,"availableReplicas":1,"readyReplicas":1,"updatedReplicas":1%s}}`, kind, name, namespace, conditions)
}

func localControllerJSON() string {
	var controllers []string
	for _, name := range []string{"source-controller", "kustomize-controller", "helm-controller", "notification-controller"} {
		controllers = append(controllers, healthyLocalWorkload("Deployment", "flux-system", name))
	}
	return `{"items":[` + strings.Join(controllers, ",") + `]}`
}

func localFluxJSON() string {
	return `{"items":[{"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":1},"status":{"artifact":{"revision":"main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}},{"kind":"Kustomization","metadata":{"name":"flux-system","namespace":"flux-system","generation":2},"spec":{"suspend":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}},{"kind":"Kustomization","metadata":{"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}]}`
}

func TestLocalFluxRequiresFixedSourceAndRevision(t *testing.T) {
	cases := []struct {
		name string
		old  string
		new  string
	}{
		{"foreign-source", `"kind":"GitRepository","metadata":{"name":"flux-system"`, `"kind":"GitRepository","metadata":{"name":"foreign"`},
		{"foreign-source-namespace", `"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system"`, `"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"foreign"`},
		{"missing-artifact", `"artifact":{"revision":"main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},`, ``},
		{"malformed-revision", `main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`, `main@sha1:invalid`},
		{"wrong-root-source", `"sourceRef":{"kind":"GitRepository","name":"flux-system"}`, `"sourceRef":{"kind":"GitRepository","name":"foreign"}`},
		{"wrong-root-source-namespace", `"sourceRef":{"kind":"GitRepository","name":"flux-system"}`, `"sourceRef":{"kind":"GitRepository","name":"flux-system","namespace":"foreign"}`},
		{"deleted-source", `"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":1`, `"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":1,"deletionTimestamp":"2026-10-02T00:00:00Z"`},
		{"zero-source-generation", `"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":1`, `"kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":0`},
		{"zero-root-generation", `"kind":"Kustomization","metadata":{"name":"flux-system","namespace":"flux-system","generation":2`, `"kind":"Kustomization","metadata":{"name":"flux-system","namespace":"flux-system","generation":0`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.new, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture replacement did not match")
			}
			runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
			if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" {
				t.Fatalf("source ambiguity passed local doctor: %#v", check)
			}
			if len(runner.calls) != 2 {
				t.Fatalf("extra runtime commands: %v", runner.calls)
			}
		})
	}
}

func TestLocalFluxRejectsDuplicateRootResources(t *testing.T) {
	for _, index := range []int{0, 1} {
		var list struct {
			Items []json.RawMessage `json:"items"`
		}
		if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
			t.Fatal(err)
		}
		list.Items = append(list.Items, list.Items[index])
		output, err := json.Marshal(list)
		if err != nil {
			t.Fatal(err)
		}
		runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "duplicated") {
			t.Fatalf("duplicated root item %d passed: %#v", index, check)
		}
	}
}

func TestLocalFluxRejectsAmbiguousNonRootResources(t *testing.T) {
	for _, test := range []struct{ name, old, replacement, want string }{
		{"unexpected-kind", `"kind":"Kustomization","metadata":{"name":"shirokuma-catalog"`, `"kind":"Secret","metadata":{"name":"shirokuma-catalog"`, "unexpected kind"},
		{"zero-generation", `"name":"shirokuma-catalog","namespace":"flux-system","generation":1`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":0`, "generation unverified"},
		{"missing-name", `"name":"shirokuma-catalog","namespace":"flux-system"`, `"namespace":"flux-system"`, "identity unverified"},
		{"missing-namespace", `"name":"shirokuma-catalog","namespace":"flux-system"`, `"name":"shirokuma-catalog"`, "identity unverified"},
		{"missing-kind", `"kind":"Kustomization","metadata":{"name":"shirokuma-catalog"`, `"metadata":{"name":"shirokuma-catalog"`, "identity unverified"},
	} {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.replacement, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture replacement did not match")
			}
			runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
			if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, test.want) {
				t.Fatalf("ambiguous resource accepted: %#v", check)
			}
		})
	}
	var list struct {
		Items []json.RawMessage `json:"items"`
	}
	if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
		t.Fatal(err)
	}
	list.Items = append(list.Items, list.Items[2])
	output, err := json.Marshal(list)
	if err != nil {
		t.Fatal(err)
	}
	runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}}
	if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "duplicated") {
		t.Fatalf("duplicated non-root resource accepted: %#v", check)
	}
	generic := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}}
	if check := checkFlux(context.Background(), generic, localExperimentContext); check.Status != "degraded" || !strings.Contains(check.Summary, "suspended") || strings.Contains(check.Summary, "duplicated") {
		t.Fatalf("generic doctor contract changed: %#v", check)
	}
}

func TestLocalFluxRejectsConflictingStatusGenerationWhenReported(t *testing.T) {
	for _, test := range []struct{ name, old, replacement string }{
		{"source", `"artifact":{"revision":"main@sha1:`, `"observedGeneration":2,"artifact":{"revision":"main@sha1:`},
		{"child", `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"observedGeneration":2,`},
	} {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.replacement, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture replacement did not match")
			}
			runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
			if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "status generation mismatch") {
				t.Fatalf("conflicting reported generation accepted: %#v", check)
			}
			generic := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
			if check := checkFlux(context.Background(), generic, localExperimentContext); strings.Contains(check.Summary, "status generation mismatch") {
				t.Fatalf("generic doctor changed: %#v", check)
			}
		})
	}
	resources := strings.Replace(localFluxJSON(), `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"observedGeneration":1,`, 1)
	runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
	if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("matching reported generation rejected: %#v", check)
	}
}

func TestLocalFluxRejectsWrongControllerIdentity(t *testing.T) {
	for _, test := range []struct{ name, old, replacement string }{
		{"kind", `"kind":"Deployment"`, `"kind":"StatefulSet"`},
		{"namespace", `"namespace":"flux-system"`, `"namespace":"other"`},
	} {
		t.Run(test.name, func(t *testing.T) {
			controllers := strings.Replace(localControllerJSON(), test.old, test.replacement, 1)
			if controllers == localControllerJSON() {
				t.Fatal("fixture replacement did not match")
			}
			runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
			if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
				t.Fatalf("wrong controller identity accepted: %#v", check)
			}
		})
	}
	runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}
	if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("valid controllers rejected: %#v", check)
	}
}

func TestLocalFluxRejectsFailedAvailableCondition(t *testing.T) {
	for _, status := range []string{"False", "Unknown", ""} {
		controllers := strings.Replace(localControllerJSON(), `"type":"Available","status":"True"`, `"type":"Available","status":"`+status+`"`, 1)
		runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
			t.Fatalf("controller condition %q accepted: %#v", status, check)
		}
	}
}

func TestLocalFluxRejectsUnresolvedReplicaFailure(t *testing.T) {
	for _, status := range []string{"True", "Unknown", ""} {
		controllers := strings.Replace(localControllerJSON(), `"type":"Available","status":"True"`, `"type":"Available","status":"True"},{"type":"ReplicaFailure","status":"`+status+`"`, 1)
		runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
			t.Fatalf("unresolved ReplicaFailure %q accepted: %#v", status, check)
		}
	}
	controllers := strings.Replace(localControllerJSON(), `"type":"Available","status":"True"`, `"type":"Available","status":"True"},{"type":"ReplicaFailure","status":"False"`, 1)
	runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
	if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("explicitly clear ReplicaFailure rejected: %#v", check)
	}
}

func TestLocalFluxRequiresAvailableConditionOnControllers(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `,"conditions":[{"type":"Available","status":"True"}]`, "", 1)
	runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
	if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
		t.Fatalf("controller missing Available condition passed: %#v", check)
	}
	generic := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
	if check := checkFlux(context.Background(), generic, localExperimentContext); !strings.Contains(check.Summary, "suspended") {
		t.Fatalf("generic doctor behavior changed: %#v", check)
	}
}

func TestLocalFluxRejectsDuplicateControllerWithoutReadingSources(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `"name":"notification-controller"`, `"name":"source-controller"`, 1)
	runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
	check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "missing required") {
		t.Fatalf("duplicate controller passed: %#v", check)
	}
	// A missing fixed controller is already sufficient to reject the inventory.
	if len(runner.calls) != 1 {
		t.Fatalf("source read after incomplete controller inventory: %v", runner.calls)
	}
}

func TestLocalFluxRejectsContradictoryReadyAndTerminatingSuspendedRoot(t *testing.T) {
	for _, test := range []struct{ name, old, replacement, want string }{
		{"non-root-ready-conflict", `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1},{"type":"Ready","status":"False","observedGeneration":1}]}`, "conflicting Ready condition"},
		{"suspended-root-terminating", `"name":"flux-system","namespace":"flux-system","generation":2}`, `"name":"flux-system","namespace":"flux-system","generation":2,"deletionTimestamp":"2026-10-03T00:00:00Z"}`, "terminating"},
	} {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.replacement, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture replacement did not match")
			}
			runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
			if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, test.want) {
				t.Fatalf("ambiguous resource passed: %#v", check)
			}
		})
	}
}

func TestLocalFluxRejectsPaginatedInventories(t *testing.T) {
	for _, first := range []bool{true, false} {
		responses := []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}
		index := 1
		if first {
			index = 0
		}
		responses[index].output = strings.Replace(responses[index].output, `{"items":`, `{"metadata":{"continue":"private-token"},"items":`, 1)
		runner := &fakeRunner{responses: responses}
		check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "incomplete") || strings.Contains(check.Summary, "private-token") {
			t.Fatalf("continued inventory accepted or leaked: %#v", check)
		}
		wantCalls := 2
		if first {
			wantCalls = 1
		}
		if len(runner.calls) != wantCalls {
			t.Fatalf("unexpected reads: %v", runner.calls)
		}
	}
}

func localWorkloadResponses() []fakeResponse {
	return []fakeResponse{
		{output: `{"items":[` + healthyLocalWorkload("StatefulSet", "shirokuma-storage", "seaweedfs") + `]}`},
		{output: `{"items":[` + healthyLocalWorkload("StatefulSet", "shirokuma-dev", "polaris-postgresql") + `,` + healthyLocalWorkload("Deployment", "shirokuma-dev", "polaris") + `,` + healthyLocalWorkload("Deployment", "shirokuma-dev", "trino-local") + `]}`},
	}
}

func localDoctorResponses() []fakeResponse {
	return append([]fakeResponse{
		{output: "https://127.0.0.1:6443"},
		{output: "ok\n"},
		{output: localControllerJSON()},
		{output: localFluxJSON()},
	}, localWorkloadResponses()...)
}

func TestDoctorLocalExperimentReportsBoundedReadiness(t *testing.T) {
	runner := &fakeRunner{responses: localDoctorResponses()}
	var output bytes.Buffer
	command := newRootCommand(&output, runner)
	// Runtime-only diagnostics do not depend on a policy repository being present.
	command.SetArgs([]string{"doctor", "--profile", "local-experiment", "--output", "json", "--repo-root", t.TempDir()})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	var report doctorReport
	if err := json.Unmarshal(output.Bytes(), &report); err != nil {
		t.Fatal(err)
	}
	if report.Status != "healthy" || report.Profile != "local-experiment" || len(report.Checks) != 9 {
		t.Fatalf("report = %#v", report)
	}
	byName := map[string]doctorCheck{}
	for _, check := range report.Checks {
		byName[check.Name] = check
	}
	if byName["flux"].Status != "informational" || !strings.Contains(byName["flux"].Summary, "intent and source revision provenance not independently verified") {
		t.Fatalf("root suspension = %#v", byName["flux"])
	}
	for _, name := range []string{"seaweedfs", "postgresql", "polaris", "trino"} {
		if byName[name].Status != "healthy" || !strings.Contains(byName[name].Summary, "Kubernetes readiness only") {
			t.Fatalf("workload %s = %#v", name, byName[name])
		}
	}
	for _, name := range []string{"sql", "publication"} {
		if byName[name].Status != "informational" || !strings.Contains(byName[name].Summary, "not evaluated") {
			t.Fatalf("bounded check %s = %#v", name, byName[name])
		}
	}
	for _, call := range runner.calls {
		if call[0] != "kubectl" || call[1] != "--context" || call[2] != localExperimentContext {
			t.Fatalf("unexpected command: %v", call)
		}
	}
}

func TestLocalExperimentRejectsOtherContextBeforeChecks(t *testing.T) {
	runner := &fakeRunner{}
	command := newRootCommand(&bytes.Buffer{}, runner)
	command.SetArgs([]string{"doctor", "--profile", "local-experiment", "--context", "production"})
	if err := command.Execute(); err == nil {
		t.Fatal("expected context rejection")
	}
	if len(runner.calls) != 0 {
		t.Fatalf("commands ran for a different context: %v", runner.calls)
	}
	if report := runDoctor(context.Background(), runner, localExperimentProfile, "production", "/repo", time.Unix(0, 0)); report.Status != "degraded" {
		t.Fatalf("direct invocation bypassed context guard: %#v", report)
	}
}

func TestLocalExperimentScopeRejectsRemoteOrMalformedEndpoints(t *testing.T) {
	for _, endpoint := range []string{"https://cluster.example:6443", "https://127.0.0.1.example", "http://127.0.0.1:6443", "https://user:secret@127.0.0.1", "https://127.0.0.1", "https://127.0.0.1:0", "https://127.0.0.1:65536", "https://127.0.0.1:abc", "https://127.0.0.1:6443/proxy", "https://127.0.0.1:6443?redirect=remote", "https://127.0.0.1:6443?", "https://127.0.0.1:6443#fragment", "https://127.0.0.1:6443#", "invalid", "%"} {
		t.Run(endpoint, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{output: endpoint}}}
			report := runLocalExperimentDoctor(context.Background(), runner, localExperimentContext, time.Unix(0, 0))
			if report.Status != "degraded" || len(runner.calls) != 1 {
				t.Fatalf("unexpected scope report: %#v calls=%v", report, runner.calls)
			}
			if strings.Contains(report.Checks[0].Summary, "secret") {
				t.Fatal("endpoint credentials leaked")
			}
		})
	}
	for _, endpoint := range []string{"https://127.0.0.1:6443", "https://[::1]:6443", "https://localhost:6443"} {
		runner := &fakeRunner{responses: []fakeResponse{{output: endpoint}}}
		if check := checkLocalExperimentScope(context.Background(), runner, localExperimentContext); check.Status != "healthy" {
			t.Fatalf("loopback %s rejected: %#v", endpoint, check)
		}
	}
}

func TestLocalLakehouseRejectsMissingStaleOrFailedWorkloads(t *testing.T) {
	cases := []struct {
		name string
		old  string
		new  string
		want string
	}{
		{"missing", `"seaweedfs"`, `"unrelated"`, "is missing"},
		{"wrong-kind", `"StatefulSet"`, `"Deployment"`, "is missing"},
		{"wrong-namespace", `"shirokuma-storage"`, `"unrelated"`, "is missing"},
		{"stale-generation", `"observedGeneration":2`, `"observedGeneration":1`, "current generation"},
		{"missing-generation", `"observedGeneration":2,`, ``, "current generation"},
		{"scaled-down", `"spec":{"replicas":1}`, `"spec":{"replicas":0}`, "positive desired replica"},
		{"missing-desired", `"spec":{"replicas":1}`, `"spec":{}`, "positive desired replica"},
		{"missing-ready", `"readyReplicas":1`, `"readyReplicas":0`, "rollout incomplete"},
		{"old-replicas", `"updatedReplicas":1`, `"updatedReplicas":0`, "rollout incomplete"},
		{"unavailable", `"availableReplicas":1`, `"availableReplicas":0`, "rollout incomplete"},
		{"extra-replica", `"status":{"observedGeneration":2,"replicas":1`, `"status":{"observedGeneration":2,"replicas":2`, "rollout incomplete"},
		{"terminating", `"generation":2`, `"generation":2,"deletionTimestamp":"2026-09-16T00:00:00Z"`, "terminating"},
		{"replica-failure", `"observedGeneration":2`, `"conditions":[{"type":"ReplicaFailure","status":"True"}],"observedGeneration":2`, "failed rollout"},
		{"replica-failure-unknown", `"observedGeneration":2`, `"conditions":[{"type":"ReplicaFailure","status":"Unknown"}],"observedGeneration":2`, "failed rollout"},
		{"replica-failure-empty", `"observedGeneration":2`, `"conditions":[{"type":"ReplicaFailure","status":""}],"observedGeneration":2`, "failed rollout"},
		{"progress-failure", `"observedGeneration":2`, `"conditions":[{"type":"Progressing","status":"False"}],"observedGeneration":2`, "failed rollout"},
		{"available-failure", `"observedGeneration":2`, `"conditions":[{"type":"Available","status":"False"}],"observedGeneration":2`, "failed rollout"},
		{"available-unknown", `"observedGeneration":2`, `"conditions":[{"type":"Available","status":"Unknown"}],"observedGeneration":2`, "failed rollout"},
		{"progress-unknown", `"observedGeneration":2`, `"conditions":[{"type":"Progressing","status":"Unknown"}],"observedGeneration":2`, "failed rollout"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			responses := localWorkloadResponses()
			responses[0].output = strings.Replace(responses[0].output, test.old, test.new, 1)
			runner := &fakeRunner{responses: responses}
			checks := checkLocalLakehouse(context.Background(), runner, localExperimentContext)
			if checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, test.want) {
				t.Fatalf("check = %#v, want %s", checks[0], test.want)
			}
			for _, check := range checks[1:] {
				if check.Status != "healthy" {
					t.Fatalf("unrelated workload degraded: %#v", check)
				}
			}
		})
	}
}

func TestLocalLakehouseRequiresAvailableConditionOnlyOnDeployments(t *testing.T) {
	responses := localWorkloadResponses()
	responses[1].output = strings.Replace(responses[1].output, `,"conditions":[{"type":"Available","status":"True"}]`, "", 1)
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
	if checks[0].Status != "healthy" || checks[1].Status != "healthy" || checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "Available=True") || checks[3].Status != "healthy" {
		t.Fatalf("missing deployment condition or StatefulSet condition handling: %#v", checks)
	}
}

func TestLocalLakehouseReportsNamespaceReadFailuresWithoutLeakingOutput(t *testing.T) {
	for _, response := range []fakeResponse{{output: "not JSON secret=hidden"}, {err: errors.New("credential=hidden")}} {
		responses := localWorkloadResponses()
		responses[1] = response
		runner := &fakeRunner{responses: responses}
		checks := checkLocalLakehouse(context.Background(), runner, localExperimentContext)
		if checks[0].Status != "healthy" {
			t.Fatalf("unrelated namespace degraded: %#v", checks[0])
		}
		for _, check := range checks[1:] {
			if check.Status != "degraded" || strings.Contains(check.Summary, "hidden") {
				t.Fatalf("unsafe or incomplete error: %#v", check)
			}
		}
	}
}

func TestLocalLakehouseRejectsIncompleteAndDuplicateInventory(t *testing.T) {
	for _, change := range []struct {
		name, output, want string
	}{
		{"missing-items", `{}`, "incomplete"},
		{"continued-list", `{"metadata":{"continue":"private-token"},"items":[]}`, "incomplete"},
		{"duplicate", `{"items":[` + healthyLocalWorkload("StatefulSet", "shirokuma-storage", "seaweedfs") + `,` + healthyLocalWorkload("StatefulSet", "shirokuma-storage", "seaweedfs") + `]}`, "duplicated"},
	} {
		t.Run(change.name, func(t *testing.T) {
			responses := localWorkloadResponses()
			responses[0].output = change.output
			checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
			if checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, change.want) || strings.Contains(checks[0].Summary, "private-token") {
				t.Fatalf("incomplete inventory accepted or leaked: %#v", checks[0])
			}
			for _, check := range checks[1:] {
				if check.Status != "healthy" {
					t.Fatalf("unrelated namespace degraded: %#v", check)
				}
			}
		})
	}
}

func TestLocalFluxExceptionDoesNotHideOtherFailures(t *testing.T) {
	cases := []struct {
		name        string
		controllers string
		resources   string
	}{
		{"stale-controller", strings.Replace(localControllerJSON(), `"observedGeneration":2`, `"observedGeneration":1`, 1), localFluxJSON()},
		{"unready-controller", strings.Replace(localControllerJSON(), `"availableReplicas":1`, `"availableReplicas":0`, 1), localFluxJSON()},
		{"terminating-controller", strings.Replace(localControllerJSON(), `"generation":2`, `"generation":2,"deletionTimestamp":"2026-09-16T00:00:00Z"`, 1), localFluxJSON()},
		{"failed-controller", strings.Replace(localControllerJSON(), `"conditions":[{"type":"Available","status":"True"}]`, `"conditions":[{"type":"Available","status":"True"},{"type":"ReplicaFailure","status":"True"}]`, 1), localFluxJSON()},
		{"missing-controller-replicas", strings.Replace(localControllerJSON(), `"spec":{"replicas":1}`, `"spec":{}`, 1), localFluxJSON()},
		{"failed-child", localControllerJSON(), strings.Replace(localFluxJSON(), `"name":"shirokuma-catalog","namespace":"flux-system","generation":1`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":2`, 1)},
		{"suspended-child", localControllerJSON(), strings.Replace(localFluxJSON(), `"generation":1},"status"`, `"generation":1},"spec":{"suspend":true},"status"`, 1)},
		{"missing-root", localControllerJSON(), strings.Replace(localFluxJSON(), `"name":"flux-system","namespace":"flux-system","generation":2`, `"name":"other-root","namespace":"flux-system","generation":2`, 1)},
		{"terminating-root", localControllerJSON(), strings.Replace(localFluxJSON(), `"generation":2`, `"generation":2,"deletionTimestamp":"2026-09-16T00:00:00Z"`, 1)},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			runner := &fakeRunner{responses: []fakeResponse{{output: test.controllers}, {output: test.resources}}}
			if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" {
				t.Fatalf("unhealthy Flux hidden by suspension exception: %#v", check)
			}
		})
	}
	runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}
	if check := checkFlux(context.Background(), runner, localExperimentContext); check.Status != "degraded" {
		t.Fatalf("default profile unexpectedly permits suspension: %#v", check)
	}
}

func TestLocalDoctorUnhealthyWorkloadDegradesWholeReport(t *testing.T) {
	responses := localDoctorResponses()
	responses[5].output = strings.Replace(responses[5].output, `"trino-local"`, `"missing-trino"`, 1)
	runner := &fakeRunner{responses: responses}
	report := runDoctor(context.Background(), runner, localExperimentProfile, localExperimentContext, "/repo", time.Unix(0, 0))
	if report.Status != "degraded" {
		t.Fatalf("report = %#v", report)
	}
}
