# CLI scope

The root `AGENTS.md` applies here. Keep the command entrypoint thin and put reusable command behavior under `internal/cli`.

- Preserve deterministic `--help`, `version`, and `--version` behavior.
- Do not read workstation-local credentials or secrets during preparation, build, or tests.
- Run `gofmt`, `go test ./...`, and `go vet ./...` for Go changes.
