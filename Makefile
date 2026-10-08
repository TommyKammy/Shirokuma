SHELL := /bin/bash
PYTHON ?= python3
PREFLIGHT_REF ?= origin/main
TOFU ?= tofu
TOFU_DIR ?= opentofu/dev
KUBE_CONTEXT ?= colima-mac-studio-solo
FLUX ?= flux
FLUX_VERSION ?= v2.9.2
KYVERNO ?= kyverno
KYVERNO_VERSION ?= v1.18.2
COSIGN ?= cosign
COSIGN_VERSION ?= v3.1.1
GITHUB_OWNER ?= TommyKammy
FLUX_GITHUB_REPOSITORY ?= Shirokuma
FLUX_GITHUB_PRIVATE ?= false
FLUX_BOOTSTRAP_BRANCH ?= flux/bootstrap-local-lite
FLUX_PATH ?= deploy/gitops/clusters/local-lite

.PHONY: prepare verify verify-cosign verify-security verify-policy verify-design-context verify-preflight-parser verify-supervisor-workflow-docs verify-colima-baseline verify-gitops-bootstrap verify-gitops-image-admission verify-gitops-teardown verify-kyverno-bootstrap verify-object-storage-profile test-polaris-build-contract verify-polaris-build-contract test-polaris-admin-build-inputs-contract verify-polaris-admin-build-inputs-contract test-polaris-admin-image-contract verify-polaris-admin-image-contract verify-polaris-runtime capture-polaris-runtime-acceptance verify-iceberg-table-bootstrap test-trino-dependency-publisher verify-trino-dependency-publisher test-trino-maven-feasibility test-trino-maven-security-feasibility verify-trino-bootstrap verify-dataops-bootstrap verify-superset-bootstrap verify-tpch-benchmark verify-metadata-bootstrap verify-ui-design-baseline verify-observability-baseline verify-repository-skeleton verify-go supervisor-preflight colima-start colima-status tofu-init tofu-fmt tofu-validate flux-version-check gitops-bootstrap gitops-status gitops-reconcile gitops-teardown check-newlines check-trailing-whitespace check-required-files check-no-secret-filenames

verify: check-required-files verify-design-context verify-preflight-parser verify-supervisor-workflow-docs verify-colima-baseline verify-gitops-bootstrap verify-gitops-teardown verify-kyverno-bootstrap verify-object-storage-profile verify-polaris-admin-image-contract verify-polaris-runtime verify-iceberg-table-bootstrap verify-trino-bootstrap verify-ui-design-baseline verify-observability-baseline verify-repository-skeleton verify-go verify-security verify-policy check-newlines check-trailing-whitespace check-no-secret-filenames

prepare: verify-design-context

verify-cosign:
	@command -v $(COSIGN) >/dev/null || { echo "cosign $(COSIGN_VERSION) is required for retained evidence verification"; exit 1; }
	@test "$$($(COSIGN) version | awk '/^GitVersion:/ {print $$2}')" = "$(COSIGN_VERSION)" || { echo "cosign $(COSIGN_VERSION) is required for retained evidence verification"; exit 1; }

verify-security: verify-cosign
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_supply_chain_security.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_trivyignore.py'
	@$(PYTHON) scripts/verify_trivyignore.py
	@$(PYTHON) scripts/verify_supply_chain.py scan-secrets --repo .
	@$(PYTHON) scripts/verify_supply_chain.py check-images --manifest security/resident-images.json --repo . --profile local-lab --exceptions security/resident-image-exceptions.json
	@$(PYTHON) scripts/verify_trusted_image.py audit --root .
	@$(PYTHON) scripts/verify_polaris_trusted_image.py audit --root .

verify-policy:
	@command -v $(KYVERNO) >/dev/null || { echo "kyverno $(KYVERNO_VERSION) is required for policy verification"; exit 1; }
	@test "$$($(KYVERNO) version | awk '/^Version:/ {print $$2}')" = "$(patsubst v%,%,$(KYVERNO_VERSION))" || { echo "kyverno $(KYVERNO_VERSION) is required for policy verification"; exit 1; }
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_policy_exceptions.py'
	@$(PYTHON) scripts/verify_policy_exceptions.py
	@$(KYVERNO) test tests/policy --require-tests
	@$(KYVERNO) apply policies/ --resource tests/policy/allowed.yaml.fixture

verify-design-context:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_arm64_compatibility_matrix.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_rustfs_desk_review.py'
	@$(PYTHON) scripts/verify_design_context.py

verify-preflight-parser:
	@$(PYTHON) -m unittest discover -s tests -p 'test_preflight_supervisor_issues.py'

verify-supervisor-workflow-docs:
	@$(PYTHON) -m unittest discover -s tests -p 'test_codex_supervisor_workflow_docs.py'

verify-colima-baseline:
	@$(PYTHON) -m unittest discover -s tests -p 'test_colima_baseline*.py'

verify-gitops-bootstrap: tofu-fmt tofu-validate
	@$(PYTHON) -m unittest discover -s tests -p 'test_gitops_bootstrap.py'

verify-gitops-teardown:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_gitops_teardown.py'
	@$(PYTHON) scripts/verify_gitops_teardown.py --root .

verify-kyverno-bootstrap:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_kyverno_bootstrap.py'

verify-object-storage-profile:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_object_storage_profile.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_object_storage_backup.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_package_go_vendor.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_trusted_image_contract.py'
	@$(PYTHON) scripts/verify_trusted_image.py audit --root .

test-polaris-build-contract:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_package_polaris_gradle_dependencies.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_polaris_trusted_image_contract.py'

verify-polaris-build-contract: test-polaris-build-contract verify-cosign
	@$(PYTHON) scripts/verify_polaris_trusted_image.py audit --root .

test-polaris-admin-build-inputs-contract:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_polaris_admin_build_inputs.py'

verify-polaris-admin-build-inputs-contract: test-polaris-admin-build-inputs-contract verify-cosign
	@$(PYTHON) scripts/verify_polaris_admin_build_inputs.py audit --root .

test-polaris-admin-image-contract: test-polaris-admin-build-inputs-contract
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_polaris_admin_image_contract.py'

verify-polaris-admin-image-contract: test-polaris-admin-image-contract verify-cosign
	@$(PYTHON) scripts/verify_polaris_admin_image.py audit --root .

verify-polaris-runtime: verify-polaris-build-contract verify-polaris-admin-image-contract
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_polaris_runtime.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_polaris_runtime_acceptance.py'
	@$(PYTHON) scripts/verify_polaris_runtime.py audit --root .

capture-polaris-runtime-acceptance:
	@test -n "$${SHIROKUMA_HOST_EXPORT_ROOT:-}" || { echo "SHIROKUMA_HOST_EXPORT_ROOT is required and must be a durable macOS directory outside Colima"; exit 1; }
	@$(PYTHON) scripts/polaris_runtime_acceptance.py \
		--backup-root "$${SHIROKUMA_HOST_EXPORT_ROOT}" \
		--output security/evidence/polaris-runtime-acceptance.json

verify-iceberg-table-bootstrap: flux-version-check
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_iceberg_table_bootstrap.py'

test-trino-dependency-publisher:
	@$(PYTHON) -m unittest -v tests.test_trino_dependency_publisher
verify-trino-dependency-publisher: test-trino-dependency-publisher
	@$(PYTHON) scripts/verify_trino_dependency_publisher.py audit --root .

test-trino-maven-feasibility:
	@$(PYTHON) -m unittest -v tests.test_trino_maven_feasibility

test-trino-maven-security-feasibility:
	@$(PYTHON) -m unittest -v tests.test_trino_maven_security_feasibility

verify-trino-bootstrap: verify-trino-dependency-publisher test-trino-maven-feasibility test-trino-maven-security-feasibility
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_trino_bootstrap.py'

