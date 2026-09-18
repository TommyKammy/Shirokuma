# Fixed local lakehouse Notebook

The original fixed profile passed two explicit real-kernel executions and
independent saved-evidence verification. The runner connects registered Notebook
templates to the existing Reader CLI and local MLflow evaluation experiment.
The optional request profile below adds a bounded table preview. The dated
verification sections distinguish the original fixed profile from the selected-table
preview and its default compatibility run. This is a local partial implementation of
[WP-L4-NB-001](../../../docs/design/06_WorkPackages/L4/WP-L4-NB-001_JupyterHub_bootstrap.md).

The dedicated Python 3.12 environment pins nbclient 0.11.0, nbformat 5.11.1 and
ipykernel 7.3.0 in `pyproject.toml` and `uv.lock`. It does not install a global
kernelspec or start JupyterHub. Without input, the runner uses
`examples/local_lakehouse.ipynb`. With `--request-stdin`, it uses the separately
registered `examples/local_table_preview.ipynb`. Neither profile accepts a
caller-provided Notebook path or code.

## Default observations

The Notebook invokes the existing Go Reader once for
`query select --table smoke.fixture_v1 --columns id,name --limit 10`. The Reader discovers
and pins the table snapshot, validates typed rows, and drains its owned queries
before returning success. The Notebook displays that verified result. It does
not add direct Trino or PySpark access, arbitrary SQL, or data/catalog writes.

It then reads the fixed loopback MLflow evaluation experiment
`shirokuma-local-evaluation-receipts-v1`. The expected current inventory is 12
evaluation runs. The four Spark benchmark runs belong to a separate experiment
and are excluded. Displayed data preserves suite/provider/case boundaries and
unknown values, including null cost. MLflow run times describe import activity,
not the original inference timeline. This read performs no new inference,
evaluation, MLflow logging, training, or autologging.

## Preview a selected table

Pass a Reader v1 request from this checkout:

```sh
rtk proxy printf '%s\n' '{"command":"select","table":"smoke.fixture_v1","columns":["name"],"limit":1}' | rtk make local-notebook-preview
```

`local-notebook-preview` passes stdin directly to the dedicated environment's
`runner.py --request-stdin`; it does not run dependency sync. The dedicated
selection tests are available through `rtk make test-local-notebook-selection`.

