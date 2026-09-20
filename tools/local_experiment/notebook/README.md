# Fixed local lakehouse, training and review Notebooks

The original fixed profile passed two explicit real-kernel executions and
independent saved-evidence verification. The runner connects registered Notebook
templates to the existing Reader CLI and local MLflow evaluation experiment.
The request profile adds a bounded table preview, and the training-review profile
reads one explicitly selected saved training attempt. A separate lake-training
review profile adds saved Iceberg provenance and holdout rows. The dated
verification sections distinguish the original fixed profile from the selected-table
preview and its default compatibility run. This is a local partial implementation of
[WP-L4-NB-001](../../../docs/design/06_WorkPackages/L4/WP-L4-NB-001_JupyterHub_bootstrap.md).

The dedicated Python 3.12 environment pins nbclient 0.11.0, nbformat 5.11.1 and
ipykernel 7.3.0 in `pyproject.toml` and `uv.lock`. It does not install a global
kernelspec or start JupyterHub. Without input, the runner uses
`examples/local_lakehouse.ipynb`. With `--request-stdin`, it uses the separately
registered `examples/local_table_preview.ipynb`. With `--training-token`, it uses
`examples/local_training_review.ipynb`. With `--lake-training-token`, it uses
`examples/local_lake_training_review.ipynb`. These profiles accept no caller-provided
Notebook path or code. The new `--train-lake` profile uses the fixed
`examples/local_lake_training.ipynb` to request one owned training worker.

## Trigger fixed Iceberg training

```sh
rtk proxy make local-notebook-lake-training
```

The target uses the existing Notebook environment and `runner.py --train-lake`.
The kernel stays in that environment; its worker uses the existing MLflow
environment, including NumPy for workload normalization. Neither environment is
synchronized or installed by this target.
Its registered template has exactly three code cells and a 600-second execution
deadline. It accepts no caller Notebook code, table, model or training parameters.
Before the pipeline starts, an ownership handshake binds the outer runner to
one worker requested by the kernel. That worker invokes the unchanged
[fixed Iceberg pipeline](../lake_training/README.md) once.

The worker creates 64 fixed Iceberg rows, reads them through the owned Reader
and cleans up its table. It then stops the owned MLflow server if running,
retains a complete cold backup, fits and records one run, and restores the
initial server state. The Notebook loads that same completed attempt through
the saved-review helper, displaying provenance, three groups of metrics,
16 holdout rows and residual SVG without an additional HTTP read.

Cancellation allows up to 600 seconds for owned worker cleanup before closing
the kernel, separately from the 600-second execution deadline. Uncertain closure
stays unknown and retains evidence; there is no
automatic retry. Private pipeline intent, owner, launch and status receipts bind
the worker before it may start the pipeline. The final result records pipeline
cleanup, source/training tokens, Reader evidence and training identity;
`training-review.json` retains the displayed result.
This is a Notebook entry point to an owned worker, not a fit
inside the kernel or autolog. No dependency, service or LLM is added.
Keep any newly completed run, artifact and pre-write cold backup even if later
Notebook display fails or use stops. Deleting them is not a rollback step.

### 2026-09-20 worker-training acceptance

Notebook `a074314c6580479ebeb45d0b33802a62` passed first in 86.739 seconds,
with three completed cells and all cleanup flags true. Kernel PID 38498 exited.
Source `07c524979e4341649de1297cc51a3c05` and Reader evidence
`3ad5d0b8df6b7d1add5da259073c7f75` produced training attempt
`6df9b9e5a40b4035b7865f8435fdd291`, experiment 4 and run
`1ee733efdf1e428da531f522f2d42c70`.

Fourteen retention checks preserved 37 sources, 852 files, all old rows across
59 MLflow tables, the previous 18 runs and artifacts, and 535 AgentHouse rows.
One run, 24 parameters, six tags, nine metric points/latest metrics and one
artifact were added: totals are 19 runs / 1,347 points / 19 artifacts. The same
store returned to a healthy server. All 33 new focused tests passed in
0.602 seconds (15 client and 18 runner tests). Full regression passed 80 suites /
2,870 tests: 2,869 passed and one existing optional test skipped, in 70.443 seconds.
Independent saved verification passed first: 81 checks across 114 inputs in
0.118 seconds. Nine metrics and 16 predictions/residuals matched; all 11 workload
keys other than the new lake_input provenance reproduced the earlier fixed fit.
The checks bound the before/after retention-guard receipts rather than repeating
a live file audit, and added no API, SDK, fit, Reader, kernel or product import.

Kernel, worker, Reader and tracker processes were gone. The same store returned
to healthy server PID 39313. Five non-overlapping output areas (Notebook, source,
Reader, ML attempt and artifact) total 80 files / 12,996,025 bytes (13,164,544
allocated). This includes the source's 42-file / 2,638,481-byte cold backup;
do not add that backup again. Evidence is retained under
`tmp/local-experiment/notebook-train-checks/6155edd59ccc4214b58c3a6d0156d5b0/`.

