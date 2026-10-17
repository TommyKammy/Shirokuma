package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

// Build ordinary Kubernetes responses from typed objects so tests describe
// runtime states rather than JSON encodings.
func localTestResource(kind, namespace, name string) kubernetesResource {
	var resource kubernetesResource
	resource.Kind = kind
	resource.Metadata.Name = name
	resource.Metadata.Namespace = namespace
	resource.Metadata.Generation = 2
	resource.Status.ObservedGeneration = 2
	return resource
}

func localTestCondition(resource kubernetesResource, kind, status string) kubernetesResource {
	// Unmarshal the condition to avoid duplicating the API struct definition.
	raw, _ := json.Marshal(map[string]any{"status": map[string]any{
		"conditions": []map[string]any{{"type": kind, "status": status, "observedGeneration": resource.Metadata.Generation}},
	}})
	_ = json.Unmarshal(raw, &resource)
	return resource
}

func localTestWorkload(kind, namespace, name string, replicas int) kubernetesResource {
	resource := localTestResource(kind, namespace, name)
	resource.Spec.Replicas = &replicas
	resource.Status.Replicas = replicas
	resource.Status.ReadyReplicas = replicas
	resource.Status.UpdatedReplicas = replicas
	resource.Status.AvailableReplicas = replicas
	return resource
}

type localDoctorFixture struct {
	endpoint    string
	nodes       []kubernetesResource
	controllers []kubernetesResource
	flux        []kubernetesResource
	storage     []kubernetesResource
	dev         []kubernetesResource
}

func healthyLocalDoctorFixture() localDoctorFixture {
	fixture := localDoctorFixture{
		endpoint: "https://127.0.0.1:6443",
		nodes: []kubernetesResource{
			localTestCondition(localTestResource("Node", "", "colima"), "Ready", "True"),
		},
		storage: []kubernetesResource{
			localTestWorkload("StatefulSet", "shirokuma-storage", "seaweedfs", 1),
		},
		dev: []kubernetesResource{
			localTestWorkload("StatefulSet", "shirokuma-dev", "polaris-postgresql", 1),
			localTestWorkload("Deployment", "shirokuma-dev", "polaris", 2),
			localTestWorkload("Deployment", "shirokuma-dev", "trino-local", 1),
			localTestCondition(localTestResource("Job", "shirokuma-dev", "polaris-bootstrap"), "Complete", "True"),
			localTestCondition(localTestResource("Job", "shirokuma-dev", "iceberg-table-bootstrap"), "Complete", "True"),
		},
	}
	for _, name := range []string{"source-controller", "kustomize-controller", "helm-controller", "notification-controller"} {
		fixture.controllers = append(fixture.controllers, localTestWorkload("Deployment", "flux-system", name, 1))
	}
	fixture.flux = append(fixture.flux,
		localTestCondition(localTestResource("GitRepository", "flux-system", "flux-system"), "Ready", "True"))
	root := localTestResource("Kustomization", "flux-system", "flux-system")
	root.Spec.Suspend = true // No fresh Ready condition while intentionally suspended.
	fixture.flux = append(fixture.flux, root)
	for _, name := range []string{"shirokuma-dev", "shirokuma-catalog", "shirokuma-object-storage", "shirokuma-catalog-bootstrap", "shirokuma-iceberg-bootstrap"} {
		fixture.flux = append(fixture.flux,
			localTestCondition(localTestResource("Kustomization", "flux-system", name), "Ready", "True"))
	}
	return fixture
}

func (fixture localDoctorFixture) runner(t *testing.T) *fakeRunner {
	t.Helper()
	responses := []fakeResponse{{output: fixture.endpoint}, {output: "ok\n"}}
	for _, items := range [][]kubernetesResource{fixture.nodes, fixture.controllers, fixture.flux, fixture.storage, fixture.dev} {
		raw, err := json.Marshal(kubernetesResourceList{Items: items})
		if err != nil {
			t.Fatal(err)
		}
		responses = append(responses, fakeResponse{output: string(raw)})
	}
	return &fakeRunner{responses: responses}
}

func localReportCheck(t *testing.T, report doctorReport, name string) doctorCheck {
	t.Helper()
	for _, check := range report.Checks {
		if check.Name == name {
			return check
		}
	}
	t.Fatalf("missing check %q in %#v", name, report)
	return doctorCheck{}
}

