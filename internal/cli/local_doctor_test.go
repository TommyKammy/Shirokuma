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
		conditions = `,"conditions":[{"type":"Available","status":"True"},{"type":"Progressing","status":"True"}]`
	}
	return fmt.Sprintf(`{"apiVersion":"apps/v1","kind":%q,"metadata":{"name":%q,"namespace":%q,"generation":2},"spec":{"replicas":1},"status":{"observedGeneration":2,"replicas":1,"availableReplicas":1,"readyReplicas":1,"updatedReplicas":1%s}}`, kind, name, namespace, conditions)
}

func localControllerJSON() string {
	var controllers []string
	for _, name := range []string{"source-controller", "kustomize-controller", "helm-controller", "notification-controller"} {
		controllers = append(controllers, healthyLocalWorkload("Deployment", "flux-system", name))
	}
	return `{"items":[` + strings.Join(controllers, ",") + `]}`
}

func TestLocalFluxRequiresControllerAPIVersion(t *testing.T) {
	for _, version := range []string{``, `"v1"`, `null`} {
		for _, name := range []string{"source-controller", "kustomize-controller", "helm-controller", "notification-controller"} {
			replacement := ``
			if version != `` {
				replacement = `"apiVersion":` + version + `,`
			}
			member := healthyLocalWorkload("Deployment", "flux-system", name)
			controllers := strings.Replace(localControllerJSON(), member,
				strings.Replace(member, `"apiVersion":"apps/v1",`, replacement, 1), 1)
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
				{output: controllers}, {output: localFluxJSON()},
			}}, localExperimentContext, true)
			if check.Status != "degraded" {
				t.Fatalf("%s API version %q accepted: %#v", name, version, check)
			}
		}
	}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
		{output: localControllerJSON()}, {output: localFluxJSON()},
	}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("valid controller versions rejected: %#v", check)
	}
}

func TestLocalFluxRequiresControllerKindAndNamespace(t *testing.T) {
	for _, name := range []string{"source-controller", "kustomize-controller", "helm-controller", "notification-controller"} {
		member := healthyLocalWorkload("Deployment", "flux-system", name)
		for _, mutation := range []struct {
			old string
			new string
		}{
			{`"kind":"Deployment"`, `"kind":"StatefulSet"`},
			{`"namespace":"flux-system"`, `"namespace":"default"`},
		} {
			controllers := strings.Replace(localControllerJSON(), member,
				strings.Replace(member, mutation.old, mutation.new, 1), 1)
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
				{output: controllers}, {output: localFluxJSON()},
			}}, localExperimentContext, true)
			if check.Status != "degraded" {
				t.Fatalf("%s %s accepted: %#v", name, mutation.new, check)
			}
		}
	}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
		{output: localControllerJSON()}, {output: localFluxJSON()},
	}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("valid controllers rejected: %#v", check)
	}
}

func localFluxJSON() string {
	return `{"items":[{"apiVersion":"source.toolkit.fluxcd.io/v1","kind":"GitRepository","metadata":{"name":"flux-system","namespace":"flux-system","generation":1},"spec":{"ref":{"branch":"main"},"url":"ssh://git@github.com/TommyKammy/Shirokuma","secretRef":{"name":"flux-system"}},"status":{"artifact":{"revision":"main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}},{"apiVersion":"kustomize.toolkit.fluxcd.io/v1","kind":"Kustomization","metadata":{"name":"flux-system","namespace":"flux-system","generation":2},"spec":{"suspend":true,"path":"./deploy/gitops/clusters/local-lite","prune":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}},{"apiVersion":"kustomize.toolkit.fluxcd.io/v1","kind":"Kustomization","metadata":{"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}},{"apiVersion":"kustomize.toolkit.fluxcd.io/v1","kind":"Kustomization","metadata":{"name":"shirokuma-dev","namespace":"flux-system","generation":1},"spec":{"path":"./deploy/gitops/dev","prune":true,"wait":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}},"status":{"lastAppliedRevision":"main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}]}`
}

func TestLocalFluxRequiresResourceAPIVersion(t *testing.T) {
	for _, tc := range []struct{ kind, name, expected string }{
		{"GitRepository", "flux-system", "source.toolkit.fluxcd.io/v1"},
		{"Kustomization", "flux-system", "kustomize.toolkit.fluxcd.io/v1"},
		{"Kustomization", "shirokuma-catalog", "kustomize.toolkit.fluxcd.io/v1"},
		{"Kustomization", "shirokuma-dev", "kustomize.toolkit.fluxcd.io/v1"},
	} {
		for _, version := range []string{"", "v1", "null"} {
			original := `"apiVersion":"` + tc.expected + `","kind":"` + tc.kind + `","metadata":{"name":"` + tc.name + `"`
			replacement := `"kind":"` + tc.kind + `","metadata":{"name":"` + tc.name + `"`
			if version != "" {
				replacement = `"apiVersion":` + version + `,` + replacement
			}
			resources := strings.Replace(localFluxJSON(), original, replacement, 1)
			if resources == localFluxJSON() {
				t.Fatalf("fixture did not contain %s/%s", tc.kind, tc.name)
			}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
				{output: localControllerJSON()}, {output: resources},
			}}, localExperimentContext, true)
			if check.Status != "degraded" {
				t.Fatalf("%s/%s version %q accepted: %#v", tc.kind, tc.name, version, check)
			}
		}
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("valid versions rejected: %#v", check)
	}
}

func TestLocalFluxRequiresResourceKindAndNamespace(t *testing.T) {
	for _, tc := range []struct{ kind, name string }{
		{"GitRepository", "flux-system"},
		{"Kustomization", "flux-system"},
		{"Kustomization", "shirokuma-catalog"},
		{"Kustomization", "shirokuma-dev"},
	} {
		mutations := []struct{ old, replacement string }{
			{`"kind":"` + tc.kind + `"`, `"kind":"Other"`},
		}
		// Catalog is an optional resource, not one of the three fixed identities.
		if tc.name != "shirokuma-catalog" {
			mutations = append(mutations, struct{ old, replacement string }{
				`"namespace":"flux-system"`, `"namespace":"default"`,
			})
		}
		for _, mutation := range mutations {
			original := `"kind":"` + tc.kind + `","metadata":{"name":"` + tc.name + `","namespace":"flux-system"`
			member := strings.Replace(original, mutation.old, mutation.replacement, 1)
			resources := strings.Replace(localFluxJSON(), original, member, 1)
			if resources == localFluxJSON() {
				t.Fatalf("fixture did not contain %s/%s", tc.kind, tc.name)
			}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
				{output: localControllerJSON()}, {output: resources},
			}}, localExperimentContext, true)
			if check.Status != "degraded" {
				t.Fatalf("%s/%s mutation %s accepted: %#v", tc.kind, tc.name, mutation.replacement, check)
			}
		}
	}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
		{output: localControllerJSON()}, {output: localFluxJSON()},
	}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("valid resource inventory rejected: %#v", check)
	}
}

func TestLocalFluxOptionalCatalogDoesNotImposeFixedNamespace(t *testing.T) {
	// Catalog is discovered by its own identity, not part of the fixed source/root/dev scope.
	resources := strings.Replace(localFluxJSON(),
		`"name":"shirokuma-catalog","namespace":"flux-system"`,
		`"name":"shirokuma-catalog","namespace":"shirokuma-catalog"`, 1)
	if resources == localFluxJSON() {
		t.Fatal("catalog fixture was not changed")
	}
	for _, inventory := range []string{localFluxJSON(), resources} {
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
			{output: localControllerJSON()}, {output: inventory},
		}}, localExperimentContext, true)
		if check.Status != "informational" {
			t.Fatalf("optional catalog namespace changed fixed identity check: %#v", check)
		}
	}
}

func TestLocalFluxOptionalCatalogMayBeAbsent(t *testing.T) {
	const catalog = `,{"apiVersion":"kustomize.toolkit.fluxcd.io/v1","kind":"Kustomization","metadata":{"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}`
	resources := strings.Replace(localFluxJSON(), catalog, "", 1)
	if resources == localFluxJSON() {
		t.Fatal("catalog fixture was not removed")
	}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{
		{output: localControllerJSON()}, {output: resources},
	}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("absent optional catalog changed fixed Flux scope: %#v", check)
	}
}

func TestLocalFluxRejectsIncompleteConditions(t *testing.T) {
	for _, extra := range []string{`null`, `{}`, `{"type":"Ready"}`, `{"status":"True"}`, `{"type":"Other","status":"bogus"}`} {
		controllers := strings.Replace(localControllerJSON(), `{"type":"Available","status":"True"}`, `{"type":"Available","status":"True"},`+extra, 1)
		controllerCheck := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if controllerCheck.Status != "degraded" {
			t.Fatalf("controller extra condition %s accepted: %#v", extra, controllerCheck)
		}
		resources := strings.Replace(localFluxJSON(), `{"type":"Ready","status":"True","observedGeneration":1}`, `{"type":"Ready","status":"True","observedGeneration":1},`+extra, 1)
		responses := []fakeResponse{{output: localControllerJSON()}, {output: resources}}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
		expected := "resource response was incomplete"
		if check.Status != "degraded" || !strings.Contains(check.Summary, expected) {
			t.Fatalf("extra condition %s accepted: %#v", extra, check)
		}
		generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
		if strings.Contains(generic.Summary, "incomplete condition") {
			t.Fatalf("generic profile inherited local check: %#v", generic)
		}
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("complete conditions rejected: %#v", check)
	}
}

func TestLocalLakehouseRejectsIncompleteConditions(t *testing.T) {
	for _, extra := range []string{`null`, `{}`, `{"type":"Available"}`, `{"status":"True"}`, `{"type":"Other","status":"bogus"}`} {
		responses := localWorkloadResponses()
		responses[1].output = strings.Replace(responses[1].output, `{"type":"Available","status":"True"}`, `{"type":"Available","status":"True"},`+extra, 1)
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		if checks[2].Status != "degraded" {
			t.Fatalf("extra condition %s accepted: %#v", extra, checks)
		}
	}
	for _, check := range checkLocalLakehouse(context.Background(), &fakeRunner{responses: localWorkloadResponses()}, localExperimentContext) {
		if check.Status != "healthy" {
			t.Fatalf("complete workload rejected: %#v", check)
		}
	}
}

func TestLocalLakehouseRequiresWorkloadAPIVersion(t *testing.T) {
	for _, target := range []struct {
		response int
		last     bool
		check    int
	}{{0, false, 0}, {1, false, 1}, {1, true, 3}} {
		for _, version := range []string{``, `"v1"`, `null`} {
			responses := localWorkloadResponses()
			replacement := ""
			if version != "" {
				replacement = `"apiVersion":` + version + `,`
			}
			const original = `"apiVersion":"apps/v1",`
			if target.last {
				index := strings.LastIndex(responses[target.response].output, original)
				responses[target.response].output = responses[target.response].output[:index] + replacement + responses[target.response].output[index+len(original):]
			} else {
				responses[target.response].output = strings.Replace(responses[target.response].output, original, replacement, 1)
			}
			checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
			if checks[target.check].Status != "degraded" {
				t.Fatalf("workload %d API version %q accepted: %#v", target.check, version, checks[target.check])
			}
		}
	}
}

func TestLocalLakehouseRejectsUIDReusedAcrossNamespaceReads(t *testing.T) {
	responses := localWorkloadResponses()
	for i := range responses {
		responses[i].output = strings.Replace(responses[i].output, `"generation":2`, `"generation":2,"uid":"same-resource-uid"`, 1)
	}
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
	for _, check := range checks {
		if check.Status != "degraded" {
			t.Fatalf("cross-namespace UID collision accepted: %#v", checks)
		}
	}
}

func TestLocalLakehouseRejectsUIDReusedWithinNamespace(t *testing.T) {
	responses := localWorkloadResponses()
	responses[1].output = strings.Replace(responses[1].output, `"generation":2`, `"generation":2,"uid":"same-resource-uid"`, 2)
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
	if checks[0].Status != "healthy" {
		t.Fatalf("unrelated storage namespace rejected: %#v", checks)
	}
	for _, check := range checks[1:] {
		if check.Status != "degraded" {
			t.Fatalf("same-namespace UID collision accepted: %#v", checks)
		}
	}
}

func TestLocalFluxRejectsControllerUIDReusedByResource(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `"generation":2`, `"generation":2,"uid":"shared-uid"`, 1)
	resources := strings.Replace(localFluxJSON(), `"generation":1`, `"generation":1,"uid":"shared-uid"`, 1)
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: resources}}}, localExperimentContext, true)
	if check.Status != "degraded" {
		t.Fatalf("controller/resource UID collision accepted: %#v", check)
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("omitted UIDs rejected: %#v", check)
	}
}

func TestLocalInventoryRequiresCanonicalEnvelopeKeys(t *testing.T) {
	for _, alias := range []string{"Items", "ITEMS", "Metadata", "Kind", "APIVERSION"} {
		controllers := strings.Replace(localControllerJSON(), `"items"`, `"`+alias+`"`, 1)
		if alias != "Items" && alias != "ITEMS" {
			value := `{}`
			if alias == "Kind" {
				value = `"List"`
			} else if alias == "APIVERSION" {
				value = `"v1"`
			}
			controllers = strings.TrimSuffix(localControllerJSON(), "}") + `,"` + alias + `":` + value + `}`
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("alias %q accepted: %#v", alias, check)
		}
		generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, false)
		if strings.Contains(generic.Summary, "controller response was incomplete") {
			t.Fatalf("generic profile inherited canonical-envelope check: %#v", generic)
		}
		flux := strings.Replace(localFluxJSON(), `"items"`, `"Items"`, 1)
		check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: flux}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("Flux resource alias accepted: %#v", check)
		}
		lakehouse := localWorkloadResponses()
		lakehouse[0].output = strings.Replace(lakehouse[0].output, `"items"`, `"Items"`, 1)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: lakehouse}, localExperimentContext); checks[0].Status != "degraded" {
			t.Fatalf("workload alias accepted: %#v", checks)
		}
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("canonical envelope rejected: %#v", check)
	}
}