verify-dataops-bootstrap:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_dataops_bootstrap.py'

verify-superset-bootstrap:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_superset_bootstrap.py'

verify-tpch-benchmark:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_tpch_benchmark.py'

verify-metadata-bootstrap:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_metadata_bootstrap.py'

verify-gitops-image-admission: verify-security
	@$(PYTHON) scripts/verify_gitops_image_admission.py

tofu-init:
	@$(TOFU) -chdir=$(TOFU_DIR) init -backend=false -input=false -lockfile=readonly

tofu-fmt:
	@$(TOFU) fmt -check -recursive

tofu-validate: tofu-init
	@$(TOFU) -chdir=$(TOFU_DIR) validate

flux-version-check:
	@command -v $(FLUX) >/dev/null || { echo "flux $(FLUX_VERSION) is required"; exit 1; }
	@test "$(FLUX_VERSION)" = v2.9.2 || { echo "pinned flux v2.9.2 is required"; exit 1; }
	@version="$$( $(FLUX) version --client 2>&1 && printf .)" && test "$$version" = $$'flux: v2.9.2\n.' || { echo "pinned flux v2.9.2 is required without client warnings"; exit 1; }

# Historical GitHub bootstrap is retained for external-delivery review, but local
# mode must not execute even its preflight dependencies or publish to GitHub.
gitops-bootstrap:
	@echo "gitops-bootstrap is disabled in local mode: GitHub write requires separate external-delivery authorization"; exit 1
	@test -n "$${GITHUB_TOKEN:-}" || { echo "GITHUB_TOKEN is required for Flux bootstrap and is never persisted by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_operator_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_access_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_operator_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_secret_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_access_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_secret_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_polaris_postgresql_password:-}" || { echo "TF_VAR_polaris_postgresql_password is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_polaris_root_client_secret:-}" || { echo "TF_VAR_polaris_root_client_secret is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@legacy_pvc="$$(kubectl --context $(KUBE_CONTEXT) -n shirokuma-dev get pvc seaweedfs-data-seaweedfs-0 --ignore-not-found -o name)" || { echo "legacy PVC lookup failed; refusing OpenTofu apply"; exit 1; }; test -z "$$legacy_pvc" || { echo "legacy shirokuma-dev/seaweedfs-data-seaweedfs-0 PVC exists; complete a verified export and the whole-profile nuke/rebuild procedure before bootstrap"; exit 1; }
	@$(TOFU) -chdir=$(TOFU_DIR) apply -input=false -auto-approve
	@$(FLUX) bootstrap github --owner=$(GITHUB_OWNER) --repository=$(FLUX_GITHUB_REPOSITORY) --private=$(FLUX_GITHUB_PRIVATE) --branch=$(FLUX_BOOTSTRAP_BRANCH) --path=$(FLUX_PATH) --personal --components=source-controller,kustomize-controller,helm-controller,notification-controller --version=$(FLUX_VERSION) --context=$(KUBE_CONTEXT)

gitops-status:
	@test "$(KUBE_CONTEXT)" = colima-mac-studio-solo || { echo "gitops-status requires the dedicated Kubernetes context"; exit 1; }
	@kubectl --context $(KUBE_CONTEXT) -n flux-system get deployments
	@$(FLUX) get sources git -A --context=$(KUBE_CONTEXT)
	@$(FLUX) get kustomizations -A --context=$(KUBE_CONTEXT)

gitops-reconcile: flux-version-check
	@test "$(KUBE_CONTEXT)" = colima-mac-studio-solo || { echo "gitops-reconcile requires the dedicated Kubernetes context"; exit 1; }
	@$(MAKE) --no-print-directory colima-status
	@$(FLUX) reconcile source git flux-system -n flux-system --context=$(KUBE_CONTEXT)
	@$(FLUX) reconcile kustomization flux-system -n flux-system --with-source --context=$(KUBE_CONTEXT)
	@$(FLUX) reconcile kustomization shirokuma-dev -n flux-system --context=$(KUBE_CONTEXT)
	@object_storage="$$(kubectl --context $(KUBE_CONTEXT) -n flux-system get kustomization.kustomize.toolkit.fluxcd.io shirokuma-object-storage --ignore-not-found -o name)" || { echo "shirokuma-object-storage Kustomization lookup failed"; exit 1; }; if test -n "$$object_storage"; then $(FLUX) reconcile kustomization shirokuma-object-storage -n flux-system --context=$(KUBE_CONTEXT); else echo "shirokuma-object-storage Kustomization is absent; skipping reconcile"; fi
	@$(FLUX) reconcile kustomization shirokuma-catalog-database -n flux-system --context=$(KUBE_CONTEXT)
	@$(FLUX) reconcile kustomization shirokuma-catalog-bootstrap -n flux-system --context=$(KUBE_CONTEXT)
	@$(FLUX) reconcile kustomization shirokuma-catalog -n flux-system --context=$(KUBE_CONTEXT)

# Do not run init or uninstall/destroy against retained lab state in local mode.
gitops-teardown:
	@echo "gitops-teardown is disabled in local mode: retained data and backup safety require a separate reviewed plan"; exit 1
	@test -n "$${TF_VAR_seaweedfs_s3_operator_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_access_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_operator_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_secret_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_access_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_secret_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_polaris_postgresql_password:-}" || { echo "TF_VAR_polaris_postgresql_password is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_polaris_root_client_secret:-}" || { echo "TF_VAR_polaris_root_client_secret is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@$(TOFU) -chdir=$(TOFU_DIR) plan -destroy -refresh=false -input=false >/dev/null
	@$(FLUX) uninstall --context=$(KUBE_CONTEXT) --namespace=flux-system --silent
	@$(TOFU) -chdir=$(TOFU_DIR) destroy -input=false -auto-approve

colima-start:
	@./scripts/colima_baseline.sh start

colima-status:
	@./scripts/colima_baseline.sh status

verify-ui-design-baseline:
	@$(PYTHON) -m unittest discover -s tests -p 'test_ui_design_baseline.py'

verify-observability-baseline:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_observability_baseline.py'

verify-repository-skeleton:
	@$(PYTHON) scripts/verify_repository_skeleton.py

verify-go:
	@command -v go >/dev/null || { echo "go is required for repository verification"; exit 1; }
	@command -v gofmt >/dev/null || { echo "gofmt is required for repository verification"; exit 1; }
	@unformatted="$$(find . -type f -name '*.go' -not -path './.git/*' -exec gofmt -l {} +)"; test -z "$$unformatted" || { echo "gofmt required for:"; echo "$$unformatted"; exit 1; }
	@go test ./...
	@go vet ./...
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; go build -o "$$tmp/shirokuma" ./cmd/shirokuma
	@go run ./cmd/shirokuma --help >/dev/null
	@test "$$(go run ./cmd/shirokuma version)" = "shirokuma dev"
	@test "$$(go run ./cmd/shirokuma --version)" = "shirokuma version dev"

supervisor-preflight:
	@$(PYTHON) scripts/preflight_supervisor_issues.py --ref "$(PREFLIGHT_REF)"

check-required-files:
	@test -f README.md
	@test -f LICENSE
	@test -f .gitignore
	@test -f .github/CODEOWNERS
	@test -f .github/ISSUE_TEMPLATE/config.yml
	@test -f .github/ISSUE_TEMPLATE/work_package.yml
	@test -f .github/ISSUE_TEMPLATE/bug_report.yml
	@test -f .github/pull_request_template.md
	@test -f .github/workflows/ci.yml
	@test -f .github/workflows/security.yml
	@test -f AGENTS.md
	@test -f CONTRIBUTING.md
	@test -f docs/GOVERNANCE.md
	@test -f docs/design/context-manifest.json
	@test -f docs/design/issue-context.json
	@test -f scripts/verify_design_context.py
	@test -f scripts/preflight_supervisor_issues.py
	@test -x scripts/colima_baseline.sh
	@test -f scripts/verify_supply_chain.py
	@test -f scripts/verify_trivyignore.py
	@test -f .trivyignore.yaml
	@test -f scripts/verify_policy_exceptions.py
	@test -f scripts/verify_gitops_teardown.py
	@test -f scripts/verify_polaris_admin_build_inputs.py
	@test -x scripts/polaris_runtime_acceptance.py
	@test -f tests/test_gitops_teardown.py
	@test -f tests/test_polaris_admin_build_inputs.py
	@test -f tests/test_polaris_runtime_acceptance.py
	@test -f bootstrap/polaris/v1.6.0/admin-build-inputs-contract.json
	@test -f bootstrap/polaris/v1.6.0/admin-admission.json
	@test -f bootstrap/polaris/v1.6.0/admin-image-contract.json
	@test -f bootstrap/polaris/v1.6.0/admin-release-evidence.json
	@test -f bootstrap/polaris/v1.6.0/admin-image-evidence/evidence.sha256
	@test -f security/evidence/polaris-admin-v1.6.0/evidence.sha256
	@test -f security/resident-images.json
	@test -f security/resident-image-exceptions.json
	@test -f policies/kyverno/baseline.yaml
	@test -f tests/policy/kyverno-test.yaml

check-newlines:
	@missing=0; \
	while IFS= read -r file; do \
		[ -f "$$file" ] || continue; \
		case "$$file" in \
			*.png|*.jpg|*.jpeg|*.gif|*.ico|*.pdf|*.zip|*.gz|*.tgz|*.xz|*.jar|*.war) continue ;; \
			bootstrap/seaweedfs/v4.39/evidence/cosign-signature-bundle.json|bootstrap/seaweedfs/v4.39/evidence/image-manifest.json|bootstrap/seaweedfs/v4.39/evidence/sbom-attestation-bundle.json|bootstrap/seaweedfs/v4.39/evidence/trivy-attestation-bundle.json) continue ;; \
			bootstrap/polaris/v1.6.0/evidence/cosign-signature-bundle.json|bootstrap/polaris/v1.6.0/evidence/oci-manifest.json) continue ;; \
			bootstrap/polaris/v1.6.0/admin-build-inputs-evidence/cosign-signature-bundle.json|bootstrap/polaris/v1.6.0/admin-build-inputs-evidence/oci-manifest.json) continue ;; \
			bootstrap/polaris/v1.6.0/admin-image-evidence/anonymous-image-manifest.json|bootstrap/polaris/v1.6.0/admin-image-evidence/cosign-signature-bundle.json|bootstrap/polaris/v1.6.0/admin-image-evidence/image-config.json|bootstrap/polaris/v1.6.0/admin-image-evidence/image-manifest.json|bootstrap/polaris/v1.6.0/admin-image-evidence/runtime-base-index.json|bootstrap/polaris/v1.6.0/admin-image-evidence/runtime-base-manifest.json|bootstrap/polaris/v1.6.0/admin-image-evidence/sbom-attestation-bundle.json|bootstrap/polaris/v1.6.0/admin-image-evidence/trivy-attestation-bundle.json|bootstrap/polaris/v1.6.0/admin-image-evidence/trusted-tag-manifest.json) continue ;; \
			bootstrap/polaris/v1.6.0/image-evidence/anonymous-image-manifest.json|bootstrap/polaris/v1.6.0/image-evidence/cosign-signature-bundle.json|bootstrap/polaris/v1.6.0/image-evidence/health-ready.json|bootstrap/polaris/v1.6.0/image-evidence/image-config.json|bootstrap/polaris/v1.6.0/image-evidence/image-manifest.json|bootstrap/polaris/v1.6.0/image-evidence/runtime-base-manifest.json|bootstrap/polaris/v1.6.0/image-evidence/sbom-attestation-bundle.json|bootstrap/polaris/v1.6.0/image-evidence/trivy-attestation-bundle.json|bootstrap/polaris/v1.6.0/image-evidence/trusted-tag-manifest.json) continue ;; \
			bootstrap/postgresql/v18.4/evidence/index-manifest.json|bootstrap/postgresql/v18.4/evidence/arm64-manifest.json|bootstrap/postgresql/v18.4/evidence/attestation-manifest.json|bootstrap/postgresql/v18.4/evidence/index-signature-payload.json|bootstrap/postgresql/v18.4/evidence/arm64-signature-payload.json|bootstrap/postgresql/v18.4/evidence/slsa-attestation-envelope.json|bootstrap/postgresql/v18.4/evidence/spdx-attestation-envelope.json) continue ;; \
		esac; \
		if [ -s "$$file" ] && [ "$$(tail -c 1 "$$file" | wc -l | tr -d ' ')" = "0" ]; then \
			echo "missing final newline: $$file"; \
			missing=1; \
		fi; \
	done < <(git ls-files); \
	exit $$missing