func TestLocalDoctorHealthy(t *testing.T) {
	runner := healthyLocalDoctorFixture().runner(t)
	now := time.Date(2026, 10, 17, 0, 0, 0, 0, time.UTC)
	report := runDoctor(context.Background(), runner, localExperimentProfile, localExperimentContext, "", now)
	if report.Status != "healthy" || report.GeneratedAt != "2026-10-17T00:00:00Z" || report.SchemaVersion != "1" {
		t.Fatalf("report = %#v", report)
	}
	for _, name := range []string{"local-scope", "cluster", "flux", "seaweedfs", "postgresql", "polaris", "trino", "polaris-bootstrap", "iceberg-table-bootstrap"} {
		if check := localReportCheck(t, report, name); check.Status != "healthy" {
			t.Errorf("%s = %#v", name, check)
		}
	}
	if check := localReportCheck(t, report, "flux"); !strings.Contains(check.Summary, "root Kustomization/flux-system/flux-system is suspended") {
		t.Fatalf("suspended root not explained: %#v", check)
	}
	for _, call := range runner.calls {
		if call[0] != "kubectl" || call[1] != "--context" || call[2] != localExperimentContext {
			t.Fatalf("unexpected diagnostic command: %v", call)
		}
	}
}

func TestLocalDoctorDegraded(t *testing.T) {
	for _, tc := range []struct {
		name, check string
		change      func(*localDoctorFixture)
	}{
		{"no nodes", "cluster", func(f *localDoctorFixture) { f.nodes = nil }},
		{"node not Ready", "cluster", func(f *localDoctorFixture) {
			f.nodes[0] = localTestCondition(f.nodes[0], "Ready", "False")
		}},
		{"controller missing", "flux", func(f *localDoctorFixture) { f.controllers = f.controllers[1:] }},
		{"controller unready", "flux", func(f *localDoctorFixture) { f.controllers[0].Status.ReadyReplicas = 0 }},
		{"source failed", "flux", func(f *localDoctorFixture) {
			f.flux[0] = localTestCondition(f.flux[0], "Ready", "False")
		}},
		{"source missing", "flux", func(f *localDoctorFixture) { f.flux = f.flux[1:] }},
		{"child failed", "flux", func(f *localDoctorFixture) {
			f.flux[3] = localTestCondition(f.flux[3], "Ready", "False")
		}},
		{"child suspended", "flux", func(f *localDoctorFixture) { f.flux[2].Spec.Suspend = true }},
		{"child stale", "flux", func(f *localDoctorFixture) { f.flux[2].Metadata.Generation++ }},
		{"dev child missing", "flux", func(f *localDoctorFixture) { f.flux = append(f.flux[:2], f.flux[3:]...) }},
		{"storage missing", "seaweedfs", func(f *localDoctorFixture) { f.storage = nil }},
		{"database unready", "postgresql", func(f *localDoctorFixture) { f.dev[0].Status.ReadyReplicas = 0 }},
		{"deployment rollout incomplete", "polaris", func(f *localDoctorFixture) { f.dev[1].Status.UpdatedReplicas = 1 }},
		{"deployment paused", "polaris", func(f *localDoctorFixture) { f.dev[1].Spec.Paused = true }},
		{"workload terminating", "trino", func(f *localDoctorFixture) { f.dev[2].Metadata.DeletionTimestamp = "2026-10-17T00:00:00Z" }},
		{"workload stale", "trino", func(f *localDoctorFixture) { f.dev[2].Metadata.Generation++ }},
		{"scaled to zero", "trino", func(f *localDoctorFixture) {
			f.dev[2] = localTestWorkload("Deployment", "shirokuma-dev", "trino-local", 0)
		}},
		{"bootstrap missing", "polaris-bootstrap", func(f *localDoctorFixture) { f.dev = append(f.dev[:3], f.dev[4:]...) }},
		{"bootstrap incomplete", "iceberg-table-bootstrap", func(f *localDoctorFixture) { f.dev[4].Status.Conditions = nil }},
		{"bootstrap failed", "polaris-bootstrap", func(f *localDoctorFixture) {
			f.dev[3] = localTestCondition(f.dev[3], "Failed", "True")
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fixture := healthyLocalDoctorFixture()
			tc.change(&fixture)
			report := runLocalExperimentDoctor(context.Background(), fixture.runner(t), localExperimentContext, time.Now())
			if report.Status != "degraded" || localReportCheck(t, report, tc.check).Status != "degraded" {
				t.Fatalf("expected degraded %s: %#v", tc.check, report)
			}
		})
	}
}