func TestLocalInventoryRejectsUnboundedReportedNames(t *testing.T) {
	for _, tc := range []struct {
		field string
		limit int
	}{
		{"name", 253}, {"namespace", 63},
	} {
		for _, size := range []int{tc.limit, tc.limit + 1} {
			name := strings.Repeat("a", size)
			if tc.field == "name" {
				name = strings.Repeat("a", 63) + "." + strings.Repeat("b", 63) + "." +
					strings.Repeat("c", 63) + "." + strings.Repeat("d", size-63*3-3)
			}
			raw := []byte(`{"items":[{"metadata":{"` + tc.field + `":"` + name + `"}}]}`)
			var list kubernetesResourceList
			if err := json.Unmarshal(raw, &list); err != nil {
				t.Fatal(err)
			}
			if got := localInventoryIncomplete(list, raw); got != (size > tc.limit) {
				t.Fatalf("incomplete=%t for %s length %d", got, tc.field, size)
			}
		}
	}
}

func TestLocalInventoryRejectsNullItems(t *testing.T) {
	for _, raw := range [][]byte{[]byte(`{"items":null}`), []byte(`{"items":[]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if got, want := localInventoryIncomplete(list, raw), string(raw) == `{"items":null}`; got != want {
			t.Fatalf("incomplete=%t for %s, want %t", got, raw, want)
		}
	}
}

func TestLocalInventoryRejectsNullMemberWithoutRejectingEmptyList(t *testing.T) {
	for _, tc := range []struct {
		raw     string
		invalid bool
	}{
		{`{"items":[null]}`, true},
		{`{"items":[{},null]}`, true},
		{`{"items":[]}`, false},
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal([]byte(tc.raw), &list); err != nil {
			t.Fatal(err)
		}
		if got := localInventoryIncomplete(list, []byte(tc.raw)); got != tc.invalid {
			t.Fatalf("incomplete=%t for %s, want %t", got, tc.raw, tc.invalid)
		}
	}
}

func TestLocalInventoryRejectsContradictoryListType(t *testing.T) {
	for _, tc := range []struct {
		fields  string
		invalid bool
	}{
		{`"kind":"Pod"`, true},
		{`"apiVersion":"apps/v1"`, true},
		{`"kind":""`, true},
		{`"apiVersion":""`, true},
		{`"kind":"List","apiVersion":"v1"`, false},
		{`"metadata":{},"kind":"List"`, false},
	} {
		raw := []byte(`{` + tc.fields + `,"items":[]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if got := localInventoryIncomplete(list, raw); got != tc.invalid {
			t.Fatalf("incomplete=%t want %t for %s", got, tc.invalid, raw)
		}
	}
}

func TestLocalInventoryRejectsExplicitEmptyMemberGVK(t *testing.T) {
	for _, tc := range []struct {
		member  string
		invalid bool
	}{
		{`{"kind":""}`, true},
		{`{"apiVersion":""}`, true},
		{`{"kind":"Service","apiVersion":"v1"}`, false},
		{`{}`, false}, // projected fixtures may omit member GVK entirely
	} {
		raw := []byte(`{"items":[` + tc.member + `]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if got := localInventoryIncomplete(list, raw); got != tc.invalid {
			t.Fatalf("incomplete=%t want %t for %s", got, tc.invalid, raw)
		}
	}
}

func TestLocalInventoryRejectsObscuredMemberGVK(t *testing.T) {
	for _, tc := range []struct {
		member  string
		invalid bool
	}{
		{`{"kind":"Service ","apiVersion":"v1"}`, true},
		{`{"kind":"Service","apiVersion":"v1\u0000"}`, true},
		{`{"kind":"Service","apiVersion":"v1\u200b"}`, true},
		{`{"kind":"Service","apiVersion":"v1"}`, false},
		{`{}`, false},
	} {
		raw := []byte(`{"items":[` + tc.member + `]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if got := localInventoryIncomplete(list, raw); got != tc.invalid {
			t.Fatalf("incomplete=%t want %t for %s", got, tc.invalid, raw)
		}
	}
}

func TestLocalInventoryRejectsLossyUnknownJSONKey(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"items":[],"\ud800":1}`),
		[]byte(`{"items":[{"metadata":{"name":"valid","\ud800":1}}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if !localInventoryIncomplete(list, raw) {
			t.Fatalf("lossy key accepted: %s", raw)
		}
	}
	var list kubernetesResourceList
	raw := []byte(`{"items":[],"extension":"snow"}`)
	if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
		t.Fatalf("valid extension rejected: %s (%v)", raw, err)
	}
}

func TestLocalInventoryRejectsMalformedReportedDNSNames(t *testing.T) {
	for _, tc := range []struct {
		field, value string
		invalid      bool
	}{
		{"name", "a.b", false},
		{"name", "a_b", true},
		{"name", "A", true},
		{"name", strings.Repeat("a", 64) + ".b", true},
		{"namespace", "a", false},
		{"namespace", "a.b", true},
		{"namespace", "a-", true},
	} {
		raw := []byte(`{"items":[{"metadata":{"` + tc.field + `":"` + tc.value + `"}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if got := localInventoryIncomplete(list, raw); got != tc.invalid {
			t.Fatalf("incomplete=%t for %s=%q, want %t", got, tc.field, tc.value, tc.invalid)
		}
	}
}

func TestLocalInventoryRequiresCanonicalMetadataKeys(t *testing.T) {
	for _, field := range []string{`"ResourceVersion":"123"`, `"RemainingItemCount":0`, `"Continue":""`} {
		decorate := func(raw string) string {
			return strings.Replace(raw, `"items":`, `"metadata":{`+field+`},"items":`, 1)
		}
		controllers := decorate(localControllerJSON())
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("controller metadata alias %s accepted: %#v", field, check)
		}
		check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: decorate(localFluxJSON())}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("Flux metadata alias %s accepted: %#v", field, check)
		}
		workloads := localWorkloadResponses()
		workloads[0].output = decorate(workloads[0].output)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[0].Status != "degraded" {
			t.Fatalf("workload metadata alias %s accepted: %#v", field, checks)
		}
	}
	canonical := `"metadata":{"resourceVersion":"123","remainingItemCount":0,"continue":""},"items":`
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: strings.Replace(localControllerJSON(), `"items":`, canonical, 1)}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("canonical metadata rejected: %#v", check)
	}
}

func TestLocalInventoryRejectsConflictingListEnvelopeAliases(t *testing.T) {
	for _, field := range []string{`"Items":[{"kind":"Secret"}]`, `"ITEMS":[{"kind":"Secret"}]`,
		`"Metadata":{"continue":"next-page"}`, `"Kind":"OtherList"`, `"ApiVersion":"v2"`} {
		raw := []byte(`{"apiVersion":"v1","kind":"List","items":[],` + field + `}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if !localInventoryIncomplete(list, raw) {
			t.Fatalf("conflicting list alias accepted: %s", field)
		}
	}
	raw := []byte(`{"apiVersion":"v1","kind":"List","metadata":{"continue":"","remainingItemCount":0},"items":[]}`)
	var list kubernetesResourceList
	if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
		t.Fatalf("canonical empty list rejected: %s (%v)", raw, err)
	}
}

func TestLocalInventoryRejectsEscapedDuplicateMemberNames(t *testing.T) {
	// Raw member names differ, but JSON decoding gives the same key. Neither
	// the envelope nor an ignored nested extension may hide a second claim.
	for _, raw := range []string{
		`{"items":[],"it\u0065ms":[{"kind":"Secret"}]}`,
		`{"items":[{"extension":{"uid":"first","u\u0069d":"second"}}]}`,
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal([]byte(raw), &list); err != nil {
			t.Fatal(err)
		}
		if !localInventoryIncomplete(list, []byte(raw)) {
			t.Fatalf("decoded duplicate key accepted: %s", raw)
		}
	}
	canonical := []byte(`{"items":[{"extension":{"uid":"first"}}]}`)
	var list kubernetesResourceList
	if err := json.Unmarshal(canonical, &list); err != nil || localInventoryIncomplete(list, canonical) {
		t.Fatalf("canonical extension rejected: %s (%v)", canonical, err)
	}
}

func TestLocalInventoryRejectsNullNestedObjects(t *testing.T) {
	for _, field := range []string{`"spec":{"ref":null}`, `"spec":{"secretRef":null}`, `"spec":{"sourceRef":null}`, `"status":{"artifact":null}`} {
		raw := []byte(`{"items":[{` + field + `}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if !localInventoryIncomplete(list, raw) {
			t.Fatalf("explicit null nested object accepted: %s", field)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{}]}`), []byte(`{"items":[{"spec":{"ref":{}},"status":{"artifact":{}}}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("omitted or object field rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsNullNestedScalarFields(t *testing.T) {
	for _, field := range []string{`"name":null`, `"namespace":null`, `"generation":null`, `"deletionTimestamp":null`} {
		raw := []byte(`{"items":[{"metadata":{` + field + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("null identity accepted: %s (%v)", raw, err)
		}
	}
	for _, field := range []string{`"replicas":null`, `"prune":null`, `"suspend":null`, `"observedGeneration":null`, `"lastAppliedRevision":null`} {
		parent := "spec"
		if strings.Contains(field, "observedGeneration") || strings.Contains(field, "lastAppliedRevision") {
			parent = "status"
		}
		raw := []byte(`{"items":[{"` + parent + `":{` + field + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("null workload field accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"metadata":{"name":"valid"},"spec":{"replicas":1},"status":{"observedGeneration":2}}]}`), []byte(`{"items":[{}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("non-null or omitted field rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsLossyItemIdentity(t *testing.T) {
	for _, field := range []string{"name", "namespace"} {
		raw := []byte(`{"items":[{"metadata":{"` + field + `":"app\ud800"}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if !localInventoryIncomplete(list, raw) {
			t.Fatalf("lossy %s accepted", field)
		}
	}
}

func TestLocalInventoryRejectsDuplicateNamesWithDifferentUIDs(t *testing.T) {
	for _, tc := range []struct {
		items   string
		invalid bool
	}{
		{`{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"other","namespace":"flux-system","uid":"one"}},{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"other","namespace":"flux-system","uid":"two"}}`, true},
		{`{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"other","namespace":"flux-system"}},{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"other","namespace":"flux-system","uid":"two"}}`, true},
		{`{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"other","namespace":"flux-system","uid":"one"}},{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"other","namespace":"elsewhere","uid":"two"}}`, false},
		{`{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"other","namespace":"flux-system"}},{"apiVersion":"v1","kind":"Service","metadata":{"name":"other","namespace":"flux-system"}}`, false},
	} {
		raw := []byte(`{"items":[` + tc.items + `]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if got := localInventoryIncomplete(list, raw); got != tc.invalid {
			t.Fatalf("incomplete=%t want %t for %s", got, tc.invalid, raw)
		}
	}
}

func TestLocalInventoryRejectsSharedUIDAcrossKinds(t *testing.T) {
	// A name may be shared across kinds, but a Kubernetes UID identifies an
	// object independently of its name or GVK in one complete list.
	for _, tc := range []struct {
		secondUID string
		invalid   bool
	}{{"shared", true}, {"distinct", false}} {
		raw := []byte(`{"items":[` +
			`{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"first","namespace":"flux-system","uid":"shared"}},` +
			`{"apiVersion":"v1","kind":"Service","metadata":{"name":"second","namespace":"flux-system","uid":"` + tc.secondUID + `"}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err)
		}
		if got := localInventoryIncomplete(list, raw); got != tc.invalid {
			t.Fatalf("shared UID across kinds incomplete=%t want %t", got, tc.invalid)
		}
	}
}

func TestLocalInventoryRejectsLossyItemEnvelopeIdentity(t *testing.T) {
	for _, field := range []string{"kind", "apiVersion"} {
		raw := []byte(`{"items":[{"` + field + `":"apps\ud800"}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("lossy %s accepted: %s (%v)", field, raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"kind":"Deployment","apiVersion":"apps/v1"}]}`), []byte(`{"items":[{}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid item rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsLossyReferenceIdentity(t *testing.T) {
	for _, tc := range []struct{ parent, field string }{
		{`spec":{"secretRef`, "name"}, {`spec":{"sourceRef`, "kind"},
		{`spec":{"sourceRef`, "name"}, {`spec":{"sourceRef`, "namespace"},
	} {
		raw := []byte(`{"items":[{"` + tc.parent + `":{"` + tc.field + `":"ref\ud800"}}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("lossy reference accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"spec":{"sourceRef":{"kind":"GitRepository","name":"git","namespace":"flux-system"},"secretRef":{"name":"deploy"}}}]}`), []byte(`{"items":[{}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid reference rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsLossySourceAndAppliedRevision(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"spec":{"ref":{"branch":"main\ud800"}}}]}`),
		[]byte(`{"items":[{"status":{"artifact":{"revision":"main\ud800"}}}]}`),
		[]byte(`{"items":[{"status":{"lastAppliedRevision":"main\ud800"}}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("lossy revision accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"spec":{"ref":{"branch":"main"}},"status":{"artifact":{"revision":"main@sha1:abc"},"lastAppliedRevision":"main@sha1:abc"}}]}`),
		[]byte(`{"items":[{}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid revision rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsLossySourceLocation(t *testing.T) {
	for _, field := range []string{"url", "path"} {
		raw := []byte(`{"items":[{"spec":{"` + field + `":"source\ud800"}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("lossy source %s accepted: %s (%v)", field, raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"spec":{"url":"https://example.invalid/repo.git","path":"./clusters/local"}}]}`), []byte(`{"items":[{}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid source location rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsLossyArtifactLocation(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"status":{"artifact":{"url":"https://example.invalid/\ud800"}}}]}`),
		[]byte(`{"items":[{"status":{"artifact":{"url":null}}}]}`),
		[]byte(`{"items":[{"status":{"artifact":{"URL":"https://example.invalid/artifact"}}}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("invalid artifact location accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"status":{"artifact":{"url":"https://example.invalid/artifact"}}}]}`),
		[]byte(`{"items":[{"status":{"artifact":{"revision":"main@sha1:abc"}}}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid artifact location rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsInvisibleLocations(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"spec":{"url":"https://example.invalid/repo\n.git"}}]}`),
		[]byte(`{"items":[{"spec":{"path":"./clusters/\u200blocal"}}]}`),
		[]byte(`{"items":[{"status":{"artifact":{"url":"https://example.invalid/\rartifact"}}}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("invisible location accepted: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsInvisibleRevisions(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"spec":{"ref":{"branch":"main\nother"}}}]}`),
		[]byte(`{"items":[{"spec":{"ref":{"tag":"v1\u200b"}}}]}`),
		[]byte(`{"items":[{"status":{"artifact":{"revision":"main\r@sha1:abc"}}}]}`),
		[]byte(`{"items":[{"status":{"lastAppliedRevision":"main\u200b@sha1:abc"}}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("invisible revision accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"spec":{"ref":{"branch":"main"}},"status":{"artifact":{"revision":"main@sha1:abc"},"lastAppliedRevision":"main@sha1:abc"}}]}`),
		[]byte(`{"items":[{}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid revision rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsNullReferenceScalars(t *testing.T) {
	for _, tc := range []struct{ parent, field string }{
		{`spec":{"ref`, `branch`}, {`spec":{"ref`, `tag`}, {`spec":{"ref`, `semver`},
		{`spec":{"ref`, `commit`}, {`spec":{"ref`, `name`},
		{`spec":{"secretRef`, `name`}, {`spec":{"sourceRef`, `kind`},
		{`spec":{"sourceRef`, `name`}, {`spec":{"sourceRef`, `namespace`},
		{`status":{"artifact`, `revision`},
	} {
		raw := []byte(`{"items":[{"` + tc.parent + `":{"` + tc.field + `":null}}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("null reference field accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"spec":{"ref":{"branch":"main"},"sourceRef":{"name":"git"}},"status":{"artifact":{"revision":"main@sha1:abc"}}}]}`), []byte(`{"items":[{}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("non-null reference field rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsMalformedUnrelatedConditions(t *testing.T) {
	for _, field := range []string{`{}`, `{"type":""}`, `{"type":"Ready"}`, `{"type":"Ready","status":"Maybe"}`} {
		raw := []byte(`{"items":[{"status":{"conditions":[` + field + `]}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("malformed unrelated condition accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"status":{"conditions":[{"type":"Ready","status":"Unknown"}]}}]}`), []byte(`{"items":[{}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid unrelated condition rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryConditionsRequireArrayBeforeInspection(t *testing.T) {
	for _, conditions := range []string{`{}`, `"Ready"`, `42`, `true`} {
		raw := []byte(`{"items":[{"status":{"conditions":` + conditions + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err == nil {
			t.Fatalf("non-array conditions decoded as complete inventory: %s", raw)
		}
	}
	for _, conditions := range []string{`[]`, `[{"type":"Ready","status":"True"}]`} {
		raw := []byte(`{"items":[{"status":{"conditions":` + conditions + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid conditions rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsMalformedConditionTransitionTime(t *testing.T) {
	for _, tc := range []struct {
		field   string
		invalid bool
	}{
		{``, false}, // projected conditions need not report a transition time
		{`,"lastTransitionTime":"2026-10-04T01:02:03Z"`, false},
		{`,"lastTransitionTime":"2026-10-04T01:02:03+01:00"`, false},
		{`,"lastTransitionTime":null`, true},
		{`,"lastTransitionTime":""`, true},
		{`,"lastTransitionTime":"yesterday"`, true},
		{`,"lastTransitionTime":"2026-02-30T01:02:03Z"`, true},
		{`,"LastTransitionTime":"2026-10-04T01:02:03Z"`, true},
	} {
		raw := []byte(`{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"` + tc.field + `}]}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("condition transition time classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryConditionTransitionRequiresUsableCreationWhenReported(t *testing.T) {
	for _, tc := range []struct {
		creation string
		invalid  bool
	}{
		{``, false}, // older projections can omit creation
		{`,"creationTimestamp":"2026-10-04T01:02:03Z"`, false},
		{`,"creationTimestamp":"2026-10-04T01:02:04Z"`, true},
		{`,"creationTimestamp":"not-a-time"`, true},
	} {
		raw := []byte(`{"items":[{"metadata":{"name":"unrelated"` + tc.creation + `},"status":{"conditions":[{"type":"Ready","status":"True","lastTransitionTime":"2026-10-04T01:02:03Z"}]}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("reported condition/creation classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryConditionCannotPrecedeCreation(t *testing.T) {
	for _, tc := range []struct {
		creation, transition string
		invalid              bool
	}{
		{`"2026-10-04T02:00:00Z"`, `"2026-10-04T01:59:59Z"`, true},
		{`"2026-10-04T02:00:00+01:00"`, `"2026-10-04T01:30:00Z"`, false},
		{`"2026-10-04T02:00:00Z"`, `"2026-10-04T02:00:00Z"`, false},
		{`"2026-10-04T02:00:00Z"`, `"2026-10-04T02:00:01Z"`, false},
	} {
		raw := []byte(`{"items":[{"metadata":{"creationTimestamp":` + tc.creation + `},"status":{"conditions":[{"type":"Ready","status":"True","lastTransitionTime":` + tc.transition + `}]}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("condition lifecycle classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsDuplicatedUnrelatedConditionTypes(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"},{"type":"Ready","status":"False"}]}}]}`),
		[]byte(`{"items":[{"status":{"conditions":[{"type":"Available","status":"True"},{"type":"Available","status":"True"}]}}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("ambiguous unrelated conditions accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"},{"type":"Available","status":"False"}]}}]}`),
		[]byte(`{"items":[{}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("distinct condition types rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsInvalidUTF8(t *testing.T) {
	for _, raw := range [][]byte{
		append([]byte(`{"items":[{"metadata":{"name":"other`), append([]byte{0xff}, []byte(`"}}]}`)...)...),
		append([]byte(`{"items":[{}],"extra":"`), append([]byte{0xfe}, []byte(`"}`)...)...),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil {
			t.Fatal(err) // Go replaces invalid UTF-8 instead of rejecting it.
		}
		if !localInventoryIncomplete(list, raw) {
			t.Fatal("invalid UTF-8 inventory accepted")
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"metadata":{"name":"cafe","annotation":"café"}}]}`), []byte(`{"items":[]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("valid UTF-8 rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsInvisibleReportedNamesAndKinds(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"metadata":{"name":"hidden\nname"}}]}`),
		[]byte(`{"items":[{"metadata":{"namespace":"hidden\u200bnamespace"}}]}`),
		[]byte(`{"items":[{"kind":"Git\tRepository"}]}`),
		[]byte(`{"items":[{"apiVersion":"v1\u200b"}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("invisible inventory identifier accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{
		[]byte(`{"items":[{"metadata":{"name":"valid-name","namespace":"flux-system"},"kind":"GitRepository","apiVersion":"source.toolkit.fluxcd.io/v1"}]}`),
		[]byte(`{"items":[{}]}`),
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("visible or omitted inventory identifiers rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsUnusableItemResourceVersion(t *testing.T) {
	for _, raw := range []string{
		`{"items":[{"metadata":{"resourceVersion":""}}]}`,
		`{"items":[{"metadata":{"resourceVersion":"8\n9"}}]}`,
		`{"items":[{"metadata":{"resourceVersion":"8\ud800"}}]}`,
		`{"items":[{"metadata":{"resourceVersion":null}}]}`,
		`{"items":[{"metadata":{"ResourceVersion":"8"}}]}`,
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal([]byte(raw), &list); err != nil || !localInventoryIncomplete(list, []byte(raw)) {
			t.Fatalf("unusable item resource version accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range []string{
		`{"items":[{"metadata":{"resourceVersion":"8"}}]}`,
		`{"items":[{"metadata":{}}]}`,
	} {
		var list kubernetesResourceList
		if err := json.Unmarshal([]byte(raw), &list); err != nil || localInventoryIncomplete(list, []byte(raw)) {
			t.Fatalf("valid or omitted resource version rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryCorruptionDegradesAllFixedProfileReads(t *testing.T) {
	for _, mutate := range []func(string) string{
		func(raw string) string {
			return strings.Replace(raw, `"items":[`, `"items":[{"metadata":{"generation":1},"status":{"observedGeneration":2}},`, 1)
		},
		func(raw string) string { return raw[:len(raw)-1] + `,"extra":"` + string([]byte{0xff}) + `"}` },
	} {
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: mutate(localControllerJSON())}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("corrupt controller accepted: %#v", check)
		}
		check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: mutate(localFluxJSON())}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("corrupt Flux resource accepted: %#v", check)
		}
		workloads := localWorkloadResponses()
		workloads[0].output = mutate(workloads[0].output)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, "workload response was incomplete") {
			t.Fatalf("corrupt workload accepted: %#v", checks)
		}
	}
}

func TestLocalInventoryRejectsUnrelatedObservedGenerationContradictions(t *testing.T) {
	for _, observed := range []string{`-1`, `3`} {
		raw := []byte(`{"items":[{"metadata":{"generation":2},"status":{"observedGeneration":` + observed + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("contradictory observed generation accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"metadata":{"generation":2},"status":{"observedGeneration":1}}]}`), []byte(`{"items":[{"status":{"observedGeneration":0}}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("older or omitted generation rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryDistinguishesZeroAndOmittedGeneration(t *testing.T) {
	for _, tc := range []struct {
		raw     string
		invalid bool
	}{
		{`{"items":[{"metadata":{"generation":0},"status":{"observedGeneration":1}}]}`, true},
		{`{"items":[{"metadata":{"generation":0},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}]}`, true},
		{`{"items":[{"status":{"observedGeneration":1,"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}}]}`, false},
		{`{"items":[{"metadata":{"generation":0},"status":{"observedGeneration":0,"conditions":[{"type":"Ready","status":"True","observedGeneration":0}]}}]}`, false},
	} {
		raw := []byte(tc.raw)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("explicit/omitted generation classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsConditionAheadOfStatusGeneration(t *testing.T) {
	for _, tc := range []struct {
		observed string
		invalid  bool
	}{
		{`"observedGeneration":1,`, true},
		{`"observedGeneration":2,`, false},
		{``, false}, // projected fixtures may omit the top-level status generation
	} {
		raw := []byte(`{"items":[{"metadata":{"generation":2},"status":{` + tc.observed + `"conditions":[{"type":"Ready","status":"True","observedGeneration":2}]}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("condition/status generation classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsNegativeReplicaCountersOnUnrelatedMembers(t *testing.T) {
	for _, field := range []string{"replicas", "availableReplicas", "unavailableReplicas", "readyReplicas", "updatedReplicas"} {
		raw := []byte(`{"items":[{"status":{"` + field + `":-1}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("negative %s accepted: %s (%v)", field, raw, err)
		}
	}
	for _, tc := range []struct {
		raw     string
		invalid bool
	}{
		{`{"items":[{"spec":{"replicas":-1}}]}`, true},
		{`{"items":[{"spec":{"replicas":0},"status":{"replicas":0,"availableReplicas":0,"unavailableReplicas":0,"readyReplicas":0,"updatedReplicas":0}}]}`, false},
	} {
		raw := []byte(tc.raw)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("replica counter classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsRepeatedOrInvalidUID(t *testing.T) {
	member := `{"kind":"Deployment","metadata":{"namespace":"shirokuma-dev","name":"unrelated","uid":"uid-one"}}`
	for _, tc := range []struct {
		members string
		invalid bool
	}{
		{member + `,{"kind":"StatefulSet","metadata":{"namespace":"shirokuma-dev","name":"other","uid":"uid-one"}}`, true},
		{member + `,{"kind":"StatefulSet","metadata":{"namespace":"shirokuma-dev","name":"other","uid":"uid-two"}}`, false},
		{`{"metadata":{"uid":""}}`, true},
		{`{"metadata":{"uid":null}}`, true},
		{`{"metadata":{"uid":" "}}`, true},
		{`{"metadata":{"uid":"uid\u200b-one"}}`, true},
		{`{"metadata":{"uid":"uid\ud800-one"}}`, true}, // Go replaces an unpaired JSON surrogate.
		{`{"metadata":{"uid":"` + strings.Repeat("u", 1025) + `"}}`, true},
		{`{"metadata":{"uid":"` + strings.Repeat("u", 1024) + `"}}`, false},
		{`{"metadata":{"uid":"uid-one"}}`, false},
		{`{"status":{}},{"status":{}}`, false}, // projected fixture identities remain optional
	} {
		raw := []byte(`{"items":[` + tc.members + `]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("resource UID classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsLossyConditionType(t *testing.T) {
	for _, tc := range []struct {
		typeJSON string
		invalid  bool
	}{
		{`"Ready\ud800"`, true},
		{`"Ready\ufffd"`, true},
		{`"Ready"`, false},
	} {
		raw := []byte(`{"items":[{"status":{"conditions":[{"type":` + tc.typeJSON + `,"status":"True"}]}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("condition type classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsReplacementCharacterInListVersion(t *testing.T) {
	for _, tc := range []struct {
		version string
		invalid bool
	}{
		{`"rv\ud800"`, true}, // An escaped unpaired surrogate silently decodes as U+FFFD.
		{`"rv\ufffd"`, true},
		{`"rv-opaque"`, false},
	} {
		raw := []byte(`{"metadata":{"resourceVersion":` + tc.version + `},"items":[]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("list version classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryBoundsIgnoredExtensionDepth(t *testing.T) {
	for _, tc := range []struct {
		depth   int
		invalid bool
	}{
		{16, false},
		{65, true},
	} {
		raw := []byte(`{"items":[{"extension":` + strings.Repeat(`[`, tc.depth) + `"ok"` + strings.Repeat(`]`, tc.depth) + `}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("extension depth %d classification incorrect: %v", tc.depth, err)
		}
	}
}

func TestLocalInventoryIdentityFieldsDoNotCollideOnSeparator(t *testing.T) {
	for _, tc := range []struct {
		body    string
		invalid bool
	}{
		{`{"items":[{"apiVersion":"example.io/v1","kind":"Widget","metadata":{"namespace":"ns","name":"one"}},{"apiVersion":"example.io","kind":"v1/Widget","metadata":{"namespace":"ns","name":"one"}}]}`, false},
		{`{"items":[{"apiVersion":"example.io/v1","kind":"Widget","metadata":{"namespace":"ns","name":"one"}},{"apiVersion":"example.io/v1","kind":"Widget","metadata":{"namespace":"ns","name":"one"}}]}`, true},
	} {
		raw := []byte(tc.body)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("reported identity classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsLossyIgnoredValues(t *testing.T) {
	for _, tc := range []struct {
		body    string
		invalid bool
	}{
		{`{"extension":{"nested":["ok", "\ud800"]},"items":[]}`, true},
		{`{"items":[{"extension":"\ud800"}]}`, true},
		{`{"items":[{"extension":"\ufffd"}]}`, true},
		{`{"extension":{"nested":["雪"]},"items":[]}`, false},
	} {
		raw := []byte(tc.body)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("ignored value classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsEscapedDuplicateExtensionKey(t *testing.T) {
	for _, tc := range []struct {
		body    string
		invalid bool
	}{
		{`{"extension":{"owner":1,"ow\u006eer":2},"items":[]}`, true},
		{`{"items":[{"extension":{"owner":1,"ow\u006eer":2}}]}`, true},
		{`{"extension":{"owner":1,"other":2},"items":[]}`, false},
	} {
		raw := []byte(tc.body)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("escaped extension key classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRequiresCanonicalUIDKey(t *testing.T) {
	for _, uidField := range []string{`"UID":"uid-one"`, `"Uid":"uid-one"`, `"uid":"uid-one","UID":"uid-two"`} {
		raw := []byte(`{"items":[{"metadata":{` + uidField + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("noncanonical UID accepted: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsMalformedDeletionTimestamp(t *testing.T) {
	for _, tc := range []struct {
		value   string
		invalid bool
	}{
		{`"not-a-time"`, true},
		{`"2026-10-03"`, true},
		{`"2026-10-03T12:00:00,123Z"`, true},
		{`"2026-10-03T12:00:00+24:00"`, true},
		{`"2026-10-03T12:00:00.1234567890Z"`, true},
		{`"2026-10-03T12:00:00Z"`, false},
		{`"2026-10-03T12:00:00.123456789Z"`, false},
		{`"2026-10-03T12:00:00+09:00"`, false},
		{`""`, true}, // a reported empty timestamp is not an omitted projection
	} {
		raw := []byte(`{"items":[{"metadata":{"deletionTimestamp":` + tc.value + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("deletion timestamp classification incorrect: %s (%v)", raw, err)
		}
	}
	raw := []byte(`{"items":[{"metadata":{}}]}`)
	var list kubernetesResourceList
	if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
		t.Fatalf("omitted deletion timestamp rejected: %s (%v)", raw, err)
	}
}

func TestLocalInventoryRejectsMalformedCreationTimestamp(t *testing.T) {
	for _, tc := range []struct {
		value   string
		invalid bool
	}{
		{`"not-a-time"`, true},
		{`""`, true},
		{`null`, true},
		{`"2026-10-03T12:00:00+24:00"`, true},
		{`"2026-10-03T12:00:00.123456789Z"`, false},
	} {
		raw := []byte(`{"items":[{"kind":"Service","metadata":{"creationTimestamp":` + tc.value + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("creation timestamp classification incorrect: %s (%v)", raw, err)
		}
	}
	raw := []byte(`{"items":[{"metadata":{}}]}`)
	var list kubernetesResourceList
	if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
		t.Fatalf("omitted creation timestamp rejected: %s (%v)", raw, err)
	}
}

func TestLocalInventoryRejectsNullGeneration(t *testing.T) {
	for _, tc := range []struct {
		metadata string
		invalid  bool
	}{
		{`"generation":null`, true},
		{`"generation":0`, false},
		{`"generation":1`, false},
		{`"name":"other"`, false},
	} {
		raw := []byte(`{"items":[{"kind":"Service","metadata":{` + tc.metadata + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("generation classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsObscuredMemberUID(t *testing.T) {
	for _, tc := range []struct {
		uid     string
		invalid bool
	}{
		{`"uid":"a b"`, true},
		{`"uid":"a\u200bb"`, true},
		{`"uid":"a\u0000b"`, true},
		{`"uid":"a-b"`, false},
		{`"name":"other"`, false},
	} {
		raw := []byte(`{"items":[{"kind":"Service","metadata":{` + tc.uid + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("UID classification incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsDeletionBeforeCreation(t *testing.T) {
	for _, tc := range []struct {
		metadata string
		invalid  bool
	}{
		{`"creationTimestamp":"2026-10-03T12:00:00Z","deletionTimestamp":"2026-10-03T11:59:59Z"`, true},
		{`"creationTimestamp":"2026-10-03T12:00:00+09:00","deletionTimestamp":"2026-10-03T03:00:01Z"`, false},
		{`"creationTimestamp":"2026-10-03T12:00:00Z","deletionTimestamp":"2026-10-03T12:00:00Z"`, false},
		{`"creationTimestamp":"2026-10-03T12:00:00Z"`, false},
		{`"deletionTimestamp":"2026-10-03T12:00:00Z"`, false},
	} {
		raw := []byte(`{"items":[{"kind":"Service","metadata":{` + tc.metadata + `}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) != tc.invalid {
			t.Fatalf("creation/deletion order incorrect: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsAvailableGreaterThanReady(t *testing.T) {
	for _, status := range []string{
		`{"availableReplicas":2,"readyReplicas":1}`,
		`{"replicas":3,"availableReplicas":2,"readyReplicas":1}`,
	} {
		raw := []byte(`{"items":[{"kind":"Deployment","status":` + status + `}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("available replicas exceeded ready replicas: %s (%v)", raw, err)
		}
	}
	for _, status := range []string{
		`{"replicas":3,"availableReplicas":1,"readyReplicas":2}`,
		`{"availableReplicas":2}`,
		`{"readyReplicas":1}`,
	} {
		raw := []byte(`{"items":[{"status":` + status + `}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("compatible replica counters rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsUnavailableGreaterThanDesired(t *testing.T) {
	for _, rawText := range []string{
		`{"items":[{"kind":"Deployment","spec":{"replicas":1},"status":{"unavailableReplicas":2}}]}`,
		`{"items":[{"kind":"Deployment","spec":{"replicas":0},"status":{"unavailableReplicas":1}}]}`,
	} {
		raw := []byte(rawText)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("unavailable exceeds desired: %s (%v)", raw, err)
		}
	}
	for _, rawText := range []string{
		`{"items":[{"spec":{"replicas":2},"status":{"unavailableReplicas":2}}]}`,
		`{"items":[{"status":{"unavailableReplicas":2}}]}`,
		`{"items":[{"spec":{"replicas":0},"status":{"unavailableReplicas":0}}]}`,
	} {
		raw := []byte(rawText)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("compatible unavailable count rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsAvailableAndUnavailableExceedingDesired(t *testing.T) {
	for _, status := range []string{
		`{"availableReplicas":2,"unavailableReplicas":1}`,
		`{"replicas":3,"availableReplicas":2,"unavailableReplicas":2}`,
	} {
		raw := []byte(`{"items":[{"kind":"Deployment","spec":{"replicas":2},"status":` + status + `}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("contradictory available/unavailable counts accepted: %s (%v)", raw, err)
		}
	}
	for _, status := range []string{
		`{"availableReplicas":1,"unavailableReplicas":1}`,
		`{"replicas":3,"availableReplicas":3,"unavailableReplicas":0}`,
		`{"availableReplicas":2}`,
		`{"unavailableReplicas":2}`,
	} {
		raw := []byte(`{"items":[{"kind":"Deployment","spec":{"replicas":2},"status":` + status + `}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("compatible available/unavailable counts rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsNullConditionFields(t *testing.T) {
	for _, field := range []string{`"type":null`, `"status":null`, `"observedGeneration":null`} {
		raw := []byte(`{"items":[{"status":{"conditions":[{` + field + `}]}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("null condition field accepted: %s (%v)", raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"}]}}]}`), []byte(`{"items":[{"status":{"conditions":[]}}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("non-null or omitted condition field rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsNullMembers(t *testing.T) {
	for _, tc := range []struct{ name, raw string }{
		{"list item", `{"items":[{},null]}`},
		{"condition entry", `{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"},null]}}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var list kubernetesResourceList
			if err := json.Unmarshal([]byte(tc.raw), &list); err != nil {
				t.Fatal(err)
			}
			if !localInventoryIncomplete(list, []byte(tc.raw)) {
				t.Fatalf("null member accepted: %s", tc.raw)
			}
		})
	}
	for _, raw := range []string{`{"items":[{}]}`, `{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"}]}}]}`} {
		var list kubernetesResourceList
		if err := json.Unmarshal([]byte(raw), &list); err != nil || localInventoryIncomplete(list, []byte(raw)) {
			t.Fatalf("non-null member rejected: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryRejectsNullReplicaCounters(t *testing.T) {
	for _, field := range []string{"replicas", "availableReplicas", "unavailableReplicas", "readyReplicas", "updatedReplicas"} {
		parent := "status"
		if field == "replicas" {
			parent = "spec"
		}
		raw := []byte(`{"items":[{"` + parent + `":{"` + field + `":null}}]}`)
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || !localInventoryIncomplete(list, raw) {
			t.Fatalf("null %s.%s accepted: %s (%v)", parent, field, raw, err)
		}
	}
	for _, raw := range [][]byte{[]byte(`{"items":[{"status":{"readyReplicas":0}}]}`), []byte(`{"items":[{"status":{}}]}`)} {
		var list kubernetesResourceList
		if err := json.Unmarshal(raw, &list); err != nil || localInventoryIncomplete(list, raw) {
			t.Fatalf("replica counter boundary: %s (%v)", raw, err)
		}
	}
}

func TestLocalInventoryMalformedMembersDegradeFixedProfile(t *testing.T) {
	for _, member := range []string{
		`null`,
		`{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":1},"status":{"conditions":[null]}}`,
		`{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"bogus"}]}}`,
		`{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":1},"spec":{"sourceRef":{"name":null}}}`,
	} {
		decorate := func(raw string) string {
			return strings.Replace(raw, `"items":[`, `"items":[`+member+`,`, 1)
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: decorate(localControllerJSON())}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("controller member %s accepted: %#v", member, check)
		}
		check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: decorate(localFluxJSON())}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("Flux member %s accepted: %#v", member, check)
		}
		workloads := localWorkloadResponses()
		workloads[0].output = decorate(workloads[0].output)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[0].Status != "degraded" {
			t.Fatalf("Lakehouse member %s accepted: %#v", member, checks)
		}
	}
}

func TestLocalInventoryRejectsAliasedItemIdentity(t *testing.T) {
	for _, field := range []string{"name", "namespace", "generation", "deletionTimestamp"} {
		alias := strings.ToUpper(field[:1]) + field[1:]
		decorate := func(raw string) string {
			if field == "deletionTimestamp" {
				return strings.Replace(raw, `"generation":`, `"`+alias+`":"","generation":`, 1)
			}
			return strings.Replace(raw, `"`+field+`":`, `"`+alias+`":`, 1)
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: decorate(localControllerJSON())}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("controller item alias %s accepted: %#v", field, check)
		}
		check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: decorate(localFluxJSON())}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("Flux item alias %s accepted: %#v", field, check)
		}
		workloads := localWorkloadResponses()
		workloads[0].output = decorate(workloads[0].output)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[0].Status != "degraded" {
			t.Fatalf("workload item alias %s accepted: %#v", field, checks)
		}
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("canonical item identity rejected: %#v", check)
	}
}

func TestLocalInventoryRejectsAliasedItemEnvelope(t *testing.T) {
	for _, field := range []string{"kind", "apiVersion", "metadata", "spec", "status"} {
		alias := strings.ToUpper(field[:1]) + field[1:]
		decorate := func(raw string) string {
			// Insert a harmless alias alongside the canonical field where available;
			// the inventory itself, not just the later readiness check, must fail.
			marker := `"metadata":{`
			if field == "metadata" {
				return strings.Replace(raw, marker, `"Metadata":{},`+marker, 1)
			}
			if field == "kind" || field == "apiVersion" {
				return strings.Replace(raw, marker, `"`+alias+`":"`+map[string]string{"kind": "Deployment", "apiVersion": "apps/v1"}[field]+`",`+marker, 1)
			}
			return strings.Replace(raw, `"`+field+`":`, `"`+alias+`":{},"`+field+`":`, 1)
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: decorate(localControllerJSON())}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("controller item alias %s accepted: %#v", field, check)
		}
		check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: decorate(localFluxJSON())}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("Flux item alias %s accepted: %#v", field, check)
		}
		workloads := localWorkloadResponses()
		workloads[0].output = decorate(workloads[0].output)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[0].Status != "degraded" {
			t.Fatalf("workload item alias %s accepted: %#v", field, checks)
		}
	}
}

func TestLocalInventoryRejectsAliasedNestedFields(t *testing.T) {
	for _, tc := range []struct{ marker, alias string }{
		{`"replicas":1`, `"Replicas":1`},
		{`"availableReplicas":1`, `"AvailableReplicas":1`},
		{`"conditions":[`, `"Conditions":[]`},
		{`"type":"Available"`, `"Type":"Available"`},
		{`"status":"True"`, `"Status":"True"`},
		{`"observedGeneration":2`, `"ObservedGeneration":2`},
	} {
		decorate := func(raw string) string {
			return strings.Replace(raw, tc.marker, tc.alias+`,`+tc.marker, 1)
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: decorate(localControllerJSON())}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("controller alias %s accepted: %#v", tc.alias, check)
		}
		workloads := localWorkloadResponses()
		workloads[1].output = decorate(workloads[1].output)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[2].Status != "degraded" {
			t.Fatalf("workload alias %s accepted: %#v", tc.alias, checks)
		}
	}
	for _, tc := range []struct{ marker, alias string }{
		{`"branch":"main"`, `"Branch":"main"`},
		{`"secretRef":{`, `"SecretRef":{}`},
		{`"revision":"main@sha1:`, `"Revision":"unused"`},
		{`"sourceRef":{`, `"SourceRef":{}`},
		{`"path":"./deploy/gitops`, `"Path":"unused"`},
		{`"type":"Ready"`, `"Type":"Ready"`},
	} {
		decorated := strings.Replace(localFluxJSON(), tc.marker, tc.alias+`,`+tc.marker, 1)
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: decorated}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("Flux alias %s accepted: %#v", tc.alias, check)
		}
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("canonical nested fields rejected: %#v", check)
	}
}

func TestLocalInventoryRejectsExplicitNullItemFields(t *testing.T) {
	for _, key := range []string{"kind", "apiVersion", "metadata", "spec", "status"} {
		// Null on an otherwise unrelated item can previously be skipped while
		// the expected resources still make the inventory look complete.
		foreign := `{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":1},"spec":{},"status":{}}`
		if key == "apiVersion" {
			foreign = strings.TrimSuffix(foreign, "}") + `,"apiVersion":null}`
		} else {
			marker := map[string]string{"kind": `"Deployment"`, "metadata": `{"name":"unrelated","namespace":"flux-system","generation":1}`, "spec": `{}`, "status": `{}`}[key]
			foreign = strings.Replace(foreign, `"`+key+`":`+marker, `"`+key+`":null`, 1)
		}
		flux := strings.Replace(localFluxJSON(), `"items":[`, `"items":[`+foreign+`,`, 1)
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: flux}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("null item %s accepted: %#v", key, check)
		}
		workloads := localWorkloadResponses()
		workloads[0].output = strings.Replace(workloads[0].output, `"items":[`, `"items":[`+foreign+`,`, 1)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, "incomplete") {
			t.Fatalf("null workload item %s accepted: %#v", key, checks)
		}
	}
	controller := strings.Replace(localControllerJSON(), `"spec":{"replicas":1}`, `"spec":null`, 1)
	if controller == localControllerJSON() {
		t.Fatal("controller fixture did not change")
	}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controller}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
		t.Fatalf("null controller spec accepted: %#v", check)
	}
}

func TestLocalInventoryRejectsMalformedUnrelatedConditionTypes(t *testing.T) {
	for _, conditionType := range []string{`" "`, `"Ready\nspoof"`, `"` + strings.Repeat("x", 317) + `"`} {
		foreign := `{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":` + conditionType + `,"status":"True"}]}}`
		resources := strings.Replace(localFluxJSON(), `"items":[`, `"items":[`+foreign+`,`, 1)
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("malformed unrelated condition type accepted: %#v", check)
		}
		workloads := localWorkloadResponses()
		workloads[1].output = strings.Replace(workloads[1].output, `"items":[`, `"items":[`+foreign+`,`, 1)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "incomplete") {
			t.Fatalf("malformed unrelated workload condition type accepted: %#v", checks)
		}
	}
}

func TestLocalInventoryRejectsImpossibleUnrelatedReplicaCounters(t *testing.T) {
	for _, status := range []string{
		`{"replicas":1,"readyReplicas":2}`,
		`{"replicas":1,"updatedReplicas":2}`,
		`{"replicas":1,"availableReplicas":2}`,
		`{"replicas":3,"readyReplicas":1,"availableReplicas":2}`,
	} {
		foreign := `{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":1},"spec":{"replicas":1},"status":` + status + `}`
		controller := strings.Replace(localControllerJSON(), `"items":[`, `"items":[`+foreign+`,`, 1)
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controller}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("impossible controller counters %s accepted: %#v", status, check)
		}
		workloads := localWorkloadResponses()
		workloads[1].output = strings.Replace(workloads[1].output, `"items":[`, `"items":[`+foreign+`,`, 1)
		if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "incomplete") {
			t.Fatalf("impossible workload counters %s accepted: %#v", status, checks)
		}
	}
	// During a rolling update total may exceed desired; that alone is not contradictory.
	valid := `{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"unrelated","namespace":"shirokuma-dev","generation":1},"spec":{"replicas":1},"status":{"replicas":2,"readyReplicas":2,"updatedReplicas":2,"availableReplicas":2}}`
	workloads := localWorkloadResponses()
	workloads[1].output = strings.Replace(workloads[1].output, `"items":[`, `"items":[`+valid+`,`, 1)
	if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[2].Status != "healthy" {
		t.Fatalf("valid rolling update rejected: %#v", checks)
	}
}

func TestLocalInventoryRejectsFutureUnrelatedConditionGeneration(t *testing.T) {
	for _, generation := range []string{"-1", "2"} {
		foreign := `{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":` + generation + `}]}}`
		resources := strings.Replace(localFluxJSON(), `"items":[`, `"items":[`+foreign+`,`, 1)
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
			t.Fatalf("unrelated condition generation %s accepted: %#v", generation, check)
		}
		controllers := strings.Replace(localControllerJSON(), `"items":[`, `"items":[`+foreign+`,`, 1)
		check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
			t.Fatalf("unrelated controller condition generation %s accepted: %#v", generation, check)
		}
	}
}

func TestLocalInventoryRejectsNegativeUnrelatedItemGeneration(t *testing.T) {
	foreign := `{"kind":"Deployment","metadata":{"name":"unrelated","namespace":"flux-system","generation":-1}}`
	resources := strings.Replace(localFluxJSON(), `"items":[`, `"items":[`+foreign+`,`, 1)
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
		t.Fatalf("negative unrelated Flux generation accepted: %#v", check)
	}
	controllers := strings.Replace(localControllerJSON(), `"items":[`, `"items":[`+foreign+`,`, 1)
	check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
		t.Fatalf("negative unrelated controller generation accepted: %#v", check)
	}
	workloads := localWorkloadResponses()
	workloads[1].output = strings.Replace(workloads[1].output, `"items":[`, `"items":[`+foreign+`,`, 1)
	if checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: workloads}, localExperimentContext); checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "incomplete") {
		t.Fatalf("negative unrelated workload generation accepted: %#v", checks)
	}
}

func TestLocalFluxRejectsDuplicatedConditionTypes(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `{"type":"Available","status":"True"}`, `{"type":"Available","status":"True"},{"type":"Available","status":"True"}`, 1)
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "degraded" {
		t.Fatalf("duplicate controller availability accepted: %#v", check)
	}
	resources := strings.Replace(localFluxJSON(), `{"type":"Ready","status":"True","observedGeneration":1}`, `{"type":"Ready","status":"True","observedGeneration":1},{"type":"Ready","status":"True","observedGeneration":1}`, 1)
	responses := []fakeResponse{{output: localControllerJSON()}, {output: resources}}
	check = checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
		t.Fatalf("duplicate source readiness accepted: %#v", check)
	}
	generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
	if strings.Contains(generic.Summary, "duplicated condition type") {
		t.Fatalf("generic profile inherited local condition check: %#v", generic)
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("valid fixed inventory rejected: %#v", check)
	}
}

func TestLocalLakehouseRejectsDuplicatedConditionTypes(t *testing.T) {
	responses := localWorkloadResponses()
	for i := range responses {
		responses[i].output = strings.Replace(responses[i].output, `{"type":"Available","status":"True"}`, `{"type":"Available","status":"True"},{"type":"Available","status":"True"}`, 1)
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		if i == 1 && checks[2].Status != "degraded" {
			t.Fatalf("duplicate dev deployment condition accepted: %#v", checks)
		}
	}
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: localWorkloadResponses()}, localExperimentContext)
	for _, check := range checks {
		if check.Status != "healthy" {
			t.Fatalf("valid workload rejected: %#v", check)
		}
	}
}

func TestLocalDeclaredSourceRejectsCompetingRevisionSelectors(t *testing.T) {
	for _, selector := range []string{"tag", "semver", "commit", "name"} {
		var list map[string]interface{}
		if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
			t.Fatal(err)
		}
		ref := list["items"].([]interface{})[0].(map[string]interface{})["spec"].(map[string]interface{})["ref"].(map[string]interface{})
		ref[selector] = "untrusted-revision"
		output, err := json.Marshal(list)
		if err != nil {
			t.Fatal(err)
		}
		responses := []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "competing source revision selector") || strings.Contains(check.Summary, "untrusted-revision") {
			t.Fatalf("selector %s accepted or echoed: %#v", selector, check)
		}
		generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
		if strings.Contains(generic.Summary, "competing source revision selector") {
			t.Fatalf("generic profile inherited local check: %#v", generic)
		}
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("declared branch-only source rejected: %#v", check)
	}
}

func TestLocalDeclaredFluxAPIVersions(t *testing.T) {
	for _, test := range []struct {
		index   int
		version string
	}{
		{0, "source.toolkit.fluxcd.io/v1"},
		{1, "kustomize.toolkit.fluxcd.io/v1"},
		{3, "kustomize.toolkit.fluxcd.io/v1"},
	} {
		for _, version := range []string{test.version, "wrong.example/v1"} {
			var list map[string]interface{}
			if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
				t.Fatal(err)
			}
			list["items"].([]interface{})[test.index].(map[string]interface{})["apiVersion"] = version
			output, err := json.Marshal(list)
			if err != nil {
				t.Fatal(err)
			}
			responses := []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
			if version == test.version && check.Status != "informational" || version != test.version && (check.Status != "degraded" || !strings.Contains(check.Summary, "declared API version mismatch") || strings.Contains(check.Summary, version)) {
				t.Fatalf("index %d version %q: %#v", test.index, version, check)
			}
			generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
			if strings.Contains(generic.Summary, "declared API version mismatch") {
				t.Fatalf("generic inherited local version check: %#v", generic)
			}
		}
	}
}

func TestLocalOtherFluxResourceAPIVersions(t *testing.T) {
	for _, test := range []struct{ kind, expected string }{
		{"OCIRepository", "source.toolkit.fluxcd.io/v1"},
		{"Bucket", "source.toolkit.fluxcd.io/v1"},
		{"HelmRepository", "source.toolkit.fluxcd.io/v1"},
		{"HelmChart", "source.toolkit.fluxcd.io/v1"},
		{"HelmRelease", "helm.toolkit.fluxcd.io/v2"},
	} {
		for _, version := range []string{"", test.expected, "wrong.example/v1"} {
			var list map[string]interface{}
			if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
				t.Fatal(err)
			}
			list["items"] = append(list["items"].([]interface{}), map[string]interface{}{
				"kind": test.kind, "apiVersion": version,
				"metadata": map[string]interface{}{"name": "another-resource", "namespace": "flux-system", "generation": 1},
				"status":   map[string]interface{}{"conditions": []interface{}{map[string]interface{}{"type": "Ready", "status": "True", "observedGeneration": 1}}},
			})
			output, err := json.Marshal(list)
			if err != nil {
				t.Fatal(err)
			}
			responses := []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
			if version == "" {
				if check.Status != "degraded" || !strings.Contains(check.Summary, "response was incomplete") {
					t.Fatalf("%s empty reported version accepted: %#v", test.kind, check)
				}
			} else if version == "wrong.example/v1" {
				if check.Status != "degraded" || !strings.Contains(check.Summary, "declared API version mismatch") || strings.Contains(check.Summary, version) {
					t.Fatalf("%s %q accepted or echoed: %#v", test.kind, version, check)
				}
			} else if check.Status != "informational" {
				t.Fatalf("%s %q rejected: %#v", test.kind, version, check)
			}
			generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
			if strings.Contains(generic.Summary, "declared API version mismatch") {
				t.Fatalf("generic profile inherited local version check: %#v", generic)
			}
		}
	}
}

func TestLocalActiveRootAppliedRevisionMatchesSource(t *testing.T) {
	for _, revision := range []string{"", "main@sha1:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"} {
		var list map[string]interface{}
		if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
			t.Fatal(err)
		}
		root := list["items"].([]interface{})[1].(map[string]interface{})
		delete(root["spec"].(map[string]interface{}), "suspend")
		root["status"].(map[string]interface{})["conditions"] = []interface{}{map[string]interface{}{"type": "Ready", "status": "True", "observedGeneration": 2}}
		root["status"].(map[string]interface{})["lastAppliedRevision"] = revision
		output, err := json.Marshal(list)
		if err != nil {
			t.Fatal(err)
		}
		responses := []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
		if revision == "main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" && check.Status != "healthy" || revision != "main@sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" && (check.Status != "degraded" || !strings.Contains(check.Summary, "applied revision differs from source") || strings.Contains(check.Summary, revision) && revision != "") {
			t.Fatalf("active root revision %q: %#v", revision, check)
		}
		generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
		if strings.Contains(generic.Summary, "applied revision differs from source") {
			t.Fatalf("generic inherited local revision check: %#v", generic)
		}
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("suspended root should not require current applied revision: %#v", check)
	}
}

func TestLocalClusterReadyResponseIsExact(t *testing.T) {
	for _, response := range []string{"ok", "ok\n"} {
		check := checkClusterForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: response}}}, localExperimentContext, true)
		if check.Status != "healthy" {
			t.Fatalf("valid readiness response rejected: %#v", check)
		}
	}
	for _, response := range []string{" ok", "ok ", "ok\r\n", "\nok", "ok\nextra"} {
		check := checkClusterForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: response}}}, localExperimentContext, true)
		if check.Status != "degraded" {
			t.Fatalf("padded readiness response accepted: %#v", check)
		}
	}
	if check := checkCluster(context.Background(), &fakeRunner{responses: []fakeResponse{{output: " ok "}}}, localExperimentContext); check.Status != "healthy" {
		t.Fatalf("generic doctor behavior changed: %#v", check)
	}
}

func TestLocalFluxDiagnosticDoesNotEchoInvalidResourceIdentity(t *testing.T) {
	for _, test := range []struct{ field, value string }{
		{"name", "token-secret\nunsafe"},
		{"namespace", "token-secret/unsafe"},
		{"kind", "token-secret/unsafe"},
		{"name", "a..b"},
		{"name", strings.Repeat("a", 64) + ".b"},
		{"namespace", "flux.system"},
	} {
		var list map[string]interface{}
		if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
			t.Fatal(err)
		}
		item := list["items"].([]interface{})[2].(map[string]interface{})
		if test.field == "kind" {
			item[test.field] = test.value
		} else {
			item["metadata"].(map[string]interface{})[test.field] = test.value
		}
		output, err := json.Marshal(list)
		if err != nil {
			t.Fatal(err)
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}}, localExperimentContext, true)
		expected := "identity unverified"
		if test.field != "kind" {
			expected = "resource response was incomplete"
		}
		if check.Status != "degraded" || !strings.Contains(check.Summary, expected) || strings.Contains(check.Summary, test.value) || strings.Contains(check.Summary, "token-secret") {
			t.Fatalf("invalid %s leaked or accepted: %#v", test.field, check)
		}
	}
}

func TestLocalLakehouseRejectsMalformedUnrelatedNames(t *testing.T) {
	for _, name := range []string{"extra..pod", strings.Repeat("a", 64) + ".pod", "secret\nfield"} {
		responses := localWorkloadResponses()
		responses[0].output = strings.TrimSuffix(responses[0].output, `]}`) + `,{"kind":"Deployment","metadata":{"name":` + fmt.Sprintf("%q", name) + `,"namespace":"shirokuma-storage"}}]}`
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		expected := "workload response was incomplete"
		if checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, expected) || strings.Contains(checks[0].Summary, name) || checks[1].Status != "healthy" {
			t.Fatalf("malformed unrelated workload name %q accepted or leaked: %#v", name, checks)
		}
	}
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: localWorkloadResponses()}, localExperimentContext)
	if checks[0].Status != "healthy" {
		t.Fatalf("valid workload inventory rejected: %#v", checks)
	}
}

func TestLocalFluxDevAppliedRevisionMatchesSource(t *testing.T) {
	for _, test := range []struct{ name, revision string }{
		{"missing", ""},
		{"older", "main@sha1:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
	} {
		t.Run(test.name, func(t *testing.T) {
			var list map[string]interface{}
			if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
				t.Fatal(err)
			}
			for _, item := range list["items"].([]interface{}) {
				object := item.(map[string]interface{})
				if object["kind"] == "Kustomization" && object["metadata"].(map[string]interface{})["name"] == "shirokuma-dev" {
					if test.revision == "" {
						delete(object["status"].(map[string]interface{}), "lastAppliedRevision")
					} else {
						object["status"].(map[string]interface{})["lastAppliedRevision"] = test.revision
					}
				}
			}
			output, err := json.Marshal(list)
			if err != nil {
				t.Fatal(err)
			}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}}, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, "applied revision differs from source") || strings.Contains(check.Summary, test.revision) && test.revision != "" {
				t.Fatalf("dev %s revision accepted: %#v", test.name, check)
			}
			generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}}, localExperimentContext, false)
			if strings.Contains(generic.Summary, "applied revision differs from source") {
				t.Fatalf("generic profile inherited local check: %#v", generic)
			}
		})
	}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("matching dev revision rejected: %#v", check)
	}
}

func TestLocalFluxDeclaredOriginAndCredentialsReference(t *testing.T) {
	for _, test := range []struct{ name, field, value, want string }{
		{"origin-absent", "url", "", "declared origin mismatch"},
		{"origin-other", "url", "ssh://git@github.com/other/repository", "declared origin mismatch"},
		{"credentials-absent", "secretRef", "", "declared credentials reference mismatch"},
		{"credentials-other", "secretRef", "other", "declared credentials reference mismatch"},
	} {
		t.Run(test.name, func(t *testing.T) {
			var list map[string]interface{}
			if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
				t.Fatal(err)
			}
			spec := list["items"].([]interface{})[0].(map[string]interface{})["spec"].(map[string]interface{})
			if test.value == "" {
				delete(spec, test.field)
			} else if test.field == "secretRef" {
				spec[test.field] = map[string]string{"name": test.value}
			} else {
				spec[test.field] = test.value
			}
			output, err := json.Marshal(list)
			if err != nil {
				t.Fatal(err)
			}
			responses := []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, test.want) || strings.Contains(check.Summary, test.value) && test.value != "" {
				t.Fatalf("%s: %#v", test.name, check)
			}
			generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
			if strings.Contains(generic.Summary, test.want) {
				t.Fatalf("generic profile changed: %#v", generic)
			}
		})
	}
}

func TestLocalFluxSourceMustBeActiveAtDeclaredBranch(t *testing.T) {
	for _, test := range []struct{ name, old, replacement, want string }{
		{"source-suspended", `"ref":{"branch":"main"}`, `"suspend":true,"ref":{"branch":"main"}`, "declared source suspended"},
		{"other-revision-ref", `"revision":"main@sha1:`, `"revision":"other@sha1:`, "revision unverified"},
	} {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.replacement, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture unchanged")
			}
			responses := []fakeResponse{{output: localControllerJSON()}, {output: resources}}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, test.want) {
				t.Fatalf("declared source drift accepted: %#v", check)
			}
		})
	}
}

func TestLocalFluxDeclaredBranchAndDevWait(t *testing.T) {
	for _, test := range []struct{ name, old, replacement, want string }{
		{"branch-absent", `"ref":{"branch":"main"}`, `"ref":{}`, "declared branch mismatch"},
		{"branch-other", `"ref":{"branch":"main"}`, `"ref":{"branch":"other"}`, "declared branch mismatch"},
		{"wait-absent", `"wait":true,`, ``, "wait disabled"},
		{"wait-false", `"wait":true,`, `"wait":false,`, "wait disabled"},
	} {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.replacement, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture unchanged")
			}
			responses := []fakeResponse{{output: localControllerJSON()}, {output: resources}}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, test.want) {
				t.Fatalf("declared field drift accepted: %#v", check)
			}
			generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
			if strings.Contains(generic.Summary, test.want) {
				t.Fatalf("generic profile inherited fixed declaration: %#v", generic)
			}
		})
	}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("declared configuration rejected: %#v", check)
	}
}

func TestLocalFluxDevPath(t *testing.T) {
	for _, path := range []string{"", "./deploy/gitops/other"} {
		resources := strings.Replace(localFluxJSON(), `"path":"./deploy/gitops/dev",`, `"path":"`+path+`",`, 1)
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "path mismatch") {
			t.Fatalf("dev path %q accepted: %#v", path, check)
		}
	}
	resources := strings.Replace(localFluxJSON(), `"path":"./deploy/gitops/dev",`, "", 1)
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "path mismatch") {
		t.Fatalf("missing dev path accepted: %#v", check)
	}
}

func TestLocalFluxRootPath(t *testing.T) {
	for _, path := range []string{"", "./deploy/gitops/other"} {
		resources := strings.Replace(localFluxJSON(), `"path":"./deploy/gitops/clusters/local-lite",`, `"path":"`+path+`",`, 1)
		if resources == localFluxJSON() {
			t.Fatal("root fixture has no path")
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
		if check.Status != "degraded" || !strings.Contains(check.Summary, "path mismatch") {
			t.Fatalf("root path %q accepted: %#v", path, check)
		}
	}
	resources := strings.Replace(localFluxJSON(), `"path":"./deploy/gitops/clusters/local-lite",`, "", 1)
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "path mismatch") {
		t.Fatalf("missing root path accepted: %#v", check)
	}
}

func TestLocalFluxDeclaredPrune(t *testing.T) {
	for _, name := range []string{"flux-system", "shirokuma-dev"} {
		for _, mode := range []string{"absent", "false"} {
			var list map[string]interface{}
			if err := json.Unmarshal([]byte(localFluxJSON()), &list); err != nil {
				t.Fatal(err)
			}
			for _, item := range list["items"].([]interface{}) {
				object := item.(map[string]interface{})
				if object["kind"] == "Kustomization" && object["metadata"].(map[string]interface{})["name"] == name {
					if mode == "absent" {
						delete(object["spec"].(map[string]interface{}), "prune")
					} else {
						object["spec"].(map[string]interface{})["prune"] = false
					}
				}
			}
			output, err := json.Marshal(list)
			if err != nil {
				t.Fatal(err)
			}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: string(output)}}}, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, "prune disabled") {
				t.Fatalf("%s prune %s accepted: %#v", name, mode, check)
			}
		}
	}
}

func TestLocalFluxRequiresDeclaredDevKustomizationAndSource(t *testing.T) {
	for _, test := range []struct{ name, old, replacement, want string }{
		{"missing-dev", `"name":"shirokuma-dev","namespace":"flux-system"`, `"name":"unrelated","namespace":"flux-system"`, "shirokuma-dev"},
		{"wrong-source", `"wait":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}`, `"wait":true,"sourceRef":{"kind":"GitRepository","name":"other"}`, "source reference mismatch"},
		{"wrong-source-namespace", `"wait":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}`, `"wait":true,"sourceRef":{"kind":"GitRepository","name":"flux-system","namespace":"other"}`, "source reference mismatch"},
	} {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.replacement, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture did not change")
			}
			responses := []fakeResponse{{output: localControllerJSON()}, {output: resources}}
			check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, test.want) {
				t.Fatalf("declared dev Kustomization gap accepted: %#v", check)
			}
			if test.name == "missing-dev" {
				generic := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false)
				if strings.Contains(generic.Summary, "shirokuma-dev (missing") {
					t.Fatalf("generic profile inherited fixed dev requirement: %#v", generic)
				}
			}
		})
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("declared dev Kustomization rejected: %#v", check)
	}
}

func TestLocalDoctorRejectsWrongListKind(t *testing.T) {
	for _, test := range []struct{ name, controllers, resources string }{
		{"controllers", `{"kind":"DeploymentList",` + localControllerJSON()[1:], localFluxJSON()},
		{"flux-resources", localControllerJSON(), `{"kind":"PodList",` + localFluxJSON()[1:]},
		{"valid-list", `{"kind":"List",` + localControllerJSON()[1:], `{"kind":"List",` + localFluxJSON()[1:]},
	} {
		runner := &fakeRunner{responses: []fakeResponse{{output: test.controllers}, {output: test.resources}}}
		check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
		if test.name == "valid-list" {
			if check.Status != "informational" {
				t.Fatalf("%s: %#v", test.name, check)
			}
		} else if check.Status != "degraded" || !strings.Contains(check.Summary, "incomplete") {
			t.Fatalf("%s: %#v", test.name, check)
		}
		if test.name == "controllers" && len(runner.calls) != 1 {
			t.Fatalf("unexpected source read after malformed controller list: %v", runner.calls)
		}
	}
	for _, kind := range []string{"PodList", "List"} {
		responses := localWorkloadResponses()
		responses[0].output = `{"kind":"` + kind + `",` + responses[0].output[1:]
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		want := "degraded"
		if kind == "List" {
			want = "healthy"
		}
		if checks[0].Status != want || checks[1].Status != "healthy" {
			t.Fatalf("workload list %s: %#v", kind, checks)
		}
	}
}

func TestLocalFluxRejectsCurrentReconciliationConditions(t *testing.T) {
	for _, condition := range []string{`{"type":"Stalled","status":"True","observedGeneration":1}`, `{"type":"Reconciling","status":"True","observedGeneration":1}`, `{"type":"Reconciling","status":"Unknown","observedGeneration":1}`} {
		resources := strings.Replace(localFluxJSON(), `"type":"Ready","status":"True","observedGeneration":1`, `"type":"Ready","status":"True","observedGeneration":1},`+condition[:len(condition)-1], 1)
		runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "unresolved reconciliation") {
			t.Fatalf("active condition %s accepted: %#v", condition, check)
		}
	}
	for _, condition := range []string{`{"type":"Stalled","status":"False","observedGeneration":1}`, `{"type":"Reconciling","status":"True","observedGeneration":0}`} {
		resources := strings.Replace(localFluxJSON(), `"type":"Ready","status":"True","observedGeneration":1`, `"type":"Ready","status":"True","observedGeneration":1},`+condition[:len(condition)-1], 1)
		runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "informational" {
			t.Fatalf("cleared or stale condition %s rejected: %#v", condition, check)
		}
	}
}

func TestLocalFluxSuspendedRootStillReportsCurrentReconciliationFailure(t *testing.T) {
	for _, test := range []struct {
		condition string
		status    string
	}{
		{`{"type":"Stalled","status":"True","observedGeneration":2}`, "degraded"},
		{`{"type":"Reconciling","status":"Unknown","observedGeneration":2}`, "degraded"},
		{`{"type":"Stalled","status":"True","observedGeneration":1}`, "informational"},
		{`{"type":"Reconciling","status":"False","observedGeneration":2}`, "informational"},
	} {
		resources := strings.Replace(localFluxJSON(), `"name":"flux-system","namespace":"flux-system","generation":2},"spec":{"suspend":true,"path":"./deploy/gitops/clusters/local-lite","prune":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}`, `"name":"flux-system","namespace":"flux-system","generation":2},"spec":{"suspend":true,"path":"./deploy/gitops/clusters/local-lite","prune":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1},`+test.condition+`]}`, 1)
		if resources == localFluxJSON() {
			t.Fatal("fixture did not change")
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
		if check.Status != test.status || (test.status == "degraded" && !strings.Contains(check.Summary, "unresolved reconciliation")) {
			t.Fatalf("suspended root condition %s: %#v", test.condition, check)
		}
	}
}

func TestLocalFluxSuspendedRootRejectsCurrentReadyFailure(t *testing.T) {
	for _, test := range []struct{ condition, status string }{
		{`{"type":"Ready","status":"False","observedGeneration":2}`, "degraded"},
		{`{"type":"Ready","status":"Unknown","observedGeneration":2}`, "degraded"},
		{`{"type":"Ready","status":"False","observedGeneration":1}`, "informational"},
		{`{"type":"Ready","status":"True","observedGeneration":2}`, "informational"},
	} {
		resources := strings.Replace(localFluxJSON(), `"name":"flux-system","namespace":"flux-system","generation":2},"spec":{"suspend":true,"path":"./deploy/gitops/clusters/local-lite","prune":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}`, `"name":"flux-system","namespace":"flux-system","generation":2},"spec":{"suspend":true,"path":"./deploy/gitops/clusters/local-lite","prune":true,"sourceRef":{"kind":"GitRepository","name":"flux-system"}},"status":{"conditions":[`+test.condition+`]}`, 1)
		if resources == localFluxJSON() {
			t.Fatal("fixture did not change")
		}
		check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}, localExperimentContext, true)
		if check.Status != test.status || (test.status == "degraded" && !strings.Contains(check.Summary, "conflicting Ready")) {
			t.Fatalf("suspended root condition %s: %#v", test.condition, check)
		}
	}
}

func TestLocalDoctorRejectsContradictoryUnavailableReplicas(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `"availableReplicas":1`, `"availableReplicas":1,"unavailableReplicas":1`, 1)
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "controller response was incomplete") {
		t.Fatalf("contradictory Flux controller accepted: %#v", check)
	}
	for _, value := range []string{"1", "-1"} {
		responses := localWorkloadResponses()
		responses[0].output = strings.Replace(responses[0].output, `"availableReplicas":1`, `"availableReplicas":1,"unavailableReplicas":`+value, 1)
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		summary := "workload response was incomplete"
		if checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, summary) || checks[1].Status != "healthy" {
			t.Fatalf("contradictory Lakehouse status %s accepted: %#v", value, checks)
		}
	}
	responses := localWorkloadResponses()
	responses[0].output = strings.Replace(responses[0].output, `"availableReplicas":1`, `"availableReplicas":1,"unavailableReplicas":0`, 1)
	if check := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)[0]; check.Status != "healthy" {
		t.Fatalf("explicit zero unavailable rejected: %#v", check)
	}
}

func TestLocalLakehouseRejectsDuplicateUnrelatedResource(t *testing.T) {
	other := healthyLocalWorkload("Deployment", "shirokuma-dev", "unrelated")
	for _, entries := range []string{other + `,` + other, healthyLocalWorkload("Deployment", "shirokuma-dev", "")} {
		responses := localWorkloadResponses()
		responses[1].output = strings.TrimSuffix(responses[1].output, `]}`) + `,` + entries + `]}`
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		if checks[0].Status != "healthy" {
			t.Fatalf("unaffected namespace rejected: %#v", checks[0])
		}
		for _, check := range checks[1:] {
			expected := "workload response was incomplete"
			if check.Status != "degraded" || !strings.Contains(check.Summary, expected) {
				t.Fatalf("ambiguous list accepted: %#v", check)
			}
		}
	}
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
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || (!strings.Contains(check.Summary, "duplicated") && !strings.Contains(check.Summary, "response was incomplete")) {
			t.Fatalf("duplicated root item %d passed: %#v", index, check)
		}
	}
}

func TestLocalFluxRejectsAmbiguousNonRootResources(t *testing.T) {
	for _, test := range []struct{ name, old, replacement, want string }{
		{"unexpected-kind", `"kind":"Kustomization","metadata":{"name":"shirokuma-catalog"`, `"kind":"Secret","metadata":{"name":"shirokuma-catalog"`, "unexpected kind"},
		{"zero-generation", `"name":"shirokuma-catalog","namespace":"flux-system","generation":1`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":0`, "response was incomplete"},
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
	if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || (!strings.Contains(check.Summary, "duplicated") && !strings.Contains(check.Summary, "response was incomplete")) {
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
		{"explicit-zero", `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"observedGeneration":0,`},
	} {
		t.Run(test.name, func(t *testing.T) {
			resources := strings.Replace(localFluxJSON(), test.old, test.replacement, 1)
			if resources == localFluxJSON() {
				t.Fatal("fixture replacement did not match")
			}
			runner := &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: resources}}}
			check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, "resource response was incomplete") {
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

func TestLocalLakehouseRejectsOutOfScopeInventoryItems(t *testing.T) {
	for _, test := range []struct{ name, old, replacement string }{
		{"unexpected-kind", `"kind":"Deployment","metadata":{"name":"polaris"`, `"kind":"Secret","metadata":{"name":"polaris"`},
		{"other-namespace", `"kind":"Deployment","metadata":{"name":"polaris","namespace":"shirokuma-dev"`, `"kind":"Deployment","metadata":{"name":"polaris","namespace":"elsewhere"`},
	} {
		t.Run(test.name, func(t *testing.T) {
			responses := localWorkloadResponses()
			index := 1
			responses[index].output = strings.Replace(responses[index].output, test.old, test.replacement, 1)
			if !strings.Contains(responses[index].output, test.replacement) {
				t.Fatal("fixture replacement did not match")
			}
			runner := &fakeRunner{responses: responses}
			checks := checkLocalLakehouse(context.Background(), runner, localExperimentContext)
			if checks[0].Status != "healthy" || checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "out-of-scope") {
				t.Fatalf("out-of-scope inventory accepted: %#v", checks)
			}
		})
	}
	responses := localWorkloadResponses()
	responses[1].output = strings.TrimSuffix(responses[1].output, `]}`) + `,{"kind":"Secret","metadata":{"name":"extra","namespace":"shirokuma-dev"}}]}`
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
	if checks[0].Status != "healthy" || checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "out-of-scope") {
		t.Fatalf("extra out-of-scope item accepted: %#v", checks)
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

func TestLocalFluxRejectsWrongControllerAPIVersion(t *testing.T) {
	for _, version := range []string{"v1", "batch/v1"} {
		controllers := strings.Replace(localControllerJSON(), `"apiVersion":"apps/v1"`, `"apiVersion":"`+version+`"`, 1)
		runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
			t.Fatalf("wrong controller version %q accepted: %#v", version, check)
		}
	}
	controllers := localControllerJSON()
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("apps/v1 controller rejected: %#v", check)
	}
}

func TestLocalFluxRejectsFailedAvailableCondition(t *testing.T) {
	for _, status := range []string{"False", "Unknown", ""} {
		controllers := strings.Replace(localControllerJSON(), `"type":"Available","status":"True"`, `"type":"Available","status":"`+status+`"`, 1)
		runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || (status != "" && !strings.Contains(check.Summary, "source-controller")) || (status == "" && !strings.Contains(check.Summary, "controller response was incomplete")) {
			t.Fatalf("controller condition %q accepted: %#v", status, check)
		}
	}
}

func TestLocalFluxRejectsUnresolvedReplicaFailure(t *testing.T) {
	for _, status := range []string{"True", "Unknown", ""} {
		controllers := strings.Replace(localControllerJSON(), `"type":"Available","status":"True"`, `"type":"Available","status":"True"},{"type":"ReplicaFailure","status":"`+status+`"`, 1)
		runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
		if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || (status != "" && !strings.Contains(check.Summary, "source-controller")) || (status == "" && !strings.Contains(check.Summary, "controller response was incomplete")) {
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
	controllers := strings.Replace(localControllerJSON(), `{"type":"Available","status":"True"},`, "", 1)
	runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
	if check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true); check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
		t.Fatalf("controller missing Available condition passed: %#v", check)
	}
	generic := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
	if check := checkFlux(context.Background(), generic, localExperimentContext); !strings.Contains(check.Summary, "suspended") {
		t.Fatalf("generic doctor behavior changed: %#v", check)
	}
}

func TestLocalFluxRequiresProgressingConditionOnControllers(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `,{"type":"Progressing","status":"True"}`, "", 1)
	responses := []fakeResponse{{output: controllers}, {output: localFluxJSON()}}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
		t.Fatalf("controller without Progressing=True accepted: %#v", check)
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false); strings.Contains(check.Summary, "source-controller") {
		t.Fatalf("generic profile inherited local condition check: %#v", check)
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true); check.Status != "informational" {
		t.Fatalf("healthy controllers rejected: %#v", check)
	}
}

func TestLocalFluxRejectsPausedController(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `"spec":{"replicas":1}`, `"spec":{"replicas":1,"paused":true}`, 1)
	responses := []fakeResponse{{output: controllers}, {output: localFluxJSON()}}
	check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true)
	if check.Status != "degraded" || !strings.Contains(check.Summary, "source-controller") {
		t.Fatalf("paused controller accepted: %#v", check)
	}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false); strings.Contains(check.Summary, "source-controller") {
		t.Fatalf("generic profile inherited fixed paused check: %#v", check)
	}
	check = checkFluxForProfile(context.Background(), &fakeRunner{responses: []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}}, localExperimentContext, true)
	if check.Status != "informational" {
		t.Fatalf("unpaused controllers rejected: %#v", check)
	}
}

func TestLocalFluxRejectsDuplicateControllerWithoutReadingSources(t *testing.T) {
	controllers := strings.Replace(localControllerJSON(), `"name":"notification-controller"`, `"name":"source-controller"`, 1)
	runner := &fakeRunner{responses: []fakeResponse{{output: controllers}, {output: localFluxJSON()}}}
	check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
	if check.Status != "degraded" || (!strings.Contains(check.Summary, "missing required") && !strings.Contains(check.Summary, "response was incomplete")) {
		t.Fatalf("duplicate controller passed: %#v", check)
	}
	// A missing fixed controller is already sufficient to reject the inventory.
	if len(runner.calls) != 1 {
		t.Fatalf("source read after incomplete controller inventory: %v", runner.calls)
	}
}

func TestLocalFluxRejectsContradictoryReadyAndTerminatingSuspendedRoot(t *testing.T) {
	for _, test := range []struct{ name, old, replacement, want string }{
		{"non-root-ready-conflict", `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1}]}`, `"name":"shirokuma-catalog","namespace":"flux-system","generation":1},"status":{"conditions":[{"type":"Ready","status":"True","observedGeneration":1},{"type":"Ready","status":"False","observedGeneration":1}]}`, "resource response was incomplete"},
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

func TestLocalFluxRejectsInconsistentListEnvelope(t *testing.T) {
	for _, field := range []string{`"resourceVersion":null`, `"remainingItemCount":null`, `"continue":null`} {
		for _, first := range []bool{true, false} {
			responses := []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}
			index := 1
			if first {
				index = 0
			}
			responses[index].output = strings.Replace(responses[index].output, `{"items":`, `{"metadata":{`+field+`},"items":`, 1)
			runner := &fakeRunner{responses: responses}
			check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, "incomplete") || first && len(runner.calls) != 1 {
				t.Fatalf("explicit null %s first=%v: %#v, calls=%v", field, first, check, runner.calls)
			}
		}
	}
	for _, field := range []string{`"metadata":null,`, `"metadata":{"remainingItemCount":1},`, `"metadata":{"remainingItemCount":-1},`, `"metadata":{"resourceVersion":""},`, `"metadata":{"resourceVersion":"  "},`, `"metadata":{"resourceVersion":"1 2"},`, `"metadata":{"resourceVersion":"rv\u001b[0m"},`, `"metadata":{"resourceVersion":"rv\u0001"},`, `"metadata":{"resourceVersion":"rv\u200b"},`, `"metadata":{"resourceVersion":"` + strings.Repeat("x", 1025) + `"},`, `"apiVersion":"v2",`, `"apiVersion":null,`, `"kind":null,`} {
		for _, first := range []bool{true, false} {
			responses := []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}
			index := 1
			if first {
				index = 0
			}
			responses[index].output = strings.Replace(responses[index].output, `{"items":`, `{`+field+`"items":`, 1)
			runner := &fakeRunner{responses: responses}
			check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, "incomplete") {
				t.Fatalf("%s first=%v: %#v", field, first, check)
			}
			if first && len(runner.calls) != 1 {
				t.Fatalf("source read after incomplete controllers: %v", runner.calls)
			}
		}
	}
	for _, field := range []string{`"metadata":{"remainingItemCount":0},`, `"metadata":{"resourceVersion":"1234"},`, `"metadata":{"resourceVersion":"opaque-v2"},`, `"apiVersion":"v1",`} {
		responses := []fakeResponse{{output: strings.Replace(localControllerJSON(), `{"items":`, `{`+field+`"items":`, 1)}, {output: strings.Replace(localFluxJSON(), `{"items":`, `{`+field+`"items":`, 1)}}
		if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, true); check.Status != "informational" {
			t.Fatalf("valid envelope %s rejected: %#v", field, check)
		}
	}
	responses := []fakeResponse{{output: localControllerJSON()}, {output: strings.Replace(localFluxJSON(), `{"items":`, `{"apiVersion":"v2","items":`, 1)}}
	if check := checkFluxForProfile(context.Background(), &fakeRunner{responses: responses}, localExperimentContext, false); strings.Contains(check.Summary, "incomplete") {
		t.Fatalf("generic profile inherited local envelope rule: %#v", check)
	}
}

func TestLocalDoctorRejectsDuplicateInventoryFields(t *testing.T) {
	for _, replacement := range []string{
		`{"items":[],"items":`,
		`{"metadata":{"continue":"private-token"},"metadata":{},"items":`,
	} {
		for _, first := range []bool{true, false} {
			responses := []fakeResponse{{output: localControllerJSON()}, {output: localFluxJSON()}}
			index := 1
			if first {
				index = 0
			}
			responses[index].output = strings.Replace(responses[index].output, `{"items":`, replacement, 1)
			runner := &fakeRunner{responses: responses}
			check := checkFluxForProfile(context.Background(), runner, localExperimentContext, true)
			if check.Status != "degraded" || !strings.Contains(check.Summary, "incomplete") || strings.Contains(check.Summary, "private-token") {
				t.Fatalf("duplicate field accepted: %#v", check)
			}
			if first && len(runner.calls) != 1 {
				t.Fatalf("read source after ambiguous controller list: %v", runner.calls)
			}
		}
	}
	for _, replacement := range []string{
		`"name":"seaweedfs","name":"seaweedfs"`,
		`"status":{},"status":{`,
	} {
		responses := localWorkloadResponses()
		if strings.Contains(replacement, `"name"`) {
			responses[0].output = strings.Replace(responses[0].output, `"name":"seaweedfs"`, replacement, 1)
		} else {
			responses[0].output = strings.Replace(responses[0].output, `"status":{`, replacement, 1)
		}
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		if checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, "incomplete") {
			t.Fatalf("nested duplicate accepted: %#v", checks)
		}
	}
	if !localInventoryUniqueFields([]byte(`{"a":[{"x":1},{"x":2}],"b":null}`)) || localInventoryUniqueFields([]byte(`{"a":1,"\u0061":2}`)) {
		t.Fatal("unique object scopes or escaped duplicate key not handled")
	}
}