check-trailing-whitespace:
	@if git grep -I -nE '[[:blank:]]$$' -- ':!LICENSE' ':!*.png' ':!*.jpg' ':!*.jpeg' ':!*.gif' ':!*.ico' ':!*.pdf'; then \
		echo "trailing whitespace found"; \
		exit 1; \
	fi

check-no-secret-filenames:
	@if git ls-files | grep -Ei '(^|/)(\.env|.*\.(pem|key|p12|pfx|token|secret))$$'; then \
		echo "secret-like filename is tracked"; \
		exit 1; \
	fi
# Host-local experiments use their own fixed context; KUBE_CONTEXT is not used.
.PHONY: local-experiment-status local-experiment-prepare local-experiment-apply local-experiment-resume test-local-experiment

local-experiment-status:
	@$(PYTHON) tools/local_experiment/local_experiment.py status

local-experiment-prepare:
	@$(PYTHON) tools/local_experiment/local_experiment.py prepare "$(COMPONENT)"

local-experiment-apply:
	@test -n "$(RUN)" || { echo "RUN must name a prepared local experiment directory"; exit 1; }
	@$(PYTHON) tools/local_experiment/local_experiment.py apply "$(RUN)"

local-experiment-resume:
	@test -n "$(RUN)" || { echo "RUN must name a prepared local experiment directory"; exit 1; }
	@$(PYTHON) tools/local_experiment/local_experiment.py resume "$(RUN)"

test-local-experiment:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_experiment.py'

TRINO_LOCAL_URL ?= http://127.0.0.1:18080
.PHONY: local-trino-build local-trino-render local-trino-smoke local-trino-smoke-write test-local-trino-smoke verify-local-experiment

local-trino-build:
	@test -f "$(TRINO_ARCHIVE)" || { echo "TRINO_ARCHIVE must name the pinned Trino 483 archive"; exit 1; }
	@./bootstrap/trino/local-experiment/build-local.sh "$(TRINO_ARCHIVE)"

local-trino-render:
	@$(PYTHON) bootstrap/trino/local-experiment/render-local.py "$(TRINO_IMAGE_DIGEST)"

local-trino-smoke:
	@$(PYTHON) tools/local_experiment/local_trino_smoke.py --url "$(TRINO_LOCAL_URL)"

local-trino-smoke-write:
	@$(PYTHON) tools/local_experiment/local_trino_smoke.py --url "$(TRINO_LOCAL_URL)" --write-test

test-local-trino-smoke:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_trino_smoke.py'

.PHONY: test-local-polaris-permissions
test-local-polaris-permissions:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_experiment_polaris_permissions.py'

.PHONY: local-lakehouse-smoke local-dataops-sync local-dataops-smoke local-dataops-recovery-smoke test-local-session test-local-dataops local-doctor

local-lakehouse-smoke:
	@$(PYTHON) tools/local_experiment/local_session.py smoke --write-test

local-dataops-sync:
	@uv sync --locked --no-dev --project tools/local_experiment/dataops

