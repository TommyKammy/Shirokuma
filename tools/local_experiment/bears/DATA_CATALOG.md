# Local Data → Catalog handoff

This entrypoint connects the existing Data Engineer and Catalog Curator
workflows. One fixed-fixture request produces verified, disposable dbt results
and then registers their historical descriptions, owner and lineage in the
existing OpenMetadata service. It uses the configured local Qwen endpoint.

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