func TestLocalWorkloadRejectsEmptyReportedListResourceVersion(t *testing.T) {
	for _, field := range []string{`"resourceVersion":null`, `"remainingItemCount":null`, `"continue":null`} {
		responses := localWorkloadResponses()
		responses[0].output = strings.Replace(responses[0].output, `{"items":`, `{"metadata":{`+field+`},"items":`, 1)
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		if checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, "incomplete") || checks[1].Status != "healthy" {
			t.Fatalf("explicit null %s: %#v", field, checks)
		}
	}
	for _, field := range []string{`"metadata":{"resourceVersion":""},`, `"metadata":{"resourceVersion":"rv\tother"},`, `"metadata":{"resourceVersion":"rv\u200b"},`, `"metadata":{"resourceVersion":"` + strings.Repeat("x", 1025) + `"},`, `"metadata":null,`, `"kind":null,`, `"apiVersion":null,`} {
		for _, index := range []int{0, 1} {
			responses := localWorkloadResponses()
			responses[index].output = strings.Replace(responses[index].output, `{"items":`, `{`+field+`"items":`, 1)
			checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
			for _, check := range checks {
				isAffected := index == 0 && check.Name == "seaweedfs" || index == 1 && check.Name != "seaweedfs"
				if isAffected && (check.Status != "degraded" || !strings.Contains(check.Summary, "incomplete")) || !isAffected && check.Status != "healthy" {
					t.Fatalf("list %d: unexpected workload check %#v", index, check)
				}
			}
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
	for _, endpoint := range []string{"https://cluster.example:6443", "https://127.0.0.1.example", "http://127.0.0.1:6443", "https://user:secret@127.0.0.1", "https://127.0.0.1", "https://127.0.0.1:0", "https://127.0.0.1:65536", "https://127.0.0.1:abc", "https://127.0.0.1:6443/proxy", "https://127.0.0.1:6443?redirect=remote", "https://127.0.0.1:6443?", "https://127.0.0.1:6443#fragment", "https://127.0.0.1:6443#", " https://127.0.0.1:6443", "https://127.0.0.1:6443\n", "invalid", "%"} {
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
		{"wrong-namespace", `"shirokuma-storage"`, `"unrelated"`, "out-of-scope"},
		{"stale-generation", `"observedGeneration":2`, `"observedGeneration":1`, "current generation"},
		{"missing-generation", `"observedGeneration":2,`, ``, "current generation"},
		{"scaled-down", `"spec":{"replicas":1}`, `"spec":{"replicas":0}`, "positive desired replica"},
		{"missing-desired", `"spec":{"replicas":1}`, `"spec":{}`, "positive desired replica"},
		{"missing-ready", `"readyReplicas":1`, `"readyReplicas":0`, "response was incomplete"},
		{"old-replicas", `"updatedReplicas":1`, `"updatedReplicas":0`, "rollout incomplete"},
		{"unavailable", `"availableReplicas":1`, `"availableReplicas":0`, "rollout incomplete"},
		{"extra-replica", `"status":{"observedGeneration":2,"replicas":1`, `"status":{"observedGeneration":2,"replicas":2`, "rollout incomplete"},
		{"terminating", `"generation":2`, `"generation":2,"deletionTimestamp":"2026-09-16T00:00:00Z"`, "terminating"},
		{"replica-failure", `"observedGeneration":2`, `"conditions":[{"type":"ReplicaFailure","status":"True"}],"observedGeneration":2`, "failed rollout"},
		{"replica-failure-unknown", `"observedGeneration":2`, `"conditions":[{"type":"ReplicaFailure","status":"Unknown"}],"observedGeneration":2`, "failed rollout"},
		{"replica-failure-empty", `"observedGeneration":2`, `"conditions":[{"type":"ReplicaFailure","status":""}],"observedGeneration":2`, "response was incomplete"},
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
	responses[1].output = strings.Replace(responses[1].output, `{"type":"Available","status":"True"},`, "", 1)
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
	if checks[0].Status != "healthy" || checks[1].Status != "healthy" || checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "Available=True") || checks[3].Status != "healthy" {
		t.Fatalf("missing deployment condition or StatefulSet condition handling: %#v", checks)
	}
}

func TestLocalLakehouseRequiresProgressingConditionOnlyOnDeployments(t *testing.T) {
	responses := localWorkloadResponses()
	responses[1].output = strings.Replace(responses[1].output, `,{"type":"Progressing","status":"True"}`, "", 1)
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
	if checks[0].Status != "healthy" || checks[1].Status != "healthy" || checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "Progressing=True") || checks[3].Status != "healthy" {
		t.Fatalf("missing Deployment progress or StatefulSet handling: %#v", checks)
	}
	for _, check := range checkLocalLakehouse(context.Background(), &fakeRunner{responses: localWorkloadResponses()}, localExperimentContext) {
		if check.Status != "healthy" {
			t.Fatalf("healthy workload rejected: %#v", check)
		}
	}
}