local-dataops-smoke: local-dataops-sync
	@$(PYTHON) tools/local_experiment/local_session.py dataops

local-dataops-recovery-smoke: local-dataops-sync
	@$(PYTHON) tools/local_experiment/local_session.py dataops --exercise-failure

test-local-session:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_session.py'

test-local-dataops:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_dataops.py'

local-doctor:
	@go run ./cmd/shirokuma doctor --profile local-experiment

verify-local-experiment: test-local-experiment test-local-trino-smoke test-local-polaris-permissions test-local-session test-local-dataops

.PHONY: local-tpch-smoke local-superset-start local-superset-smoke local-metadata-start local-metadata-ingest local-agenthouse-start local-agenthouse-smoke local-loki-start test-local-platform

local-tpch-smoke:
	@$(PYTHON) tools/local_experiment/local_session.py benchmark

local-superset-start:
	@$(PYTHON) tools/local_experiment/superset/local_superset.py start

local-superset-smoke:
	@$(PYTHON) tools/local_experiment/superset/local_superset.py smoke

local-metadata-start:
	@$(PYTHON) tools/local_experiment/metadata/runtime.py up

local-metadata-ingest:
	@$(PYTHON) tools/local_experiment/metadata/ingest.py

local-agenthouse-start:
	@$(PYTHON) tools/local_experiment/agenthouse/manage.py up

local-agenthouse-smoke:
	@$(PYTHON) tools/local_experiment/agenthouse/manage.py smoke

local-loki-start:
	@$(PYTHON) tools/local_experiment/loki/manage.py up

test-local-platform:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_platform_status.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_superset.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_metadata.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_tpch.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_agenthouse.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_loki.py'

verify-local-experiment: test-local-platform

.PHONY: local-gateway-sync local-gateway-smoke test-local-gateway local-platform-status
local-platform-status:
	@$(PYTHON) tools/local_experiment/platform_status.py

local-gateway-sync:
	@uv sync --locked --no-dev --project tools/local_experiment/gateway

local-gateway-smoke: local-gateway-sync
	@tools/local_experiment/gateway/.venv/bin/python tools/local_experiment/gateway/smoke.py

test-local-gateway:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_gateway.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_gateway_container_status.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_gateway_pod_usage.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_gateway_pod_logs.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_gateway_pod_read.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_gateway_pod_dispatch.py'

verify-local-experiment: test-local-gateway

.PHONY: local-llm-sync local-llm-smoke local-llm-qwen-smoke local-llm-eval local-llm-qwen-eval test-local-llm
local-llm-sync:
	@uv sync --locked --no-dev --project tools/local_experiment/llm

local-llm-smoke: local-llm-sync local-gateway-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/llm/workflow.py

local-llm-qwen-smoke: local-llm-sync local-gateway-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/llm/workflow.py --config tools/local_experiment/llm/providers.qwen-local.json --probe-vm

local-llm-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/llm/evaluation.py

local-llm-qwen-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/llm/evaluation.py --config tools/local_experiment/llm/providers.qwen-local.json

test-local-llm: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_llm*.py'

verify-local-experiment: test-local-llm

.PHONY: local-sre-smoke local-sre-qwen-smoke local-sre-qwen-eval local-sre-qwen-drill test-local-bears test-local-sre-llm
local-sre-smoke: local-gateway-sync
	@tools/local_experiment/gateway/.venv/bin/python tools/local_experiment/bears/run_sre.py

local-sre-qwen-smoke: local-gateway-sync local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_sre_llm.py

local-sre-qwen-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/evaluate_sre.py

local-sre-qwen-drill: local-gateway-sync local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/drill_sre.py

test-local-sre-llm: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_sre*.py'

test-local-bears:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_bears.py'

verify-local-experiment: test-local-bears test-local-sre-llm

.PHONY: local-policy-status local-policy-apply local-policy-smoke local-policy-withdraw test-local-policy
local-policy-status:
	@$(PYTHON) tools/local_experiment/policy/manage.py status

local-policy-apply:
	@$(PYTHON) tools/local_experiment/policy/manage.py apply

local-policy-smoke:
	@$(PYTHON) tools/local_experiment/policy/manage.py smoke

local-policy-withdraw:
	@$(PYTHON) tools/local_experiment/policy/manage.py rollback-bindings

test-local-policy:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_policy.py'

verify-local-experiment: test-local-policy

.PHONY: local-data-bear-smoke local-data-bear-eval local-data-bear-request local-data-bear-request-eval test-local-data-bear
local-data-bear-smoke: local-gateway-sync local-llm-sync local-dataops-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_data.py

local-data-bear-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/evaluate_data.py

local-data-bear-request: local-gateway-sync local-llm-sync local-dataops-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_data.py --request-stdin

local-data-bear-request-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/evaluate_data.py --suite requests

test-local-data-bear: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_data_*.py'

verify-local-experiment: test-local-data-bear

.PHONY: local-catalog-bear-smoke local-catalog-bear-eval test-local-catalog-bear
local-catalog-bear-smoke: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_catalog.py --data-plan-token "$(CATALOG_DATA_PLAN_TOKEN)"

local-catalog-bear-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/evaluate_catalog.py

test-local-catalog-bear: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_catalog_*.py'

verify-local-experiment: test-local-catalog-bear

.PHONY: local-data-catalog-smoke local-data-catalog-request local-data-catalog-resume test-local-data-catalog
local-data-catalog-smoke: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_data_catalog.py run

local-data-catalog-request: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_data_catalog.py run --request-stdin

local-data-catalog-resume: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_data_catalog.py resume --handoff-token "$(DATA_CATALOG_HANDOFF_TOKEN)"

test-local-data-catalog: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_data_catalog.py'

# test-local-data-bear already discovers test_local_data_catalog.py.

.PHONY: test-data-catalog-cli
test-data-catalog-cli: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_data_catalog_cli.py'

verify-local-experiment: test-data-catalog-cli

.PHONY: local-lineage-coverage test-local-lineage-coverage
local-lineage-coverage:
	@$(PYTHON) tools/local_experiment/metadata/coverage.py

test-local-lineage-coverage:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_lineage_coverage*.py'

verify-local-experiment: test-local-lineage-coverage

.PHONY: local-query-bear-smoke local-query-bear-eval test-local-query-bear local-evaluation-report test-local-evaluation-report
local-query-bear-smoke: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_query.py

local-query-bear-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/evaluate_query.py

test-local-query-bear: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_query_bear.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_query_observe.py'

local-evaluation-report:
	@$(PYTHON) tools/local_experiment/evaluation/report.py

test-local-evaluation-report:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_evaluation_report.py'

verify-local-experiment: test-local-query-bear test-local-evaluation-report

.PHONY: local-policy-bear-smoke local-policy-bear-eval test-local-policy-bear
local-policy-bear-smoke: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/run_policy.py

local-policy-bear-eval: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/bears/evaluate_policy.py

test-local-policy-bear: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_policy_bear.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_gatekeeper_observe.py'

verify-local-experiment: test-local-policy-bear

.PHONY: local-iceberg-sandbox test-local-iceberg-sandbox
local-iceberg-sandbox:
	@$(PYTHON) tools/local_experiment/local_session.py iceberg-sandbox

test-local-iceberg-sandbox:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_iceberg_sandbox.py'

verify-local-experiment: test-local-iceberg-sandbox

.PHONY: local-iceberg-refs test-local-iceberg-refs
local-iceberg-refs:
	@$(PYTHON) tools/local_experiment/iceberg_refs/run.py

test-local-iceberg-refs:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_iceberg_refs*.py'

verify-local-experiment: test-local-iceberg-refs

.PHONY: local-grafana-prepare local-grafana-up local-grafana-status local-grafana-smoke local-grafana-stop local-grafana-verify-dashboard test-local-grafana
local-grafana-prepare:
	@$(PYTHON) tools/local_experiment/grafana/manage.py prepare

