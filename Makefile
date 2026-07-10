SHELL := /bin/bash
PYTHON ?= python3
PREFLIGHT_REF ?= origin/main

.PHONY: prepare verify verify-security verify-design-context verify-preflight-parser verify-supervisor-workflow-docs verify-colima-baseline verify-ui-design-baseline verify-repository-skeleton verify-go supervisor-preflight colima-start colima-status check-newlines check-trailing-whitespace check-required-files check-no-secret-filenames

verify: check-required-files verify-design-context verify-preflight-parser verify-supervisor-workflow-docs verify-colima-baseline verify-ui-design-baseline verify-repository-skeleton verify-go verify-security check-newlines check-trailing-whitespace check-no-secret-filenames

prepare: verify-design-context

verify-security:
	@$(PYTHON) -m unittest discover -v -s tests -p 'test_supply_chain_security.py'
	@$(PYTHON) scripts/verify_supply_chain.py scan-secrets --repo .
	@$(PYTHON) scripts/verify_supply_chain.py check-images --manifest security/resident-images.json --repo .

verify-design-context:
	@$(PYTHON) scripts/verify_design_context.py

verify-preflight-parser:
	@$(PYTHON) -m unittest discover -s tests -p 'test_preflight_supervisor_issues.py'

verify-supervisor-workflow-docs:
	@$(PYTHON) -m unittest discover -s tests -p 'test_codex_supervisor_workflow_docs.py'

verify-colima-baseline:
	@$(PYTHON) -m unittest discover -s tests -p 'test_colima_baseline*.py'

colima-start:
	@./scripts/colima_baseline.sh start

colima-status:
	@./scripts/colima_baseline.sh status

verify-ui-design-baseline:
	@$(PYTHON) -m unittest discover -s tests -p 'test_ui_design_baseline.py'

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
	@test -f security/resident-images.json

check-newlines:
	@missing=0; \
	while IFS= read -r file; do \
		[ -f "$$file" ] || continue; \
		case "$$file" in \
			*.png|*.jpg|*.jpeg|*.gif|*.ico|*.pdf|*.zip|*.gz|*.tgz|*.jar|*.war) continue ;; \
		esac; \
		if [ -s "$$file" ] && [ "$$(tail -c 1 "$$file" | wc -l | tr -d ' ')" = "0" ]; then \
			echo "missing final newline: $$file"; \
			missing=1; \
		fi; \
	done < <(git ls-files); \
	exit $$missing

check-trailing-whitespace:
	@if git grep -nE '[[:blank:]]$$' -- ':!LICENSE' ':!*.png' ':!*.jpg' ':!*.jpeg' ':!*.gif' ':!*.ico' ':!*.pdf'; then \
		echo "trailing whitespace found"; \
		exit 1; \
	fi

check-no-secret-filenames:
	@if git ls-files | grep -Ei '(^|/)(\.env|.*\.(pem|key|p12|pfx|token|secret))$$'; then \
		echo "secret-like filename is tracked"; \
		exit 1; \
	fi