func TestLocalLakehouseRejectsPausedDeploymentOnly(t *testing.T) {
	responses := localWorkloadResponses()
	responses[1].output = strings.Replace(responses[1].output, healthyLocalWorkload("Deployment", "shirokuma-dev", "polaris"), strings.Replace(healthyLocalWorkload("Deployment", "shirokuma-dev", "polaris"), `"spec":{"replicas":1}`, `"spec":{"replicas":1,"paused":true}`, 1), 1)
	checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
	if checks[0].Status != "healthy" || checks[1].Status != "healthy" || checks[2].Status != "degraded" || !strings.Contains(checks[2].Summary, "paused") || checks[3].Status != "healthy" {
		t.Fatalf("paused deployment or unrelated workload misreported: %#v", checks)
	}
	for _, check := range checkLocalLakehouse(context.Background(), &fakeRunner{responses: localWorkloadResponses()}, localExperimentContext) {
		if check.Status != "healthy" {
			t.Fatalf("unpaused workload rejected: %#v", check)
		}
	}
}

func TestLocalLakehouseAcceptsCompleteV1ListEnvelope(t *testing.T) {
	responses := localWorkloadResponses()
	for i := range responses {
		responses[i].output = strings.Replace(responses[i].output, `{"items":`, `{"apiVersion":"v1","kind":"List","metadata":{"remainingItemCount":0},"items":`, 1)
	}
	for _, check := range checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext) {
		if check.Status != "healthy" {
			t.Fatalf("valid v1 inventory rejected: %#v", check)
		}
	}
}