local-grafana-up:
	@$(PYTHON) tools/local_experiment/grafana/manage.py up

local-grafana-status:
	@$(PYTHON) tools/local_experiment/grafana/manage.py status

local-grafana-smoke:
	@$(PYTHON) tools/local_experiment/grafana/manage.py smoke

local-grafana-verify-dashboard:
	@$(PYTHON) tools/local_experiment/grafana/verify_dashboard.py

local-grafana-stop:
	@$(PYTHON) tools/local_experiment/grafana/manage.py stop

test-local-grafana:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_grafana_runtime.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_agenthouse_dashboard.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_grafana_dashboard_verifier.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_grafana_history_*.py'

.PHONY: local-grafana-history-enable local-grafana-verify-history
local-grafana-history-enable:
	@$(PYTHON) tools/local_experiment/grafana/manage.py history-enable
	@$(PYTHON) tools/local_experiment/grafana/manage.py history-publish

local-grafana-verify-history:
	@$(PYTHON) tools/local_experiment/grafana/verify_history_dashboard.py

verify-local-experiment: test-local-grafana

.PHONY: local-benchmark-events-plan local-benchmark-events-import test-local-benchmark-events
local-benchmark-events-plan:
	@$(PYTHON) tools/local_experiment/benchmark_events/import_receipts.py plan

local-benchmark-events-import:
	@$(PYTHON) tools/local_experiment/benchmark_events/import_receipts.py apply

test-local-benchmark-events:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_benchmark_events.py'

verify-local-experiment: test-local-benchmark-events

.PHONY: local-query-history-plan local-query-history-import test-local-query-history
local-query-history-plan:
	@$(PYTHON) tools/local_experiment/query_history/import_receipts.py plan
local-query-history-import:
	@$(PYTHON) tools/local_experiment/query_history/import_receipts.py apply
test-local-query-history:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_query_history.py'
verify-local-experiment: test-local-query-history

.PHONY: local-trino-opa-build local-trino-opa-plan local-trino-opa-run local-trino-opa-recover test-local-trino-opa
local-trino-opa-build:
	@$(PYTHON) tools/local_experiment/trino_opa/build.py --trino-archive "$(TRINO_ARCHIVE)"
local-trino-opa-plan:
	@$(PYTHON) tools/local_experiment/trino_opa/run.py plan --build-token "$(TRINO_OPA_BUILD_TOKEN)"
local-trino-opa-run:
	@$(PYTHON) tools/local_experiment/trino_opa/run.py run --build-token "$(TRINO_OPA_BUILD_TOKEN)"
local-trino-opa-recover:
	@$(PYTHON) tools/local_experiment/trino_opa/run.py recover --run-token "$(TRINO_OPA_RUN_TOKEN)"
test-local-trino-opa:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_trino_opa.py'
verify-local-experiment: test-local-trino-opa

.PHONY: local-trino-identity-build local-trino-identity-plan local-trino-identity-run local-trino-identity-recover test-local-trino-identity
local-trino-identity-build:
	@$(PYTHON) tools/local_experiment/trino_identity/build.py
local-trino-identity-plan:
	@$(PYTHON) tools/local_experiment/trino_identity/run.py plan --build-token "$(TRINO_IDENTITY_BUILD_TOKEN)"
local-trino-identity-run:
	@$(PYTHON) tools/local_experiment/trino_identity/run.py run --build-token "$(TRINO_IDENTITY_BUILD_TOKEN)"
local-trino-identity-recover:
	@$(PYTHON) tools/local_experiment/trino_identity/run.py recover --run-token "$(TRINO_IDENTITY_RUN_TOKEN)"
test-local-trino-identity:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_trino_identity*.py'
verify-local-experiment: test-local-trino-identity

.PHONY: local-trino-identity-telemetry test-local-identity-telemetry
local-trino-identity-telemetry:
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/trino_identity/run.py run --build-token "$(TRINO_IDENTITY_BUILD_TOKEN)" --telemetry
test-local-identity-telemetry:
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_identity_telemetry.py'
	@PYTHONPATH=tests tools/local_experiment/llm/.venv/bin/python -m unittest -v test_local_trino_identity.IdentityRunnerTests.test_connected_run_exports_real_local_spans_with_fake_services
verify-local-experiment: test-local-identity-telemetry

.PHONY: local-gateway-warehouse-build local-gateway-warehouse-plan local-gateway-warehouse-run local-gateway-warehouse-recover test-local-gateway-warehouse
local-gateway-warehouse-build:
	@$(PYTHON) tools/local_experiment/gateway_warehouse/build.py
local-gateway-warehouse-plan:
	@$(PYTHON) tools/local_experiment/gateway_warehouse/run.py plan --build-token "$(GATEWAY_WAREHOUSE_BUILD_TOKEN)"
local-gateway-warehouse-run:
	@$(PYTHON) tools/local_experiment/gateway_warehouse/run.py run --build-token "$(GATEWAY_WAREHOUSE_BUILD_TOKEN)"
local-gateway-warehouse-recover:
	@$(PYTHON) tools/local_experiment/gateway_warehouse/run.py recover --run-token "$(GATEWAY_WAREHOUSE_RUN_TOKEN)"
test-local-gateway-warehouse:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_gateway_warehouse*.py'
verify-local-experiment: test-local-gateway-warehouse

.PHONY: local-warehouse-lifecycle-plan local-warehouse-lifecycle-run local-warehouse-lifecycle-session local-warehouse-lifecycle-recover test-local-warehouse-lifecycle
local-warehouse-lifecycle-plan:
	@$(PYTHON) tools/local_experiment/warehouse_lifecycle/run.py plan --build-token "$(GATEWAY_WAREHOUSE_BUILD_TOKEN)"
local-warehouse-lifecycle-run:
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/warehouse_lifecycle/run.py run --build-token "$(GATEWAY_WAREHOUSE_BUILD_TOKEN)"
local-warehouse-lifecycle-session:
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/warehouse_lifecycle/session.py run --build-token "$(GATEWAY_WAREHOUSE_BUILD_TOKEN)"
.PHONY: local-warehouse-query-session
local-warehouse-query-session:
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/warehouse_lifecycle/session.py run --query-mode --build-token "$(GATEWAY_WAREHOUSE_BUILD_TOKEN)"

.PHONY: local-warehouse-session-status
local-warehouse-session-status:
	@$(PYTHON) tools/local_experiment/warehouse_lifecycle/status.py

.PHONY: local-warehouse-session-completion
local-warehouse-session-completion:
	@$(PYTHON) tools/local_experiment/warehouse_lifecycle/status.py --with-saved-completion

.PHONY: local-warehouse-session-request-status
local-warehouse-session-request-status:
	@$(PYTHON) tools/local_experiment/warehouse_lifecycle/status.py --request-id-from-env
.PHONY: local-warehouse-session-runtime-status
local-warehouse-session-runtime-status:
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/warehouse_lifecycle/runtime_status.py --session-from-env
local-warehouse-lifecycle-recover:
	@$(PYTHON) tools/local_experiment/warehouse_lifecycle/run.py recover --run-token-from-env
test-local-warehouse-lifecycle:
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_warehouse_lifecycle*.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_warehouse_session*.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_lifecycle_telemetry.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_warehouse_query.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_query_lifecycle_telemetry.py'
verify-local-experiment: test-local-warehouse-lifecycle

.PHONY: local-autonomous-plan test-local-autonomous-runner
local-autonomous-plan:
	@tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py

test-local-autonomous-runner:
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_autonomous_runner.py'

verify-local-experiment: test-local-autonomous-runner

.PHONY: test-local-source-candidate
test-local-source-candidate:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_source_candidate.py'
verify-local-experiment: test-local-source-candidate

