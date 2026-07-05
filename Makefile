SHELL := /bin/bash

.PHONY: prepare verify check-newlines check-trailing-whitespace check-required-files check-no-secret-filenames

verify: check-required-files check-newlines check-trailing-whitespace check-no-secret-filenames

prepare:
	@true

check-required-files:
	@test -f README.md
	@test -f LICENSE
	@test -f .gitignore
	@test -f .github/workflows/ci.yml

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