func TestLocalLakehouseRejectsWrongItemAPIVersion(t *testing.T) {
	for _, version := range []string{"v1", "batch/v1"} {
		responses := localWorkloadResponses()
		responses[0].output = strings.Replace(responses[0].output, `"apiVersion":"apps/v1"`, `"apiVersion":"`+version+`"`, 1)
		checks := checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext)
		if checks[0].Status != "degraded" || !strings.Contains(checks[0].Summary, "out-of-scope") || checks[1].Status != "healthy" {
			t.Fatalf("wrong workload version %q accepted or unrelated namespace degraded: %#v", version, checks)
		}
	}
	responses := localWorkloadResponses()
	for _, check := range checkLocalLakehouse(context.Background(), &fakeRunner{responses: responses}, localExperimentContext) {
		if check.Status != "healthy" {
			t.Fatalf("apps/v1 workload rejected: %#v", check)
		}
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
		{"remaining-items", `{"metadata":{"remainingItemCount":1},"items":[]}`, "incomplete"},
		{"boolean-remaining-items", `{"metadata":{"remainingItemCount":false},"items":[]}`, "not valid JSON"},
		{"fractional-remaining-items", `{"metadata":{"remainingItemCount":0.5},"items":[]}`, "not valid JSON"},
		{"non-json-list-extension", `{"items":[],"extension":{"value":NaN}}`, "not valid JSON"},
		{"non-json-member-extension", `{"items":[{"metadata":{"name":"other"},"extension":Infinity}]}`, "not valid JSON"},
		{"overflow-list-extension", `{"items":[],"extension":{"value":1e999}}`, "incomplete"},
		{"overflow-member-extension", `{"items":[{"metadata":{"name":"other"},"extension":-1e999}]}`, "incomplete"},
		{"underflow-list-extension", `{"items":[],"extension":{"value":1e-999}}`, "incomplete"},
		{"underflow-member-extension", `{"items":[{"metadata":{"name":"other"},"extension":-1e-999}]}`, "incomplete"},
		{"wrong-api-version", `{"apiVersion":"v2","items":[]}`, "incomplete"},
		{"duplicate", `{"items":[` + healthyLocalWorkload("StatefulSet", "shirokuma-storage", "seaweedfs") + `,` + healthyLocalWorkload("StatefulSet", "shirokuma-storage", "seaweedfs") + `]}`, "incomplete"},
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

func TestLocalInventoryAllowsRepresentableExtensionNumbers(t *testing.T) {
	for _, raw := range []string{
		`{"items":[],"extension":{"value":0e-999}}`,
		`{"items":[],"extension":{"value":1.25e2}}`,
	} {
		if !localInventoryUniqueFields([]byte(raw)) {
			t.Fatalf("representable inventory extension refused: %s", raw)
		}
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
		{"failed-controller", strings.Replace(localControllerJSON(), `"type":"Available","status":"True"`, `"type":"Available","status":"True"},{"type":"ReplicaFailure","status":"True"`, 1), localFluxJSON()},
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