## Review saved Iceberg training

Select the saved **ML training attempt token**, rather than the Iceberg source
attempt token or MLflow run ID:

```sh
rtk proxy env SHIROKUMA_LAKE_TRAINING_TOKEN=3da0a631a0e549f29aac96f9998da5bb make local-notebook-lake-training-review
```

The target passes the explicit 32-character lowercase hexadecimal token to
`runner.py --lake-training-token` in the existing Notebook environment. It uses
the separately registered `examples/local_lake_training_review.ipynb`, with
three code cells and a 60-second execution deadline. The owned loopback MLflow
server must already be running. There is no dependency sync, caller-supplied
Notebook path, code or endpoint.

The review binds the completed private training receipt, workload and safe JSON
artifact. Its typed `lake_input` supplies the 64 rows, explicit 48/16 split and
provenance needed to check coefficients, predictions and nine metrics. Two
fixed GETs to the owned experiment 4 and selected run check FINISHED status,
identity, parameters, tags and latest metrics. The removed source fixture,
original Reader and live lake pipeline are not required. This performs no new
Reader execution, fit, MLflow write or LLM call.

The fixed output displays table UUID, snapshot, schema hash, rows hash and Reader
evidence token; three partitions' metrics; and 16 holdout rows with actual,
predicted, baseline and residual values. A residual SVG follows the saved holdout
order, not time. Residual means actual minus prediction. The original synthetic
training template and public result shape remain separate and unchanged.
Evidence is private under `tmp/local-experiment/notebook-runs/<token>/`; this
Notebook execution token differs from the selected ML training attempt token.

### 2026-09-20 Iceberg review acceptance

Review `2b8906fbc5ee4e258b64accd864d1622` passed on its first make invocation
in 1.108 seconds. Three cells reviewed training attempt
`3da0a631a0e549f29aac96f9998da5bb`, experiment 4 and run
`ae5e1ff478ae4a3493c437009be2d6b6`, with profile `lake-training-review-v1`.
The owned kernel PID 29776 exited and its connection file was removed. Both
cleanup flags were true; no Reader ran and its evidence token was null.
`training-intent.json` and `training-review.json` bind the selected saved result,
which the parent revalidates after kernel shutdown.

Five retention checks preserved 12 sources, 842 protected files, all 59 MLflow
tables / 18 runs / 1,338 metric points / 18 artifacts, 535 AgentHouse rows and
the running server identity at PID 23897. Client tests passed 45 cases in
0.550 seconds (18 new); runner tests passed 25 in 0.112 seconds (11 new). Full
local regression passed 77 suites / 2,820 tests: 2,819 passed and one existing
optional test skipped, in 67.325 seconds. Independent saved-evidence verification
passed first: 58 checks across 871 inputs in 0.351 seconds. It matched 256 typed
numeric values, the 48/16 split, normal equations, nine metrics, 16 predictions
and residuals, and the complete public result. Saved HTML has two tables and
21 rows (two headers, three metric rows and 16 holdout rows); the SVG has 16
points. No external reference or executable element was found. Three completed
cells and kernel cleanup also matched. These checks added no API, SDK, fit,
Reader or product import. The two GETs follow from the frozen successful code
path; they are not a captured network measurement.

The new run contains ten files / 44,560 bytes (73,728 allocated). Data files use
mode 600 and parent directories mode 700. Library defaults are recorded
separately: jupyter-runtime mode 01700 and the static IPython startup/README
mode 0644 inside a private mode-700 parent. Evidence is under
`tmp/local-experiment/notebook-lake-training-checks/daac544b51dd41c8a4ea4480565dd9ca/`.

Pixel QA remains unverified; the earlier Browser file-URL policy block is not
bypassed. These views describe the fixed saved experiment, not general model
quality, prediction on new input, Notebook training, autolog or JupyterHub
integration.

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

## Review saved training

Select the completed **training attempt token**, not the MLflow run ID. For the
saved 2026-09-20 training attempt:

```sh
rtk proxy env SHIROKUMA_TRAINING_TOKEN=e65bbc1bd3dc4ca3843ca5c502a53ff8 make local-notebook-training-review
rtk make test-local-notebook-training
```

The target passes the explicit 32-character lowercase hexadecimal token to
`runner.py --training-token` in the existing dedicated Notebook environment.
It does not synchronize dependencies. MLflow must already be running at its
owned loopback address. The registered template has three code cells; it neither
builds nor invokes the Go Reader and performs no new training, LLM call,
MLflow write, model download or autologging. The default and table-preview
entry points keep their existing behavior.