.PHONY: local-mlflow-sync local-mlflow-prepare local-mlflow-plan local-mlflow-import local-mlflow-start local-mlflow-status local-mlflow-stop test-local-mlflow
local-mlflow-sync:
	@uv sync --project tools/local_experiment/mlflow --locked --no-build
local-mlflow-prepare:
	@$(PYTHON) tools/local_experiment/mlflow/runtime.py prepare
local-mlflow-plan:
	@$(PYTHON) tools/local_experiment/mlflow/import_evaluations.py --plan
local-mlflow-import:
	@$(PYTHON) tools/local_experiment/mlflow/import_evaluations.py
local-mlflow-train:
	@$(PYTHON) tools/local_experiment/mlflow/train.py
test-local-mlflow-training:
	@tools/local_experiment/mlflow/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_mlflow_training*.py'
.PHONY: local-mlflow-train test-local-mlflow-training
verify-local-experiment: test-local-mlflow-training
.PHONY: local-lake-training test-local-lake-training
local-lake-training:
	@tools/local_experiment/mlflow/.venv/bin/python -B tools/local_experiment/lake_training/run.py
test-local-lake-training:
	@tools/local_experiment/mlflow/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_lake_training*.py'
verify-local-experiment: test-local-lake-training
local-mlflow-start:
	@$(PYTHON) tools/local_experiment/mlflow/runtime.py start
local-mlflow-status:
	@$(PYTHON) tools/local_experiment/mlflow/runtime.py status
local-mlflow-stop:
	@$(PYTHON) tools/local_experiment/mlflow/runtime.py stop
local-mlflow-restore:
	@$(PYTHON) tools/local_experiment/mlflow/restore.py --manifest "$${SHIROKUMA_MLFLOW_BACKUP_MANIFEST:?set the saved backup-manifest.json path}"
local-mlflow-restore-readback:
	@$(PYTHON) tools/local_experiment/mlflow/restore_readback.py --restore-token "$${SHIROKUMA_MLFLOW_RESTORE_TOKEN:?set the fresh restore token}"
.PHONY: local-mlflow-restore local-mlflow-restore-readback
test-local-mlflow:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_mlflow*.py'
verify-local-experiment: test-local-mlflow

.PHONY: local-spark-prepare local-spark-plan local-spark-run local-spark-failure local-spark-recover test-local-spark
local-spark-prepare:
	@$(PYTHON) tools/local_experiment/spark/prepare.py
local-spark-plan:
	@$(PYTHON) tools/local_experiment/spark/run.py plan
local-spark-run:
	@$(PYTHON) tools/local_experiment/spark/run.py run --mode success
local-spark-failure:
	@$(PYTHON) tools/local_experiment/spark/run.py run --mode failure
local-spark-recover:
	@$(PYTHON) tools/local_experiment/spark/run.py recover --run-token "$(SPARK_RUN_TOKEN)"
test-local-spark:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_spark*.py'
verify-local-experiment: test-local-spark

.PHONY: local-spark-iceberg-prepare local-spark-iceberg-build local-spark-iceberg-run local-spark-iceberg-recover test-local-spark-iceberg
local-spark-iceberg-prepare:
	@$(PYTHON) tools/local_experiment/spark_iceberg/prepare.py
local-spark-iceberg-build:
	@$(PYTHON) tools/local_experiment/spark_iceberg/build.py --prepare-token "$(SPARK_ICEBERG_PREPARE_TOKEN)"
local-spark-iceberg-run:
	@$(PYTHON) tools/local_experiment/spark_iceberg/run.py run --build-token "$(SPARK_ICEBERG_BUILD_TOKEN)"
local-spark-iceberg-recover:
	@$(PYTHON) tools/local_experiment/spark_iceberg/run.py recover --run-token "$(SPARK_ICEBERG_RUN_TOKEN)"
test-local-spark-iceberg:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_spark_iceberg*.py'
# verify-local-experiment already includes these through test-local-spark.

.PHONY: local-spark-iceberg-remote
local-spark-iceberg-remote:
	@$(PYTHON) tools/local_experiment/spark_iceberg/remote_run.py --build-token "$(SPARK_ICEBERG_BUILD_TOKEN)"

.PHONY: local-spark-benchmark test-local-spark-benchmark
local-spark-benchmark:
	@$(PYTHON) tools/local_experiment/spark_benchmark/run.py --build-token "$(SPARK_ICEBERG_BUILD_TOKEN)"

test-local-spark-benchmark:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_spark_benchmark_*.py'

# test-local-spark already discovers test_local_spark_benchmark_*.py.

.PHONY: local-spark-benchmark-report local-mlflow-benchmark-plan local-mlflow-benchmark-import test-local-mlflow-benchmark
local-spark-benchmark-report:
	@$(PYTHON) tools/local_experiment/spark_benchmark/report.py
local-mlflow-benchmark-plan:
	@$(PYTHON) tools/local_experiment/mlflow/import_benchmarks.py --plan
local-mlflow-benchmark-import:
	@$(PYTHON) tools/local_experiment/mlflow/import_benchmarks.py
test-local-mlflow-benchmark:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_spark_benchmark_report.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_mlflow_benchmark_import.py'
verify-local-experiment: test-local-mlflow-benchmark

.PHONY: local-spark-branch test-local-spark-branch
local-spark-branch:
	@$(PYTHON) tools/local_experiment/spark_branch/run.py --build-token "$(SPARK_ICEBERG_BUILD_TOKEN)"
test-local-spark-branch:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_spark_branch*.py'
# verify-local-experiment includes these through test-local-spark.

.PHONY: local-spark-publish local-spark-publish-negative test-local-spark-publish
local-spark-publish:
	@$(PYTHON) tools/local_experiment/spark_publish/run.py --build-token "$(SPARK_ICEBERG_BUILD_TOKEN)" --mode positive
local-spark-publish-negative:
	@$(PYTHON) tools/local_experiment/spark_publish/run.py --build-token "$(SPARK_ICEBERG_BUILD_TOKEN)" --mode negative
test-local-spark-publish:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_spark_publish*.py'
# verify-local-experiment includes these through test-local-spark.

.PHONY: test-query-doctor-cli
test-query-doctor-cli: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_query_cli.py'

verify-local-experiment: test-query-doctor-cli

.PHONY: test-sre-bear-cli
test-sre-bear-cli: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_sre_cli.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -v -s tests -p 'test_sre_cli_log_signals.py'

verify-local-experiment: test-sre-bear-cli

.PHONY: test-policy-candidate-cli
test-policy-candidate-cli: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -s tests -p 'test_policy_candidate*.py'
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -s tests -p 'test_policy_cli.py'

verify-local-experiment: test-policy-candidate-cli

.PHONY: test-local-query-read
test-local-query-read:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_query_read_*.py'
verify-local-experiment: test-local-query-read

.PHONY: test-local-query-diagnose
test-local-query-diagnose: local-llm-sync
	@tools/local_experiment/llm/.venv/bin/python -m unittest discover -s tests -p 'test_query_diagnose_*.py'
verify-local-experiment: test-local-query-diagnose

.PHONY: test-local-query-snapshots
test-local-query-snapshots:
	@$(PYTHON) -m unittest discover -s tests -p 'test_query_snapshots_*.py'
verify-local-experiment: test-local-query-snapshots

.PHONY: test-local-query-filter
test-local-query-filter:
	@$(PYTHON) -m unittest discover -s tests -p 'test_query_filter_*.py'
verify-local-experiment: test-local-query-filter

.PHONY: test-local-query-count
test-local-query-count:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_local_query_count.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_query_count_*.py'
verify-local-experiment: test-local-query-count

.PHONY: test-local-query-reads
test-local-query-reads:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_query_reads.py'
verify-local-experiment: test-local-query-reads

.PHONY: test-local-catalog-read
test-local-catalog-read:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_catalog_read_*.py'
verify-local-experiment: test-local-catalog-read

