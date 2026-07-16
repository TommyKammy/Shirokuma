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
GITHUB_OWNER ?= TommyKammy
FLUX_GITHUB_REPOSITORY ?= Shirokuma
FLUX_GITHUB_PRIVATE ?= false
FLUX_BOOTSTRAP_BRANCH ?= flux/bootstrap-local-lite
FLUX_PATH ?= deploy/gitops/clusters/local-lite

.PHONY: prepare verify verify-security verify-policy verify-design-context verify-preflight-parser verify-supervisor-workflow-docs verify-colima-baseline verify-gitops-bootstrap verify-gitops-image-admission verify-gitops-teardown verify-kyverno-bootstrap verify-object-storage-profile verify-iceberg-table-bootstrap verify-trino-bootstrap verify-dataops-bootstrap verify-ui-design-baseline verify-observability-baseline verify-repository-skeleton verify-go supervisor-preflight colima-start colima-status tofu-init tofu-fmt tofu-validate flux-version-check gitops-bootstrap gitops-status gitops-reconcile gitops-teardown check-newlines check-trailing-whitespace check-required-files check-no-secret-filenames

verify: check-required-files verify-design-context verify-preflight-parser verify-supervisor-workflow-docs verify-colima-baseline verify-gitops-bootstrap verify-gitops-teardown verify-kyverno-bootstrap verify-object-storage-profile verify-iceberg-table-bootstrap verify-trino-bootstrap verify-ui-design-baseline verify-observability-baseline verify-repository-skeleton verify-go verify-security verify-policy check-newlines check-trailing-whitespace check-no-secret-filenames

prepare: verify-design-context

verify-security:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_supply_chain_security.py'
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_trivyignore.py'
	@$(PYTHON) scripts/verify_trivyignore.py
	@$(PYTHON) scripts/verify_supply_chain.py scan-secrets --repo .
	@$(PYTHON) scripts/verify_supply_chain.py check-images --manifest security/resident-images.json --repo . --profile local-lab --exceptions security/resident-image-exceptions.json
	@$(PYTHON) scripts/verify_trusted_image.py audit --root .

verify-policy:
	@command -v $(KYVERNO) >/dev/null || { echo "kyverno $(KYVERNO_VERSION) is required for policy verification"; exit 1; }
	@test "$$($(KYVERNO) version | awk '/^Version:/ {print $$2}')" = "$(patsubst v%,%,$(KYVERNO_VERSION))" || { echo "kyverno $(KYVERNO_VERSION) is required for policy verification"; exit 1; }
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_policy_exceptions.py'
	@$(PYTHON) scripts/verify_policy_exceptions.py
	@$(KYVERNO) test tests/policy --require-tests
	@$(KYVERNO) apply policies/ --resource tests/policy/allowed.yaml.fixture

verify-design-context:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_arm64_compatibility_matrix.py'
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

verify-iceberg-table-bootstrap:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_iceberg_table_bootstrap.py'

verify-trino-bootstrap:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_trino_bootstrap.py'

verify-dataops-bootstrap:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_dataops_bootstrap.py'

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
	@test "$$($(FLUX) version --client 2>/dev/null | awk '/^flux:/ {print $$2}')" = "$(FLUX_VERSION)" || { echo "flux $(FLUX_VERSION) is required"; exit 1; }

gitops-bootstrap: colima-status verify-gitops-image-admission tofu-init flux-version-check
	@test -n "$${GITHUB_TOKEN:-}" || { echo "GITHUB_TOKEN is required for Flux bootstrap and is never persisted by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_operator_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_access_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_operator_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_secret_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_access_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_secret_key is required for OpenTofu apply and is never persisted or printed by this target"; exit 1; }
	@legacy_pvc="$$(kubectl --context $(KUBE_CONTEXT) -n shirokuma-dev get pvc seaweedfs-data-seaweedfs-0 --ignore-not-found -o name)" || { echo "legacy PVC lookup failed; refusing OpenTofu apply"; exit 1; }; test -z "$$legacy_pvc" || { echo "legacy shirokuma-dev/seaweedfs-data-seaweedfs-0 PVC exists; complete a verified export and the whole-profile nuke/rebuild procedure before bootstrap"; exit 1; }
	@$(TOFU) -chdir=$(TOFU_DIR) apply -input=false -auto-approve
	@$(FLUX) bootstrap github --owner=$(GITHUB_OWNER) --repository=$(FLUX_GITHUB_REPOSITORY) --private=$(FLUX_GITHUB_PRIVATE) --branch=$(FLUX_BOOTSTRAP_BRANCH) --path=$(FLUX_PATH) --personal --components=source-controller,kustomize-controller,helm-controller,notification-controller --version=$(FLUX_VERSION) --context=$(KUBE_CONTEXT)

gitops-status:
	@kubectl --context $(KUBE_CONTEXT) -n flux-system get deployments
	@$(FLUX) get sources git -A --context=$(KUBE_CONTEXT)
	@$(FLUX) get kustomizations -A --context=$(KUBE_CONTEXT)

gitops-reconcile: flux-version-check
	@$(FLUX) reconcile source git flux-system -n flux-system --context=$(KUBE_CONTEXT)
	@$(FLUX) reconcile kustomization flux-system -n flux-system --with-source --context=$(KUBE_CONTEXT)
	@$(FLUX) reconcile kustomization shirokuma-dev -n flux-system --context=$(KUBE_CONTEXT)
	@object_storage="$$(kubectl --context $(KUBE_CONTEXT) -n flux-system get kustomization.kustomize.toolkit.fluxcd.io shirokuma-object-storage --ignore-not-found -o name)" || { echo "shirokuma-object-storage Kustomization lookup failed"; exit 1; }; if test -n "$$object_storage"; then $(FLUX) reconcile kustomization shirokuma-object-storage -n flux-system --context=$(KUBE_CONTEXT); else echo "shirokuma-object-storage Kustomization is absent; skipping reconcile"; fi

gitops-teardown: tofu-init
	@test -n "$${TF_VAR_seaweedfs_s3_operator_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_access_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_operator_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_operator_secret_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_access_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_access_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
	@test -n "$${TF_VAR_seaweedfs_s3_application_secret_key:-}" || { echo "TF_VAR_seaweedfs_s3_application_secret_key is required for OpenTofu destroy and is never persisted or printed by this target"; exit 1; }
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
	@test -f tests/test_gitops_teardown.py
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
