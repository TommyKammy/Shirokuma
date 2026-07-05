# Contributing

Run the repository-owned verification command before opening a pull request:

```bash
make verify
```

Keep machine-local state, credentials, generated data, and large warehouse
scratch files out of git. If a local setting is required for development,
document the variable name in the repository and keep the value outside the
repository.

