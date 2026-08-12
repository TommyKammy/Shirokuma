---
doc_id: ADR-0029
title: Authorize exact Trino 483 Maven build-plugin remediation
status: accepted
created: 2026-08-07
updated: 2026-08-13
version: "1.4.0"
area: architecture
tags: [adr, trino, maven, supply-chain, arm64]
---

# ADR-0029: Authorize exact Trino 483 Maven build-plugin remediation

## Context

ADR-0028 kept the Trino dependency publisher blocked after reviewed-main run
`30693677356` found three nonwaivable Maven High findings. PR #142 added a
fail-closed feasibility workflow and retained evidence contract for one exact
candidate. Its final reviewed head `864da7ea3eb93c976691836630cb91775d5074af`
ran successfully as
[`31072144404`](https://github.com/TommyKammy/Shirokuma/actions/runs/31072144404).

Artifact `8956062532` retained a 5,036-file Maven repository, online and
network-none replay logs, native linux/arm64 toolchain records, and hash-bound
policy/source inputs. An independent repository verifier audit passed against
reviewed commit `864da7ea3eb93c976691836630cb91775d5074af` and workflow
execution commit `f404219ce0a189336dbcc2a48fd881064edc3780`.

Risk owner `TommyKammy` approved the exact candidate in Issue #63 comment
[`5210182460`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5210182460)
at `2026-08-07T00:08:48Z`.

After the first authorized attempt failed closed before publication and PR #144
contained the JGit `user.home` write, the risk owner reauthorized exactly one
second attempt in Issue #63 comment
[`5221869732`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5221869732)
at `2026-08-07T20:49:55Z`. The reauthorization retains the same candidate,
expiry, evidence gates, and downstream prohibitions.

Shirokuma is the risk owner's personal experimental project and has no
available independent approver. The owner therefore recorded one approval-path
exception for PR #145 in Issue #63 comment
[`5262105662`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5262105662)
at `2026-08-12T03:59:12Z`. That exception was limited to the now-consumed
second attempt and is historical; it grants no authority to a later run.

PR #145 was squash-merged to `main` as
`bbd258739c59398da8c721480c48eab82d99441b`. The resulting second authorized
attempt ran as GitHub Actions run
[`31577976760`](https://github.com/TommyKammy/Shirokuma/actions/runs/31577976760),
attempt `1`. After the first network-none build succeeded, the workflow
extracted the verified Maven snapshot inside the source checkout. The retained
clean-source check correctly detected the resulting untracked files and failed
closed. The `publish` job was skipped, zero artifacts were retained, and the
second attempt was consumed. It must not be rerun.

The risk owner then authorized exactly one third attempt and its owner-only
approval path in Issue #63 comment
[`5264706435`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5264706435)
at `2026-08-12T09:10:49Z`. That historical authorization was bound to the same
candidate and expiry, predecessor
`bbd258739c59398da8c721480c48eab82d99441b`, GitHub Actions attempt `1`, and PR
#146 final-head gates. It granted no downstream authority and is consumed.

PR #146 was squash-merged as
`b1e58117cff9f9f1441176617dbdb2e4e0e8685f`. Its reviewed-main run
[`31590750849`](https://github.com/TommyKammy/Shirokuma/actions/runs/31590750849),
attempt `1`, failed closed before publication with five unsuppressed Bun High
findings and retained zero artifacts. The sequence-3 attempt is consumed and
must not be rerun. Issue #63 comment
[`5266462504`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5266462504)
at `2026-08-12T11:54:32Z` authorized exactly one sequence-4 attempt after
predecessor `b1e58117cff9f9f1441176617dbdb2e4e0e8685f`, bound to PR #147 and
GitHub Actions attempt `1`. PR #147 was squash-merged as
`e6eb99d0e79b4a85aa7670ed75f07e4bc2d5b823`. Reviewed-main run
[`31605249586`](https://github.com/TommyKammy/Shirokuma/actions/runs/31605249586),
attempt `1`, completed both closed Maven reconstructions, both network-none
builds, the raw rootfs inventory, and the Maven and Bun SBOM/scans. It failed
closed before publication because Trivy 0.72.0 returned React Router
`FixedVersion="7.18.2, 8.3.0"` while the reviewed contract required
`"8.3.0"`. The raw report retained one High finding, the exact
OpenVEX-adjusted report retained High=0/Critical=0, and the publish job was
skipped. Diagnostic artifact
`trino-bun-vulnerability-diagnostics-31605249586-1` retained only the exact
four diagnostic files. The sequence-4 attempt is consumed and must not be
rerun.

Issue #63 comment
[`5268936554`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5268936554)
at `2026-08-12T15:32:31Z` authorizes exactly one sequence-5 attempt after
predecessor `e6eb99d0e79b4a85aa7670ed75f07e4bc2d5b823`, bound to PR #148 and
GitHub Actions attempt `1`. It permits only the exact fixed-version metadata
repair and retains the same candidate, expiry, fail-closed controls, and empty
downstream-authority set.

## Decision

Authorize only this exact sequential third Trino source patch:

- source repository: `https://github.com/trinodb/trino`
- tag: `483`
- commit: `50b0b50b75abd47f830b7805ee1b51716eb4065e`
- tree: `3b5414292a614b12393bb4605ea2d4c588a5b8ee`
- patch: `bootstrap/trino/v483/patches/0003-shirokuma-maven-build-plugin-closure.patch`
- patch SHA-256: `731e76f296a725d34ea9e226a1815782168cae3890424e69f76a05530afc15be`
- permitted path: root `pom.xml` only
- post-ADR-0027 preimage SHA-256:
  `8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1`
- authorized postimage SHA-256:
  `871c6b21cf9fc70c455d21b64d24dd4501a8b5943242418edc2b2f5cfe14fab8`
- apply arguments: `--unidiff-zero --whitespace=error-all`

The patch may:

- add `org.apache.velocity:velocity-engine-core:2.4.1` only to the exact
  `revapi-maven-plugin` declaration;
- add `org.codehaus.plexus:plexus-utils:4.0.3` only to the 12 exact
  build-plugin declarations fixed by the reviewed patch;
- remove the optional
  `io.github.gitflow-incremental-builder:gitflow-incremental-builder:4.6.0`
  declaration;
- consume only the two reviewed hash-bound Maven SCM metadata postimages
  already retained by ADR-0028.

The selected reactor remains
`:trino-server,:trino-server-core,:trino-server-main,:trino-hdfs,:trino-iceberg`.
The publisher must preserve native linux/arm64 execution, read-only source and
policy mounts, network-none independent reconstruction, closure-complete SBOM,
raw vulnerability evidence, and High=0/Critical=0 without waiver.

The two online reconstructions must replace the two authorized Maven SCM POMs
with their exact hardened postimages and remove the closed vulnerable-input
inventory before sealing or packaging either repository. Every online
`clean install` and network-none `clean package` must be followed by complete
candidate postimage revalidation before its repository or server archive can
be consumed.
The replacement POMs and checksum sidecars must retain their Maven-resolvable
Central markers so the network-none build can consume the hardened dependency
graphs. The packager separately records the dedicated
`shirokuma-scm-remediation` provenance only in the closed manifest and accepts
that origin only for the four exact reviewed postimages during offline
verification.
Pruning each blocked vulnerable JAR must also remove every Maven checksum
sidecar and any matching `_remote.repositories` entries before packaging.

Authorization expires at `2026-08-21T22:43:36Z`, has no automatic renewal,
and requires either a reviewer different from implementation author `Codex`
and the owner before merge, or the exact PR #148 owner final-head attestation
defined below.

The standard independent-review path queries the REST pull-request reviews
endpoint with `per_page=100` and a 32 MiB response bound per page through a
terminal short page, bounded to 10 pages with an accepted maximum of 999
reviews. Every review ID must be a unique positive integer, and two complete
ordered scans of decision-relevant review state must match. A full tenth page
yields 1,000 reviews without proving exhaustion and therefore fails closed; an
unstable snapshot, a malformed or duplicate ID, or a missing or malformed page
also fails closed. A qualifying human
`APPROVED` review must target the exact final pull-request head and have a valid
UTC `submitted_at` strictly before the pull request's `merged_at`; an approval
submitted at or after merge cannot authorize publication.

### PR #148 owner-only approval exception

The standard independent-review path remains accepted and is evaluated first.
When it succeeds, approval is complete without querying owner-exception issue
comments, final-head CI, or review threads. Only when the standard path has no
qualifying review does the publisher lazily query the exception data below. As
a narrow alternative for only repository `TommyKammy/Shirokuma`, PR #148, and
this fifth publication attempt, the publisher may accept one canonical
top-level issue comment when every condition below is true:

- the REST issue-comments endpoint is queried with a page size of 100 and
  followed through every page until exhaustion, bounded to at most 10 pages
  and a 32 MiB response per page; the accepted maximum is 999 comments, every
  comment ID must be a unique positive integer in strictly increasing order
  across pages, and two complete scans of the decision-relevant comment fields
  must be identical; a full tenth page yields 1,000 comments without proving
  exhaustion and therefore fails closed, as does any missing, malformed,
  reordered, duplicated, or unstable page;
- the comment author login is exactly `TommyKammy`, the GitHub account type is
  `User`, and `author_association` is `OWNER`;
- `.github/workflows/ci.yml`, `.github/workflows/security.yml`,
  `.github/workflows/trino-maven-remediation-feasibility.yml`, and
  `.github/workflows/trino-maven-dependencies.yml` each have a REST workflow-run
  result with `status=completed` and `conclusion=success` for PR #148 and the
  exact attested final head; the workflow-runs endpoint is read with
  `per_page=100` and a 4 MiB response bound per page through a terminal short
  page, bounded to 10 pages and an accepted maximum of 999 runs; every run ID
  is a unique positive integer,
  `total_count` remains stable and equals the collected run count, and two
  complete ordered scans of run identity, path, head, event, status, conclusion,
  timestamps, repository identities, and PR binding are identical; multiple
  qualifying successful pre-attestation runs for one required path are ordered
  by `updated_at`, then `created_at`, then run ID, while failed or incomplete
  runs cannot satisfy the gate; a full tenth page does not prove
  exhaustion and fails closed;
- a GraphQL cursor query reads `reviewThreads` in pages of 100 until
  `hasNextPage=false`, bounded to at most 10 pages and 1,000 threads; every
  page must report the exact repository, pull-request number, and attested
  `headRefOid`; `totalCount` must remain stable and equal the number of unique
  thread IDs collected; two complete ordered scans of thread ID, resolved
  state, and outdated state must match; and all returned pages together must
  contain zero current, non-outdated threads before attestation, whether
  resolved or unresolved; only already-outdated threads are permitted; and
- the canonical comment is created after those final-head conditions pass and
  before merge, with the exact body:

```text
Owner final-head attestation for PR #148

Decision: APPROVED
Final head: <exact final 40-character PR head SHA>
Exception: https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5268936554
```

Only `APPROVED` and `REVOKED` are valid decisions. The exact matching owner
decision with the latest `updated_at` timestamp governs; comment ID is not an
ordering signal. A later matching `REVOKED` comment invalidates the attestation,
and two or more matching decisions tied at the latest `updated_at` fail closed
as ambiguous. A bot or different login, account type, or association cannot
satisfy the exception; an owner comment with a different PR, head, body, or
post-merge timestamp fails closed. After the workflow-run and review-thread API
gates complete, the publisher repeats the same bounded two-pass owner-comment
query and requires the governing decision receipt to match the first selection
exactly; an edited approval or later `REVOKED` therefore fails closed.

The zero-current-thread result is evidence for the attested final head, not a
permission to resolve a thread later. A resolved but non-outdated thread still
fails closed. Making a thread outdated requires changing the PR head, which
invalidates the exact-head attestation and its final-head CI evidence; the new
head must pass the exception gates and receive a new attestation. Consequently,
a post-attestation transition to outdated cannot retroactively satisfy the
thread gate. A malformed page, missing or repeated cursor, cursor cycle,
repository, pull-request, `headRefOid`, or `totalCount` drift between pages,
duplicate thread IDs, a mismatch between the two complete ordered scans, or
failure to prove exhaustion before reaching either pagination bound fails
closed. On this owner-exception path only, the main publisher must repeat the
exact merged-PR, attested-head CI, review-thread, and post-gate owner-decision
queries at its write-capable boundary and again immediately before registry
authentication.

This exception grants only the approval path for PR #148's fifth publication
attempt. It does not remove the independent-review requirement from any other
pull request, later attempt, or repository policy and is not a permanent
relaxation. Failure consumes the attempt, rerun remains forbidden, and the
empty downstream-authority set is closed-world: it grants no
dependency-evidence admission, image publication, resident admission,
Flux/runtime reconciliation, credentials, public exposure, production use, or
Issue #63 closure.

## Consequences

The first authorization was bound to the protected-main transition after
`ffbb4997420d4b66abf04ec4dfaa579aff2ce965` and GitHub Actions attempt `1`. It
executed as GitHub Actions run
[`31163679280`](https://github.com/TommyKammy/Shirokuma/actions/runs/31163679280)
at `main` commit `27a313fca0aa080db8bd8f1d67744c68b1b0ab4f`. Both fresh
online dependency reconstructions and the first network-none Maven build
completed, but the post-build candidate check failed closed with
`CANDIDATE_APPLY: candidate created untracked source files`. JGit had written
`.config/jgit/config` under the source checkout because the container's
`HOME=/tmp/maven-home` did not change the JVM `user.home` property. No
dependency artifact was published. The single attempt is consumed: this
corrective change sets `MAVEN_OPTS=-Duser.home=/tmp/maven-home` on every Maven
container invocation while retaining the untracked-file rejection, but it does
not authorize another publisher run. That first attempt is historical and
consumed.

Issue #63 comment `5221869732` authorized a second attempt bound to the
protected-main transition after
`6f557abc42713629510090db10d03630043364d7` and GitHub Actions attempt `1`. PR
#145 was squash-merged as
`bbd258739c59398da8c721480c48eab82d99441b`, and the attempt executed as run
[`31577976760`](https://github.com/TommyKammy/Shirokuma/actions/runs/31577976760).
The first network-none build completed, but the workflow had extracted the
verified Maven snapshot under the source checkout as `.m2/repository`. The
unchanged clean-source postcondition detected those untracked files and failed
closed. The `publish` job was skipped, zero artifacts were retained, and the
second attempt was consumed. Rerunning that run is forbidden. The sequence-2
authorization and PR #145 owner-only exception are historical and inactive.

PR #146 repairs that workflow-owned path error without weakening the
postcondition. Each verified Maven snapshot is extracted to a fresh
`${RUNNER_TEMP}/trino-offline-maven-repository-<suffix>` directory, mounted in
the network-disabled Maven container as read-only `/m2:ro`, and selected with
`-Dmaven.repo.local=/m2`. Maven Resolver lock files are redirected to
`/tmp/maven-locks`, outside both the source checkout and the read-only
repository. The two fresh offline builds use `clean package`, produce
byte-identical server archives, and must leave the source checkout clean. The
untracked-source rejection remains mandatory; no `.gitignore`, cleanup, or
allowlist exception is permitted.

Issue #63 comment `5268936554` is the active sequence-5 decision. It authorizes
only one fifth reviewed-main attempt, after predecessor
`e6eb99d0e79b4a85aa7670ed75f07e4bc2d5b823`, with GitHub Actions attempt `1`.
The lifecycle is `dependency_snapshot_publication_pending`, and the contract,
publication, and admission permission records are true only for that exact
transition. Pull requests remain validation-only. PR #148 must pass the exact
final-head CI and review-thread gates and satisfy either a standard current
independent `APPROVED` review or the PR #148 owner-only final-head attestation
contract above. A different predecessor, a rerun, candidate drift, expiry, or
failure before completing the closed publication-evidence boundary consumes or
rejects this fifth attempt and returns publication to explicit reauthorization
pending.

The write-capable publish job must independently repeat the attempt check and
query the exact merged pull request before registry authentication. At least
one current `APPROVED` review from a human GitHub user different from risk
owner `TommyKammy` and implementation author `Codex` remains the standard
path. A qualifying standard review short-circuits evaluation without querying
owner comments or the exception-only CI and thread APIs. The only alternative
is the exact PR #148 owner attestation above; an ordinary CODEOWNERS approval
by the risk owner cannot satisfy it. The qualifying review `commit_id`, or
owner attestation `Final head`, must equal the exact final pull-request
`head.sha`; an approval of an earlier revision cannot authorize publication.
The alternative also requires all exact final-head CI checks successful and
zero current non-outdated review threads, including resolved threads.
After those API gates return, the publisher repeats the current-time
authorization and one-attempt check immediately before registry authentication
so expiry during the bounded API queries cannot reach `oras login` or a write.

Dependency artifact presence, dependency evidence admission, Trino image
publication, resident-image admission, Flux runtime reconciliation, public
Service or Ingress, production use, and Issue #63 closure remain false until
their separate evidence gates pass.

No vulnerability is waived, ignored, suppressed, or reclassified by this
decision. A changed patch, source, preimage, postimage, plugin set, Maven
metadata, reactor, toolchain, or authorization time fails closed.

## Verification

- Verify artifact `8956062532` metadata and digest
  `sha256:46c1ea610e0cb24df43b93cd954c05222573562f9b1677d3d8f7fd0ba65814da`.
- Independently audit the retained 242,729,683-byte archive
  `sha256:b12debf4e760e3042fe34bd9946c2ed96d0ead4eb7959d8491fe63f3240c208a`.
- Require closed manifest SHA-256
  `6667d7b8275c0b24ec4f8ec7070173a57a888ab425d8697e0b261f49fc347ea4`.
- Apply all three patches sequentially and verify each boundary's exact
  preimage and postimage before continuing.
- Run `python3 -m unittest -v tests.test_trino_dependency_publisher`.
- Run `python3 -m unittest -v tests.test_trino_maven_feasibility`.
- Run `python3 scripts/verify_trino_dependency_publisher.py audit --root .`.
- Run `make verify-trino-bootstrap` and `make verify`.

## Rollback

Set all three publication-permission records false, restore lifecycle state
`source_remediation_authorization_pending`, remove the third patch from the
publisher and permitted inventory, and preserve every raw feasibility record.
Do not consume an artifact produced after rollback or authorization expiry.

## Related

- [[ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[ADR-0025_Keep_Trino_483_Maven_closure_blocked_pending_source_remediation]]
- [[ADR-0026_Authorize_bounded_Parquet_Jackson_1_17_1_source_remediation]]
- [[ADR-0027_Authorize_bounded_Trino_483_Iceberg_only_Maven_closure]]
- [[ADR-0028_Keep_Trino_483_publisher_blocked_for_refreshed_Maven_findings]]
- [[../04_Development/049_Supply_Chain_Security]]
- GitHub Issue #63