.PHONY: local-notebook-sync local-notebook-run local-notebook-preview test-local-notebook test-local-notebook-selection
local-notebook-sync:
	@uv sync --project tools/local_experiment/notebook --locked --no-build --cache-dir tmp/local-experiment/notebook-cache
local-notebook-run:
	@tools/local_experiment/notebook/.venv/bin/python tools/local_experiment/notebook/runner.py
local-notebook-preview:
	@tools/local_experiment/notebook/.venv/bin/python tools/local_experiment/notebook/runner.py --request-stdin
test-local-notebook:
	@tools/local_experiment/notebook/.venv/bin/python -m unittest discover -v -s tests -p 'test_local_notebook*.py'
verify-local-experiment: test-local-notebook

test-local-notebook-selection:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_selection.py'
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_parameterized.py'
verify-local-experiment: test-local-notebook-selection

.PHONY: local-notebook-assets-run local-notebook-assets-preview test-local-notebook-assets test-local-notebook-assets-selection
local-notebook-assets-run: local-dataops-sync
	@tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assets/run.py
local-notebook-assets-preview:
	@tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assets/run.py --request-stdin
test-local-notebook-assets: local-dataops-sync
	@tools/local_experiment/dataops/.venv/bin/python -B -m unittest -v tests.test_notebook_dagster_assets
verify-local-experiment: test-local-notebook-assets

test-local-notebook-assets-selection:
	@tools/local_experiment/dataops/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_assets_selection.py'
verify-local-experiment: test-local-notebook-assets-selection

.PHONY: local-notebook-assist test-local-notebook-assist
local-notebook-assist:
	@tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assist/run.py --request-stdin
.PHONY: local-notebook-assist-filtered local-notebook-assist-count local-notebook-assets-filtered test-local-notebook-filtered test-local-notebook-assets-filtered
local-notebook-assist-filtered:
	@tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assist/run.py --filtered-request-stdin
local-notebook-assist-count:
	@tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assist/run.py --count-request-stdin
local-notebook-assets-filtered:
	@tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assets/run.py --filtered-request-stdin
test-local-notebook-filtered:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_filtered_*.py'
verify-local-experiment: test-local-notebook-filtered
test-local-notebook-assets-filtered:
	@tools/local_experiment/dataops/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_assets_filtered.py'
verify-local-experiment: test-local-notebook-assets-filtered
test-local-notebook-assist:
	@tools/local_experiment/dataops/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_assist_*.py'
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_describe_client.py'
	@tools/local_experiment/llm/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_local_provider_strict_json.py'
verify-local-experiment: test-local-notebook-assist

.PHONY: local-notebook-training-review test-local-notebook-training
local-notebook-training-review:
	@tools/local_experiment/notebook/.venv/bin/python -B tools/local_experiment/notebook/runner.py --training-token "$${SHIROKUMA_TRAINING_TOKEN:?set the saved training attempt token}"
test-local-notebook-training:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_training*.py'
verify-local-experiment: test-local-notebook-training
.PHONY: local-notebook-lake-training-review test-local-notebook-lake-training
local-notebook-lake-training-review:
	@tools/local_experiment/notebook/.venv/bin/python -B tools/local_experiment/notebook/runner.py --lake-training-token "$${SHIROKUMA_LAKE_TRAINING_TOKEN:?set a saved Iceberg training attempt token}"
test-local-notebook-lake-training:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lake_training*.py'
verify-local-experiment: test-local-notebook-lake-training

.PHONY: local-lake-predict test-local-lake-predict
local-lake-predict:
	@$(PYTHON) -B tools/local_experiment/mlflow/predict.py --training-token "$${SHIROKUMA_LAKE_TRAINING_TOKEN:?set a saved Iceberg training attempt token}" --request-stdin
test-local-lake-predict:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_predict.py'
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_saved_training_reader.py'
verify-local-experiment: test-local-lake-predict

.PHONY: local-notebook-lake-training test-local-notebook-train
local-notebook-lake-training:
	@tools/local_experiment/notebook/.venv/bin/python -B tools/local_experiment/notebook/runner.py --train-lake
test-local-notebook-train:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_train_*.py'
verify-local-experiment: test-local-notebook-train

.PHONY: local-training-runs test-local-training-runs
local-training-runs:
	@$(PYTHON) -B tools/local_experiment/mlflow/training_runs.py
test-local-training-runs:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_training_runs.py'
verify-local-experiment: test-local-training-runs

.PHONY: local-notebook-lab-sync local-notebook-lab-start local-notebook-lab-status local-notebook-lab-stop test-local-notebook-lab
local-notebook-lab-sync:
	@uv sync --project tools/local_experiment/notebook_lab --locked --no-build --cache-dir tmp/local-experiment/notebook-lab-cache
local-notebook-lab-start:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B tools/local_experiment/notebook_lab/runtime.py start
local-notebook-lab-status:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B tools/local_experiment/notebook_lab/runtime.py status
local-notebook-lab-stop:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B tools/local_experiment/notebook_lab/runtime.py stop
test-local-notebook-lab:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_runtime.py'
verify-local-experiment: test-local-notebook-lab

.PHONY: test-local-notebook-lab-query
test-local-notebook-lab-query:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_query.py'
verify-local-experiment: test-local-notebook-lab-query

.PHONY: test-local-mlflow-compare test-local-notebook-lab-compare
test-local-mlflow-compare:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_compare.py'
test-local-notebook-lab-compare:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_compare.py'
verify-local-experiment: test-local-mlflow-compare test-local-notebook-lab-compare

.PHONY: test-local-comparison-prediction local-comparison-runs test-local-comparison-runs
test-local-comparison-prediction:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_notebook_lab_comparison_saved.py'
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_comparison_predict.py'
verify-local-experiment: test-local-comparison-prediction
local-comparison-runs:
	@$(PYTHON) -B tools/local_experiment/mlflow/comparison_runs.py
test-local-comparison-runs:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_comparison_runs.py'
verify-local-experiment: test-local-comparison-runs

.PHONY: local-activation-enable local-activation-status local-activation-run test-local-activation
local-activation-enable:
	@$(PYTHON) -B tools/local_experiment/activation/runtime.py enable
local-activation-status:
	@$(PYTHON) -B tools/local_experiment/activation/runtime.py status
local-activation-run:
	@tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/activation/run.py
test-local-activation:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_activation_runtime.py'
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_local_activation_grafana.py'
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_local_activation_fixture.py'
	@tools/local_experiment/dataops/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_local_activation_sensor.py'
	@tools/local_experiment/dataops/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_local_activation_run.py'
verify-local-experiment: test-local-activation

.PHONY: test-local-notebook-lab-train
test-local-notebook-lab-train:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_train.py'
verify-local-experiment: test-local-notebook-lab-train

.PHONY: local-ray-predict test-local-ray
local-ray-predict:
	@tools/local_experiment/ray/.venv/bin/python -B tools/local_experiment/ray/run.py --training-token "$${TRAINING_TOKEN:-}"
test-local-ray:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_local_ray_prediction.py'
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_local_ray_runtime.py'
verify-local-experiment: test-local-ray

.PHONY: local-mlflow-comparison-plan local-mlflow-comparison-import test-local-mlflow-comparison-import test-local-notebook-lab-record-comparison
local-mlflow-comparison-plan:
	@$(PYTHON) -B tools/local_experiment/mlflow/import_comparisons.py --comparison-token "$${COMPARISON_TOKEN:-}" --plan
local-mlflow-comparison-import:
	@tools/local_experiment/mlflow/.venv/bin/python -B tools/local_experiment/mlflow/import_comparisons.py --comparison-token "$${COMPARISON_TOKEN:-}"
test-local-mlflow-comparison-import:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_import_comparisons.py'
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_comparison_restore.py'
test-local-notebook-lab-record-comparison:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_record_comparison.py'
verify-local-experiment: test-local-mlflow-comparison-import test-local-notebook-lab-record-comparison

