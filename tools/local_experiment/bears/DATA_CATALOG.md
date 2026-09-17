# Local Data → Catalog handoff

This entrypoint connects the existing Data Engineer and Catalog Curator
workflows. One fixed-fixture request produces verified, disposable dbt results
and then registers their historical descriptions, owner and lineage in the
existing OpenMetadata service. It uses the configured local Qwen endpoint.

## Product CLI

From the active checkout, build a local binary and use the existing dedicated
Python environment. The CLI does not install dependencies or start services.

```sh
rtk proxy go build -o ./tmp/shirokuma ./cmd/shirokuma
rtk proxy ./tmp/shirokuma data run --case name_lengths_ja
rtk proxy ./tmp/shirokuma data run --request-stdin
rtk proxy ./tmp/shirokuma data status TOKEN
rtk proxy ./tmp/shirokuma data resume TOKEN
```

`TOKEN` is the 32-character lowercase hexadecimal `handoff_token` emitted by
`run`. The profile is fixed to `local-experiment`. Outside the checkout, pass
`--repo-root /path/to/Shirokuma`; it must contain the checked-in bridge and
existing `tools/local_experiment/llm/.venv` environment.

Choose exactly one of `--request-stdin` or the fixed cases `uppercase_names_ja`,
`name_lengths_ja`, `row_count_ja`. Stdin accepts one nonblank UTF-8 line, at most
512 characters and 4096 bytes including a final CR/LF. Control characters and
multiple lines are rejected before workflow invocation. Request text travels
only through memory and the child stdin; it is not placed in argv, environment,
raw logs or receipts. The journal stores its hash and length.

Output is bounded JSONL. The early `allocated` event only identifies the chosen
token: it proves neither durable intent nor a model/API call. The final result
is released after the child process exit agrees with its receipt. `execution`
is `invoked` when the wrapper was called, which is not proof of inference.
`status` uses `read-only` and inspects existing private files without changing
the journal or calling a model/API. `journal-only` observations are distinct
from fully checked `saved-evidence`. A busy lock returns `busy`, and missing or
invalid evidence never becomes success. This is historical status, not a fresh
observation of the running catalog.

Run/resume exits are 0 for verified completion, 2 for unsupported requests, and
1 for failure or uncertainty. Status exits 0 when inspection succeeds, even if
the saved operation failed or remains unknown; inspect its `result` and
`verification` fields. Missing/invalid evidence exits 1. A confirmed child
interruption can preserve exit 130/143 with an unknown outcome; it never becomes
success. The 20-minute deadline remains unknown/exit 1.

On cancellation or protocol failure, the CLI signals only the Python owner and
waits up to 240 seconds for its scoped cleanup. It does not force-kill the group
or automatically restart the operation. If cleanup/process completion cannot
be confirmed, the error retains the token and child PID. Check `data status
TOKEN` and the owned receipt before further action; process termination alone
does not prove rollback. Interrupted or uncertain Data work is never repeated
by `resume`. Only a verified Data result may continue to a new Catalog attempt.

The lower-level development targets are also available:

```sh
rtk make local-data-catalog-smoke
rtk make local-data-catalog-request
rtk make local-data-catalog-resume DATA_CATALOG_HANDOFF_TOKEN=...
```

The request target reads one bounded UTF-8 line from standard input, following
the [Data Engineer input contract](DATA_ENGINEER.md). The default smoke uses the
existing fixed name-length case. The command prints its handoff token before
starting Data so the same operation can be inspected or resumed. To choose a
fixed case or supply a token explicitly, use the dedicated local environment:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python \
  tools/local_experiment/bears/run_data_catalog.py run --case uppercase_names_ja
```

## Durable phases

The private journal lives under
`tmp/local-experiment/data-catalog/<handoff-token>/`. It binds the input metadata,
Data output identity and Catalog attempts before invoking each workflow. Input
text stays in memory; only its hash, length and selected mode are retained.
A lock prevents concurrent operations on the same handoff.

Data is invoked at most once per handoff. The handoff verifies its saved result,
DataOps materialization and cleanup, and the retained dbt/Dagster artifacts
before passing the resulting Data Plan identity to Catalog. An unsupported
request or unsuccessful Data run starts no Catalog operation.

If execution stops after Data completes, resume uses the preselected output
location to recover its verified success. It does not create another Data run.
If success cannot be established, the journal retains failure or uncertainty.
The entrypoint does not automatically repeat a possibly executed Data action.

Catalog retries use the same verified Data Plan identity and a new attempt
receipt. The existing Curator validates ownership and saved lineage before
completing partial metadata; an already complete registration becomes a no-op.
Previous attempts remain intact. Uncertain writes do not imply rollback.

A completed handoff resumes by verifying its saved evidence and returning the
recorded completion. It calls neither model nor workflow again. This replay is
historical evidence; use `rtk make local-lineage-coverage` for a new observation
of the current catalog.

## Scope

The existing fixed fixture and three Data transformations remain the available
data operations. Generated SQL tables are removed after verification; Catalog
stores their historical metadata. Free-text interpretation and execution of the
selected typed plan remain separate evidence boundaries.

Data and Catalog keep their own Pawprint/Loki/OTel evidence. The handoff journal
links their output identities and preserves a Data success if Catalog fails.
Cancellation retains an interrupted result and propagates the original stop
signal after attempting audit and trace cleanup. Unknown usage stays null.
No cloud provider, new service, arbitrary SQL, endpoint or output path is added.