func TestLocalDoctorActiveRoot(t *testing.T) {
	fixture := healthyLocalDoctorFixture()
	fixture.flux[1].Spec.Suspend = false
	fixture.flux[1] = localTestCondition(fixture.flux[1], "Ready", "True")
	report := runLocalExperimentDoctor(context.Background(), fixture.runner(t), localExperimentContext, time.Now())
	if report.Status != "healthy" || strings.Contains(localReportCheck(t, report, "flux").Summary, "suspended") {
		t.Fatalf("active root: %#v", report)
	}
}

func TestLocalDoctorScope(t *testing.T) {
	runner := &fakeRunner{}
	report := runLocalExperimentDoctor(context.Background(), runner, "other-cluster", time.Now())
	if report.Status != "degraded" || len(runner.calls) != 0 {
		t.Fatalf("context mismatch: report=%#v calls=%v", report, runner.calls)
	}
	for _, endpoint := range []string{"https://192.0.2.1:6443", "http://127.0.0.1:6443", "https://user:password@127.0.0.1:6443", "invalid"} {
		check := checkLocalExperimentScope(context.Background(), &fakeRunner{responses: []fakeResponse{{output: endpoint}}}, localExperimentContext)
		if check.Status != "degraded" {
			t.Errorf("endpoint %q: %#v", endpoint, check)
		}
	}
	command := newDoctorCommand(runner)
	command.SetArgs([]string{"--profile", localExperimentProfile, "--context", "other-cluster"})
	command.SilenceUsage = true
	command.SilenceErrors = true
	if err := command.Execute(); err == nil || len(runner.calls) != 0 {
		t.Fatal("CLI context mismatch should fail before running diagnostics")
	}
}

func TestLocalDoctorReadFailures(t *testing.T) {
	for _, index := range []int{0, 1, 2, 3, 4, 5, 6} {
		t.Run(string(rune('A'+index)), func(t *testing.T) {
			runner := healthyLocalDoctorFixture().runner(t)
			runner.responses[index] = fakeResponse{err: errors.New("token=private-value")}
			// Cluster/controller failures skip their remaining dependent read.
			if index == 1 || index == 3 {
				runner.responses = append(runner.responses[:index+1], runner.responses[index+2:]...)
			}
			report := runLocalExperimentDoctor(context.Background(), runner, localExperimentContext, time.Now())
			raw, _ := json.Marshal(report)
			if report.Status != "degraded" || bytes.Contains(raw, []byte("private-value")) {
				t.Fatalf("failure report = %s", raw)
			}
		})
	}
	for _, index := range []int{2, 3, 4, 5, 6} {
		runner := healthyLocalDoctorFixture().runner(t)
		runner.responses[index].output = "{invalid"
		if index == 3 {
			runner.responses = append(runner.responses[:4], runner.responses[5:]...)
		}
		report := runLocalExperimentDoctor(context.Background(), runner, localExperimentContext, time.Now())
		if report.Status != "degraded" {
			t.Fatalf("invalid JSON response %d accepted", index)
		}
	}
}

func TestLocalDoctorOutput(t *testing.T) {
	for _, format := range []string{"json", "markdown"} {
		t.Run(format, func(t *testing.T) {
			var output bytes.Buffer
			command := newDoctorCommand(healthyLocalDoctorFixture().runner(t))
			command.SetOut(&output)
			command.SetArgs([]string{"--profile", localExperimentProfile, "--output", format})
			if err := command.Execute(); err != nil {
				t.Fatal(err)
			}
			if format == "json" {
				var report doctorReport
				if err := json.Unmarshal(output.Bytes(), &report); err != nil {
					t.Fatal(err)
				}
				if report.Status != "healthy" || report.Profile != localExperimentProfile || report.Context != localExperimentContext {
					t.Fatalf("JSON report = %#v", report)
				}
				if _, err := time.Parse(time.RFC3339, report.GeneratedAt); err != nil {
					t.Fatal(err)
				}
			} else {
				for _, want := range []string{"# Shirokuma doctor", "Status: **healthy**", "local-experiment", "**flux**: healthy", "suspended", "**polaris-bootstrap**: healthy", "**iceberg-table-bootstrap**: healthy"} {
					if !strings.Contains(output.String(), want) {
						t.Errorf("markdown missing %q: %s", want, output.String())
					}
				}
			}
		})
	}
}