.PHONY: local-mlflow-register-ridge local-mlflow-resolve-ridge test-local-mlflow-registry
local-mlflow-register-ridge:
	@tools/local_experiment/mlflow/.venv/bin/python -B tools/local_experiment/mlflow/registry.py register --training-token "$${SHIROKUMA_LAKE_TRAINING_TOKEN:?set a completed Iceberg training token}" --backup-manifest "$${SHIROKUMA_MLFLOW_BACKUP_MANIFEST:?set a fresh stopped-store backup-manifest.json path}"
local-mlflow-resolve-ridge:
	@$(PYTHON) -B tools/local_experiment/mlflow/registry.py resolve --name shirokuma-local-ridge --version "$${SHIROKUMA_MLFLOW_MODEL_VERSION:?set an explicit numeric model version}"
test-local-mlflow-registry:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_registry*.py'
verify-local-experiment: test-local-mlflow-registry

.PHONY: test-local-notebook-registry
test-local-notebook-registry:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_registry.py'
verify-local-experiment: test-local-notebook-registry

.PHONY: local-spark-comet-prepare local-spark-comet-build local-spark-comet-run local-spark-comet-recover test-local-spark-comet
local-spark-comet-prepare:
	@$(PYTHON) -B tools/local_experiment/spark_comet/prepare.py
local-spark-comet-build:
	@$(PYTHON) -B tools/local_experiment/spark_comet/build.py --prepare-token "$(SPARK_COMET_PREPARE_TOKEN)" --base-build-token "$(SPARK_ICEBERG_BUILD_TOKEN)"
local-spark-comet-run:
	@$(PYTHON) -B tools/local_experiment/spark_comet/run.py run --build-token "$(SPARK_COMET_BUILD_TOKEN)"
local-spark-comet-recover:
	@$(PYTHON) -B tools/local_experiment/spark_comet/run.py recover --run-token "$(SPARK_COMET_RUN_TOKEN)"
test-local-spark-comet:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_spark_comet_*.py'
verify-local-experiment: test-local-spark-comet

.PHONY: local-spark-comet-benchmark local-spark-comet-benchmark-recover
local-spark-comet-benchmark:
	@$(PYTHON) -B tools/local_experiment/spark_comet/benchmark.py run --stock-build-token "$(SPARK_ICEBERG_BUILD_TOKEN)" --comet-build-token "$(SPARK_COMET_BUILD_TOKEN)"
local-spark-comet-benchmark-recover:
	@$(PYTHON) -B tools/local_experiment/spark_comet/benchmark.py recover --run-token "$(SPARK_COMET_BENCHMARK_TOKEN)" --variant "$(SPARK_COMET_VARIANT)"

.PHONY: local-finops-report local-finops-html local-finops-history test-local-finops
local-finops-report:
	@$(PYTHON) -B tools/local_experiment/finops/run.py
local-finops-html:
	@$(PYTHON) -B tools/local_experiment/finops/render.py
local-finops-history:
	@$(PYTHON) -B tools/local_experiment/finops/history.py
test-local-finops:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_local_finops*.py'
verify-local-experiment: test-local-finops

.PHONY: test-local-notebook-lab-count
test-local-notebook-lab-count:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_count.py'
verify-local-experiment: test-local-notebook-lab-count

.PHONY: test-local-notebook-count-client
test-local-notebook-count-client:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_count_client.py'
verify-local-experiment: test-local-notebook-count-client

.PHONY: test-local-notebook-metadata-client test-local-notebook-lab-metadata
test-local-notebook-metadata-client:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_metadata_client.py'
test-local-notebook-lab-metadata:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_metadata.py'
verify-local-experiment: test-local-notebook-metadata-client test-local-notebook-lab-metadata

.PHONY: test-local-notebook-snapshot-client test-local-notebook-lab-snapshots
test-local-notebook-snapshot-client:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_snapshot_client.py'
test-local-notebook-lab-snapshots:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_snapshots.py'
verify-local-experiment: test-local-notebook-snapshot-client test-local-notebook-lab-snapshots

.PHONY: test-local-notebook-diagnose-client test-local-notebook-lab-diagnose
test-local-notebook-diagnose-client:
	@tools/local_experiment/notebook/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_diagnose_client.py'
test-local-notebook-lab-diagnose:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_diagnose.py'
verify-local-experiment: test-local-notebook-diagnose-client test-local-notebook-lab-diagnose

.PHONY: local-mlflow-serving-start local-mlflow-serving-status local-mlflow-serving-stop test-local-mlflow-serving
local-mlflow-serving-start:
	@tools/local_experiment/mlflow/.venv/bin/python -B -m tools.local_experiment.mlflow.serving_runtime start --version "$${SHIROKUMA_MLFLOW_MODEL_VERSION:?set the explicit local Registry version}"
local-mlflow-serving-status:
	@tools/local_experiment/mlflow/.venv/bin/python -B -m tools.local_experiment.mlflow.serving_runtime status
local-mlflow-serving-stop:
	@tools/local_experiment/mlflow/.venv/bin/python -B -m tools.local_experiment.mlflow.serving_runtime stop
test-local-mlflow-serving:
	@tools/local_experiment/mlflow/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_mlflow_serving*.py'
verify-local-experiment: test-local-mlflow-serving

.PHONY: local-mlflow-autolog test-local-mlflow-autolog
local-mlflow-autolog:
	@tools/local_experiment/mlflow/.venv/bin/python -B tools/local_experiment/mlflow/autolog.py --training-token "$${SHIROKUMA_LAKE_TRAINING_TOKEN:?set the saved Iceberg training token}" --backup-manifest "$${SHIROKUMA_MLFLOW_BACKUP_MANIFEST:-}"
test-local-mlflow-autolog:
	@tools/local_experiment/mlflow/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_mlflow_autolog*.py'
verify-local-experiment: test-local-mlflow-autolog

.PHONY: local-mlflow-autolog-recover
local-mlflow-autolog-recover:
	@tools/local_experiment/mlflow/.venv/bin/python -B tools/local_experiment/mlflow/autolog.py --training-token "$${SHIROKUMA_LAKE_TRAINING_TOKEN:?set the saved Iceberg training token}" --recover

.PHONY: test-local-notebook-catalog-client test-local-notebook-lab-catalog
test-local-notebook-catalog-client:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_notebook_catalog_client.py'
test-local-notebook-lab-catalog:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_catalog.py'
verify-local-experiment: test-local-notebook-catalog-client test-local-notebook-lab-catalog

.PHONY: test-local-mlflow-tune test-local-notebook-lab-tune
test-local-mlflow-tune:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_mlflow_tune.py'
test-local-notebook-lab-tune:
	@tools/local_experiment/notebook_lab/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_lab_tune.py'
verify-local-experiment: test-local-mlflow-tune test-local-notebook-lab-tune

.PHONY: local-notebook-assets-training test-local-notebook-assets-training test-local-notebook-assets-autolog-worker
local-notebook-assets-training:
	@if test -n "$${BACKUP_MANIFEST:-}"; then \
		tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assets/training.py --training-token "$${TRAINING_TOKEN:-}" --backup-manifest "$$BACKUP_MANIFEST"; \
	else \
		tools/local_experiment/dataops/.venv/bin/python -B tools/local_experiment/notebook_assets/training.py --training-token "$${TRAINING_TOKEN:-}"; \
	fi
test-local-notebook-assets-training:
	@tools/local_experiment/dataops/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_notebook_assets_training.py'
test-local-notebook-assets-autolog-worker:
	@$(PYTHON) -B -m unittest discover -v -s tests -p 'test_notebook_assets_autolog_worker.py'
verify-local-experiment: test-local-notebook-assets-training test-local-notebook-assets-autolog-worker