The review first validates the selected private completed receipt, store marker,
workload hash and byte-identical safe JSON artifact. It checks the fixed dataset
and split fingerprints, replays coefficients and holdout predictions, and
recomputes the training, holdout and training-mean baseline metrics. Only two
fixed MLflow GETs follow: the owned training experiment and its selected run.
They must match the saved identity, FINISHED state, parameters, tags and nine
latest metrics. There is no automatic retry or alternate endpoint. This is not
a fresh metric-history readback or a transaction spanning local files and HTTP.

The Notebook displays all three partitions' MAE/RMSE/R², the 32 held-out rows'
actual/predicted/baseline/residual values, and a residual SVG. Residual means
actual minus prediction. The x-axis follows the fixed holdout order; it is not
a time series. Row indices identify the generated dataset. These views explain
the saved synthetic result and do not establish general model quality.

`training-intent.json` and the public-shaped `training-review.json` remain
private under `tmp/local-experiment/notebook-runs/<token>/`, alongside
`inputs.json`, `executed.ipynb`, kernel evidence and `result.json`. Here the run
directory token identifies this review; `training_token` identifies the earlier
training attempt. Public-shaped JSON includes model coefficients and holdout
values; it is not automatically published. The template retains empty outputs.

The parent rechecks the saved review against the private receipt/workload/artifact
after the kernel exits, and seals the review source and template hashes before
and after execution. A different run, changed metrics, unsafe files, unexpected
Reader evidence, failure to save output or unknown kernel cleanup cannot become
a passed result. The kernel receives only the explicit training token; inherited
Reader/request/training variables cannot select its inputs. No Reader exists
in this profile, so a true Reader-cleanup field does not imply a new query drain.

The three code cells share a 60-second execution deadline; kernel startup and
shutdown are separate. The default and Reader-preview execution deadline remains
900 seconds. Stop further review invocations to disable this entry point.
Cancellation follows the existing owned-kernel shutdown path and retains uncertain results. Keep the
review outputs, original training run and backups; a read-only review requires
no store rollback. This increment adds no dependency, resident service, global
kernelspec, Hub integration, Notebook-triggered training or model serving.
### 2026-09-20 acceptance

Review `351ceabb39344dc7a678022579727b0f` passed on its first actual kernel
execution in **1.041 seconds**, completing three code cells. It selected training
attempt `e65bbc1bd3dc4ca3843ca5c502a53ff8` and the existing experiment 3 run
`0c2a0a34683545d3acd580642a9bb30d`. The saved Notebook contains all three
metric partitions, 32 held-out rows and the residual SVG.

Kernel PID 9124 exited and its connection file was removed. Both cleanup fields
were true, with no Reader and a null Reader evidence token. No Go build, Reader,
new fit, LLM call, MLflow write or dependency installation occurred. The before
and after guards matched eight sources, 653 retained files and MLflow's
59 tables / 17 runs / 1329 metric points / 17 artifacts and indexes.

The 41 new tests (27 client and 14 runner), 53 existing Notebook regression
tests, and full local regression passed. The full result was 75 suites /
2671 tests: 2670 passed and one existing optional skip in 62.182 seconds.
Saved display structure passed: two HTML tables with 37 rows (two headers,
three metric partitions and 32 holdout rows), plus 32 SVG points, with no external
references or executable elements. On-screen appearance was not inspected.
A standalone HTML copy is private verification evidence, separate from the
Notebook run directory. Independent saved verification passed 23 meaningful
checks across 699 bound inputs: all saved predictions and the public projection,
nine HTML metric cells, 32 held-out rows, SVG coordinates/tooltips/scale,
retained state and kernel cleanup matched. It made no additional API, SDK, fit,
kernel or product-module call. The two GETs describe the frozen successful code
path; they were not measured by capturing network traffic.

The new review contains 10 files, 48,388 bytes (77,824 allocated). Data files
are 0600 under private 0700 parents. The library-created exceptions are the
01700 Jupyter runtime directory and static IPython startup README at 0644
inside private parents.
Evidence is under
`tmp/local-experiment/notebook-training-checks/dadcbad093704ff8862a9cca46b51209/`.

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
stay under `tmp/local-experiment/notebook-runs/<token>/`. The default and preview outputs include
`executed.ipynb`, `result.json`, Reader stdout/intent/status and
`evaluations.json`. Training-review and worker-training outputs are described above.
They are not copied back into the template.
The Notebook result may contain the selected query's typed table rows;
credentials, raw driver errors and connection secrets are not public output.

For the default and table-preview Reader profiles, success requires the Reader
receipt to prove its query drain and cleanup and the
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
direct Spark sessions, arbitrary or kernel-hosted model training/autolog,
Registry/Serving, activating a
restored MLflow store, cross-host migration and production acceptance remain
unimplemented or unverified. The separate MLflow runbook covers the verified
inactive local clone restore.
The historical GitHub/PR gates in the original WP are not local execution gates
under ADR-0034.

See the [Reader runbook](../query_read/README.md) and
[MLflow runbook](../mlflow/README.md) for the reused boundaries.