Input is at most 4096 bytes and has exactly four fields: `command: "select"`,
`table`, `columns` and `limit`. It reuses the [Reader's validation](../query_read/README.md):
an existing table in `smoke` or `local_experiment`, 1–16 distinct scalar column
names and an integer limit of 1–100. Table and column existence/type are checked
by the Reader during discovery. No SQL, snapshot ID, filter, extra request keys
or arbitrary endpoint is accepted. This profile reads the current head discovered
by the Reader, with its usual before/after identity checks and truncation flag.

The normalized request is saved as private canonical `request.json` with its
SHA-256 before use. The selected registered template calls `read_requested_table`;
the default template retains its fixed `read_fixture` wrapper. Reader argv,
process ownership checks and the typed result must all match the same validated
request. Changing the request or returning another table/column selection does
not authorize a successful Notebook result.

The custom preview still makes one Reader invocation, then reads the same MLflow
evaluation experiment: 12 evaluation runs, excluding four benchmark runs. It
does not change inference, logging, cost-null or import-time semantics. Existing
no-argument execution and the fixed NotebookAssets profile remain unchanged.
Rows appear in the explicit Notebook output and its private executed artifact;
this is not a facility for executing arbitrary Notebook code.

## Execution and evidence

The default entry points keep their existing no-argument behavior:

```sh
rtk make local-notebook-sync
rtk make test-local-notebook
rtk make local-notebook-run
```

The sync target uses `uv sync --project tools/local_experiment/notebook --locked
--no-build --cache-dir tmp/local-experiment/notebook-cache`. The test target uses the dedicated environment for
`test_local_notebook*.py`; the run target invokes its Python and `runner.py`
without arguments.

Git contains the template with empty outputs and null execution counts. Executed
Notebook output, private kernel connection information, Reader logs and receipts
stay under `tmp/local-experiment/notebook-runs/<token>/`. The output includes
`executed.ipynb`, `result.json`, Reader stdout/intent/status and
`evaluations.json`. They are not copied back into the template.
The Notebook result may contain the selected query's typed table rows;
credentials, raw driver errors and connection secrets are not public output.

Success requires the Reader receipt to prove its query drain and cleanup and the
owner to close its kernel. Failure and cancellation retain private evidence and
must not turn an uncertain Reader result into success or an automatic retry.
The Reader's normal wait is 240 seconds and its interrupt cleanup wait is 245
seconds. After interrupting the kernel, the runner allows 260 seconds for the
cell/Reader cleanup, then requests kernel shutdown with a 30-second deadline and reaps its
owned process. If cleanup cannot be confirmed, it retains an unknown result,
PID and evidence; it does not automatically retry, kill or rerun the operation.
Only processes created by the run are eligible for cleanup. Existing MLflow
services, stored runs, artifacts and backups remain in place.

## 2026-09-18 selected-table preview verification

The custom preview `fa53c3cc80924087af29586b02cd339e` passed in 18.009 seconds.
It selected `[ok,id,amount,name,day]` with limit 2 from a newly owned five-column
fixture. The Reader returned two typed rows, fetched three and reported
`truncated: true`; the display limit is not the table row count. The private
request SHA-256 was `3fc19978f04ef6d92f02eea12dbfd71249dfd567cd869fff5ff26f5e7589b05e`.
The no-argument compatibility run `53a2f8a2603a4453a6937d20fff2e550`
passed in 18.262 seconds and retained the original fixed two-row result.
These are separate custom/default executions, not retry or performance evidence.

Both runs executed three code cells, displayed the same 12 evaluation runs with
four benchmark runs excluded, and confirmed Reader and kernel cleanup. There
were no new model calls or MLflow writes and no failed actual Notebook attempt
in this increment. Selection14, parameterized15 and existing fake24 tests passed;
full local regression passed 68 suites / 2468 tests (2467 passed and one existing
optional skip). The original fixed Notebook and NotebookAssets remain unchanged.

The disposable fixture was dropped once with the same table UUID, with all ten
retained tables and six retained projections restored. Before/after guards
matched 63 sources, 400 retained files, MLflow's 16 runs / 1320 metric points /
16 artifacts and AgentHouse's 519 rows. Independent saved verification passed
451 checks (51 semantic checks plus 400 retained-file checks) across 110 inputs.
It confirmed ten distinct terminal Reader query IDs, both kernels and Reader
processes gone, connection files removed and fixture restoration. Fixture setup
and cleanup SQL are separate from those ten Reader queries.
Evidence is under
`tmp/local-experiment/notebook-selection-checks/59d9d0ca8ab741f4903ad9371fa05a88/`.

The two new Notebook run directories contain 33 files, 20,424,079 bytes
(20,516,864 bytes allocated). Run directories are private mode 700; sensitive
files, rows and receipts are mode 600 and owned binaries are mode 700. IPython's
copied public startup/README files can be mode 644 under those private ancestors.
This does not establish JupyterHub, multi-user isolation, arbitrary Notebook
execution, PySpark, training/autolog or production acceptance.

## 2026-09-18 verification

Runs `fcd6b48ab9b84f86bba01058cd909087` and
`093b1e1c4ad34f3aa01d71cc8413edfd` passed in 18.275 and 18.241 seconds.
Each executed three code cells, made one fixed Reader invocation and displayed
12 evaluation runs. Both receipts confirm Reader and kernel cleanup. These are
two explicit executions, not automatic retry or a performance benchmark.

The initial run `1f0c75ef56db4bad98d1143bda983cc9` failed before the Reader or
MLflow calls: Jupyter changed the connection file's parent from mode 700 to
1700, which the private-directory guard correctly rejected. Its failed receipt
was retained and its kernel closed. Moving the connection file into the private
`jupyter-runtime/` subdirectory preserved the run directory's mode 700. The
fixed source passed 26 focused tests and both actual executions.

The 26 tests include two harmless kernel tests; the remaining tests use fakes.
They cover exact Reader status, first cancellation preservation, cleanup grace,
and no forced kill or blind retry. The broader local regression passed 62 suites,
2297 tests (2296 passed and one existing optional skip) before the connection-file
placement fix. The first regression attempt was affected by a private wrapper's
umask 077 changing an existing test fixture's requested mode; the normal 022
test environment passed, while test logs remained mode 600. Original failures
were preserved.

`runtime-before-fixed.json` and `runtime-after.json` show identical 65 source
hashes and MLflow's 59-table row multisets, 16 artifacts and indexes. The store
remains at 16 runs and 1320 metric points. Secret scanning passed. Independent
verification passed 62 checks bound to 104 saved inputs: both typed Reader
results have the same table UUID/head, ten distinct query IDs are terminal,
each Notebook shows 12 evaluation runs while excluding four benchmark runs, and
both owned Reader/kernel process pairs are gone with no connection file left.
This verification made no additional API calls. Two initial verifier assumptions
were corrected without rerunning the product; the failed checks and original
failed Notebook receipt remain saved.

Evidence is under
`tmp/local-experiment/notebook-checks/8d4a2ec9c0f5413e85009312de57faa9/`.
The after snapshot SHA-256 is
`7c38441ad4fd372261a581c3d2cafcae298ec49f94c2390b677689e17c29472e`.
No model inference, MLflow mutation or Hub/global kernelspec installation was
part of these executions.

The independent proof is `independent-saved-verification.json`, SHA-256
`70d4f50fc66522c2662be0498dfad860c15d1c42429144c6b53a7611a30eaa1e`.

## Storage and stopping use

Measured allocated storage was 71,782,400 bytes for the dedicated `.venv`,
82,808,832 bytes for the check directory's `uv-cache`, and 30,420,992 bytes for
all three actual run directories, including their Go binaries. These are
observations from this execution, not fixed capacity bounds. No retained data
or backup was migrated.

To stop using this increment, stop invoking its commands and confirm that its
owned Reader and kernel have exited. Preserve execution evidence and the
existing MLflow store, fixture and backups. There is no global kernelspec or
Hub service to remove; deleting retained data is not a rollback step.

## Remaining work outside this increment

JupyterHub, authentication and multi-user isolation, notebook editing/upload,
direct Spark sessions, model training/autolog, Registry/Serving, activating a
restored MLflow store, cross-host migration and production acceptance remain
unimplemented or unverified. The separate MLflow runbook covers the verified
inactive local clone restore.
The historical GitHub/PR gates in the original WP are not local execution gates
under ADR-0034.

See the [Reader runbook](../query_read/README.md) and
[MLflow runbook](../mlflow/README.md) for the reused boundaries.
