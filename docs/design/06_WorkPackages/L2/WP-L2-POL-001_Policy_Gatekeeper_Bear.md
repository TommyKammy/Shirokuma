---
project: Shirokuma
doc_id: "WP-L2-POL-001"
title: "WP-L2-POL-001 Policy Gatekeeper Bear"
status: local-partial
created: 2026-07-05
updated: 2026-10-04
version: "0.47"
area: "workpackage"
tags: [shirokuma, workpackage, l2, policy]
---

# WP-L2-POL-001 Policy Gatekeeper Bear

## 2026-10-04: non-JSON Kubernetes numeric constants (offline)

Three test-owned scoped API replies with nested or top-level `NaN`/`Infinity`
passed the transport parser before repair. The reader now rejects Python's
non-standard JSON constants before baseline interpretation; canonical numeric
JSON remains valid. All three negatives failed before repair; focused policy
observer/native tests passed (72). The dedicated Colima profile was read-only
observed stopped. No live admission, pending-request replay, retained volume or
backup mutation. Fake replies cannot authenticate the API, prove atomic
inventory, clear strict admission or establish operator acceptance. Roll back
only the parser/test if needed, retaining saved intents. Original Vault sync
pending outside this checkout.

## 2026-10-04: explicit null is not a missing Pod (offline)

The scoped kubectl JSON reader previously returned `None` for both an empty
`--ignore-not-found` response and a literal JSON `null`. Two fake transport
replies reproduced the ambiguity before repair. Explicit null now fails the
reader; empty/whitespace-only output still denotes absence. The focused
observer suite passed (54 tests). Dedicated Colima was observed stopped; no
live admission, retained request, volume or backup changed. This offline
transport check cannot authenticate the API, prove atomic inventory or clear
strict admission. Roll back only the reader/test if needed; preserve pending
intents without replay. Direct original Vault sync remains pending outside
this checkout.

## 2026-10-04: duplicate raw Kubernetes JSON claims (offline)

The scoped policy reader previously let JSON parsing silently retain the last
of two identical keys, including nested identity keys and list members. Three
test-owned replies reproduced acceptance before repair. All scoped policy API
JSON now rejects duplicate keys before baseline or dry-run interpretation;
canonical replies remain valid. The focused observer suite passed (53 tests).
The dedicated Colima profile was observed stopped; no live API/admission,
retained request, volume or backup changed. Fake transport data cannot
authenticate the API, prove atomic inventory, clear strict admission or
establish operator acceptance. Roll back the reader/test only if needed;
preserve pending requests without replay. Direct original Vault sync remains
pending outside this checkout.

## 2026-10-04: namespace selector label aliases (offline)

Three test-owned Namespace replies with a valid canonical selector label and
conflicting case-variant keys became ready before repair. The fixed baseline
now refuses these aliases before any dry-run. The negative failed in all three
cases before repair; the observer suite passed (52 tests) afterward. The
dedicated Colima profile was observed stopped; no live admission, retained
request, volume or backup changed. Fake replies cannot authenticate the API,
prove atomic inventory, clear strict admission or establish acceptance. Roll
back only this guard/test if necessary; preserve unknown requests without
replay. Direct original Vault synchronization is pending outside this checkout.

## 2026-10-04: policy resource envelope aliases (offline)

Six conflicting case-variant top-level fields on test-owned policy resources
were accepted as ready before repair. The fixed-candidate baseline now refuses
these aliases before any dry-run. The negative failed in all six cases before
repair; the observer suite passed (51 tests) afterward. Dedicated Colima was
observed stopped. No live admission, retained requests, volumes or backups
changed. These fixtures cannot authenticate the API, prove atomic inventory,
clear strict admission or establish operator acceptance. Roll back only this
guard/test if needed; retain pending intents without replay. Original Vault
handoff sync remains pending outside the approved checkout.

## 2026-10-04: namespace envelope aliases (offline)

Separately from policy-resource metadata, four fake Namespace replies with
conflicting case-variant top-level `Kind`, `KIND`, `ApiVersion` or `Metadata`
were accepted before repair. The observer now rejects these before dry-run;
the four-case negative failed first and the focused 50-test suite passed after
repair. Dedicated Colima remains stopped; no live API/admission or retained
data/backup changed. Fake replies do not authenticate the API, prove atomic
inventory, clear strict admission or establish operator acceptance. Roll back
only this Namespace guard/test if necessary; retain pending intents without
replay. Original Vault sync pending outside the approved checkout.

## 2026-10-04: policy resource identity aliases (offline)

Six test-owned policy resource metadata aliases (`Name`, `Uid`, `Generation`,
`Labels`, `Annotations`, `DeletionTimestamp`) were accepted alongside canonical
identity/ownership fields before repair. The fixed-candidate baseline now
rejects conflicting case-variant identity fields before dry-run; the six-case
negative failed before repair and observer suite (49 tests) passed afterward.
Dedicated Colima was observed stopped. No live API/admission, retained requests,
volumes or backups changed. This fixture cannot authenticate the API or prove
atomic inventory, strict admission or operator acceptance. Roll back only this
guard/test if necessary; preserve pending intents without replay. Original Vault
sync remains pending outside the approved checkout.

## 2026-10-04: namespace identity alias baseline (offline)

Four fake Namespace replies with canonical identity plus conflicting `Name`,
`Uid`, `Labels` or `DeletionTimestamp` metadata aliases incorrectly became
ready. The negative reproduced four failures before repair. The fixed-candidate
observer now rejects these aliases before dry-run; focused observer 48 tests
and native policy 17 tests passed. The dedicated Colima profile was observed
stopped; no live API/admission, existing request, volume or backup was changed.
This does not authenticate a namespace inventory or establish admission or
operator acceptance. Roll back only the alias guard/test if needed, preserving
pending intents without replay. Original Vault synchronization pending outside
the approved checkout.

## 2026-10-04: conflicting LimitRange list envelope aliases (offline)

Five test-owned empty canonical lists with conflicting case-variant `Items`,
`Metadata`, `Kind` or `ApiVersion` passed the fixed baseline before repair.
The observer now refuses these aliases before any dry-run. Focused observer
47 tests and native policy 17 tests passed. The dedicated Colima profile was
observed stopped; no live admission, existing request, volume or backup was
modified. This fake-list consistency check does not authenticate the API,
establish atomic inventory or grant operator acceptance. Roll back only the
alias guard and test if needed; retain pending requests without replay.
Original Vault sync pending outside the approved checkout.

## 2026-10-04: case-variant LimitRange pagination (offline)

Five test-owned empty lists with case-variant `Continue` or
`RemainingItemCount` metadata (alone or alongside canonical keys) passed the
baseline as complete before repair. The observer now refuses pagination key
aliases before any dry-run; omitted metadata and canonical empty/zero remain
compatible. Focused observer 46 tests and native policy 17 tests passed.
Dedicated Colima was observed stopped; no live admission, existing request,
volume or backup was modified. A fake response is not an authenticated or
atomic inventory or operator acceptance. Roll back only the baseline guard
and its test if needed; retain pending requests without replay. Original Vault
sync pending outside the approved checkout.

## 2026-10-04: empty LimitRange page with remaining items (offline)

An empty list with no continuation token but `remainingItemCount: 1` is not
proof of an empty namespace inventory. The candidate baseline now accepts an
omitted count or integer zero only; nonzero, negative, boolean and null counts
fail before dry-run. Focused observer tests (46) passed. Independently checked
the GitOps doctor list decoder: its existing full-list checks already reject
nonzero remaining counts, so no GitOps code change was warranted. Dedicated
Colima was observed Stopped; no live admission, Git/API writes or retained
request/report/volume/backup changes. Fake lists neither authenticate an API
nor provide an atomic inventory or operator acceptance. Roll back only this
baseline check and regression tests if necessary; preserve pending intents.
Original Vault synchronization pending outside the approved checkout.

## 2026-10-04: incomplete LimitRange inventory refused (offline)

A test-owned first page with zero LimitRanges and a nonempty continuation
token was incorrectly treated as proof of an empty namespace inventory. The
negative failed before repair. The fixed-candidate baseline now requires an
absent or empty-string continuation token and refuses malformed metadata; no
dry-run is sent on failure. Focused observer tests (45) passed. This only
validates the local fake list contract, not an authenticated or atomic live
inventory; LimitRange policy acceptance and operator acceptance remain open.
Dedicated Colima was observed Stopped; no retained requests, volumes or backups
were changed. Roll back the baseline check/test only if needed, preserving
previous pending requests. Original Vault synchronization pending outside the
approved checkout.

## 2026-10-04: fixed-corpus progress receipt failure (offline)

The legacy 15-case fixed-corpus verifier cleared its pending intent before a
per-case completion write; a test-owned writer failure left an in-memory known
result without a pending request. The negative failed before repair. A failed
completion or terminal write now restores the affected sent case as unknown and
pending, without replay. A failed pre-send write instead has no pending request
and zero probes. Focused 14 tests and local verification passed. This cannot
guarantee durability when the writer remains broken, authenticate a live API,
or establish policy acceptance. Dedicated Colima observed Stopped; no live
dry-run, retained request/volume/backup mutation or GitHub write. Roll back the
fixed-corpus observer and tests only if required; retain previous pending
intents and evidence. Original Vault synchronization pending outside checkout.

## 2026-10-04: pre-send intent writer failure (offline)

A test-owned failing intent writer stopped the request before send (zero API
calls), but the in-memory failure receipt reported `admission=unknown` with no
pending intent. The negative failed before repair. Pre-send failures now retain
`not-observed`/no pending; post-send failures still retain unknown/pending and
never replay. Focused observer 44 tests passed. No live admission or retained
request/volume/backup change; the dedicated lab was observed Stopped. This
does not prove durable writer behavior, API authenticity or acceptance. Roll
back only the observer and test if needed; preserve existing pending intents.
Original Vault sync pending outside this checkout.

## 2026-10-04: dry-run opposite-stream bytes (offline)

The candidate observer previously treated whitespace-only stderr on a
successful Pod response, or whitespace-only stdout on a recognized policy
denial, as an empty stream. It now requires the opposite stream to be exactly
empty; test-owned mixed responses remain unknown/pending after one request,
without replay. Focused observer 43 tests and native policy 17 tests passed;
`make verify-local-experiment` passed. This does not prove API authenticity,
general mutation coverage or live admission. Dedicated Colima was read-only
observed Stopped; no live request or retained data/backup change. Roll back
only the observer and test if necessary; do not replay saved pending intents.
Original Vault synchronization is pending outside the approved checkout.

## 2026-10-04: terminal receipt failure retains dry-run intent (offline)

A fake final `progress` writer failure after one allowed dry-run left the
in-memory receipt `failed/allowed` with no pending intent. The negative failed
before repair. On failure after a sent request, restore the unknown pending
marker before writing the failure receipt; never retry the request. The focused
observer suite passed (42 tests), as did local verification. No live admission,
saved request, volume or backup was modified; dedicated Colima was observed
Stopped. This does not make an actually failed disk writer durable, authenticate
the API or establish acceptance. Roll back the observer/test only if necessary;
keep any previously saved pending intent and do not replay unknown requests.
Original Vault sync pending outside this checkout.

## 2026-10-04: typed privilege and identity defaults (offline)

Pinned the existing refusal of numeric `0` as a container `privileged: false`
default and boolean `false` as a Pod metadata generation of zero. Two isolated
allowed-response fixtures remain unknown/pending after one request, with no
replay. Focused observer suite passed (41 tests); no observer implementation
changed. Dedicated Colima was observed Stopped; no admission, retained request,
volume or backup modification. This checks local type safety, not server
authenticity, general mutation coverage or operator acceptance. Roll back only
the test if necessary and retain pending requests. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: numeric false defaults regression (offline)

Pinned nine fake allowed responses with JSON number `0` in Pod host isolation,
automount, process sharing, hostname, or container stream controls. The existing
identity-based boolean checks already reject all nine; no observer behavior
changed. Focused observer suite passed (40 tests). The original intent remains
unknown/pending in every negative and is never replayed. Dedicated Colima was
read-only observed Stopped; no live API, retained request/volume/backup change
or GitHub write. These fixtures cannot authenticate a server, establish complete
mutation coverage or operator acceptance. Roll back only the new test if needed;
retain pending and historical evidence. Original Vault sync pending outside the
approved checkout.

## 2026-10-04: explicit null dry-run status (offline)

A disposable allowed response with `status: null` passed as though the status
field were absent. The focused negative failed before repair. The response
comparator now permits an omitted status or an empty object, not explicit null;
the original request remains unknown/pending and is not replayed. The focused
observer suite passed (39 tests). Dedicated Colima was observed Stopped; no
live admission, retained request/volume/backup change or GitHub write. Fake
replies cannot authenticate the API or establish full mutation coverage or
operator acceptance. Roll back only the comparator/test if needed, retaining
pending and historical evidence. Original Vault sync pending outside checkout.

## 2026-10-04: contradictory dry-run denial streams (offline)

Two disposable forbidden policy responses carrying nonempty stdout (an empty
object or a Pod) passed as denied before repair. A recognized stderr denial now
requires empty stdout; both mixed responses remain unknown/pending and the
original request is never retried. The observer's focused 39 tests and native
policy 17 tests passed. Dedicated Colima was read-only observed Stopped; no
live admission or retained request/volume/backup mutation. Fake replies do not
authenticate the API, prove general mutation coverage or establish acceptance.
Roll back observer/test only if necessary, preserving pending requests and old
evidence. Original Vault sync pending outside this checkout.

## 2026-10-04: duplicate allowed-response keys (offline)

Four test-owned successful JSON replies repeated a top-level Pod key or a
nested container key with a conflicting first value. Python's last-key-wins
decoder previously classified each as allowed; object-pair decoding now
refuses duplicates at every depth. The single original request remains
unknown/pending, never replayed. Focused observer suite (38 tests) passed.
Dedicated Colima was observed Stopped; no live admission, retained request,
volume or backup mutation. JSON ambiguity refusal does not authenticate an
API, prove general mutation coverage or establish operator acceptance. Roll
back parser/test only if needed, retaining pending requests and old evidence.
Original Vault sync pending outside checkout.

## 2026-10-04: top-level dry-run response claims (offline)

Two disposable allowed Pod responses with an unsubmitted top-level `items` or
`unexpected` claim passed before repair. The comparator now limits response
keys to the Pod envelope; both stay unknown/pending without retry. Focused
observer 37 tests and local verification passed. Dedicated Colima was observed
Stopped; no live API, retained request/report/volume/backup mutation or GitHub
write. Fake replies do not authenticate the server or establish full mutation
coverage or operator acceptance. Roll back comparator/test only if needed;
preserve pending requests and historical evidence. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: allowed dry-run object identity (offline)

Six disposable allowed responses added a persisted UID, resource version,
generation, creation time, managed fields or unknown metadata key. Each passed
before repair. The response comparator now refuses these claims, while empty
API defaults remain compatible. The pending original request is retained as
unknown, never replayed. Focused observer 36 tests and policy 17 tests passed.
Dedicated Colima was observed Stopped; no live admission, retained evidence,
volume or backup mutation, or GitHub write. Fake responses cannot authenticate
the API, prove complete mutation coverage or establish operator acceptance.
Roll back comparator/test only if required, preserving pending requests and
historical evidence. Original Vault sync remains pending outside this checkout.

## 2026-10-04: security boolean type in dry-run responses (offline)

Two disposable allowed responses substituted JSON number `1` for a submitted
Pod `runAsNonRoot: true` or container `allowPrivilegeEscalation: true`. Python
equality previously treated both as unchanged. Compare submitted security
values using canonical JSON representation so boolean and numeric types remain
distinct; keep the permitted explicit `privileged: false` default. Both
negatives now remain unknown/pending without replay; focused observer 35 tests
and policy 17 tests passed. Dedicated Colima observed Stopped; no live
admission, retained evidence/volume/backup mutation or GitHub write. Fake
responses cannot authenticate the API, prove arbitrary mutation coverage or
establish operator acceptance. Roll back only comparator/test if needed and
retain pending requests and historical evidence. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: dry-run response status boundary (offline)

Two disposable allowed responses claiming a Running phase or Ready condition
passed before repair. An allowed server dry-run may return an absent or empty
Pod status, but a populated status is not evidence of a created/running Pod;
the comparator now refuses it as unknown and retains the pending request.
Focused candidate/CLI suites passed (80 + 23). Dedicated Colima was observed
Stopped; no admission, replay, retained report/volume/backup change or GitHub
write. Fake responses cannot authenticate an API or prove complete mutation
coverage or operator acceptance. Roll back comparator/test only if needed,
preserving pending and historical evidence. Original Vault sync pending outside
the approved checkout.

## 2026-10-04: container resource claims in allowed responses (offline)

Two disposable fake allowed responses added an unsubmitted container resource
`claims` field (populated or empty). Both were previously classified allowed;
the response now requires exactly the submitted `requests` and `limits` keys,
with their existing quantity equivalence checks unchanged. Mismatches remain
unknown/pending and are never replayed. Focused observer suite passed (33).
No admission, retained request/report/volume/backup mutation or VM start;
dedicated Colima was observed stopped. Fake responses do not authenticate the
API, prove arbitrary Pod mutation coverage or establish operator acceptance.
Roll back the comparator and test only if needed, preserving pending requests.
Original Vault sync pending outside this approved checkout.

## 2026-10-04: unsubmitted Pod controls and metadata lifecycle (offline)

Three disposable allowed responses inserted `hostUsers: false`, a resource
claim, or a zero termination grace period; three separate responses added a
generated name, deletion timestamp, or deletion grace period. All six passed
before repair. The spec comparison now refuses unknown keys and these controls
except inert explicit defaults; metadata lifecycle mutations also fail closed
as unknown, retaining the pending request. Focused observer suite (32 tests)
and `make verify-local-experiment` passed. Dedicated Colima was observed
Stopped. No live admission, archived replay, retained report/volume/backup
mutation or GitHub write. Fake responses neither authenticate an API nor prove
complete Pod mutation coverage or operator acceptance. If necessary roll back
comparator/tests only; preserve pending requests and existing evidence. Original
Vault synchronization remains pending outside the noninteractive checkout.

## 2026-10-04: scheduling and lifecycle response controls (offline)

Four disposable allowed dry-run responses added an unsubmitted topology spread
constraint, readiness gate, resource overhead or active deadline. All passed
before the guard and now fail closed as unknown with the original pending
request retained. Empty API defaults still match. Focused observer suite passed
(30 tests). Dedicated Colima observed Stopped; no admission, saved-request replay,
retained report/volume/backup mutation or GitHub write. Fake responses cannot
authenticate the API, cover arbitrary Pod mutations or establish acceptance.
Roll back comparator/test only; preserve unknown requests and historical evidence.
Original Vault sync pending outside the approved checkout.

## 2026-10-04: DNS and metadata response boundaries (offline)

Six disposable allowed responses redirected DNS/hostname/service links and
three injected labels, annotations or finalizers. All nine passed before the
guard and now fail closed as unknown, retaining their pending request. Explicit
inert API defaults still match. Focused candidate tests (75) and full local
verification passed. The dedicated Colima was observed Stopped; no admission,
archived request replay, retained data/backup mutation or GitHub write. Fake
responses cannot authenticate the API or prove complete Pod mutation coverage,
current runtime state or acceptance. Roll back the comparator/tests only if
needed; preserve unknown requests. Original Vault sync remains pending outside
the approved noninteractive checkout.

## 2026-10-04: process namespace and host aliases (offline)

Two disposable allowed responses added `shareProcessNamespace: true` or a
`hostAliases` entry without submission; both passed before repair. They now
fail closed as unknown with the original request pending; explicit inert API
defaults still match. Focused observer 27 tests and local verification passed.
Dedicated Colima stayed stopped; no admission, archived request replay or
retained volume/backup change. Fake responses do not authenticate the server,
prove full mutation coverage or establish acceptance. Roll back comparator and
test only, preserving pending requests. Original Vault sync pending outside
the approved checkout.

## 2026-10-04: allowed response runtime and priority (offline)

Four disposable fake responses injected a runtime class, critical priority
class, nonzero priority or altered preemption policy. Each passed before repair;
the comparator now refuses them as unknown while retaining the pending request.
Explicit inert API defaults remain accepted. Focused observer 26 tests passed.
Dedicated Colima was observed stopped; no admission, saved-request replay,
retained data/volume/backup change or GitHub write. Fake responses do not
authenticate a server or prove complete mutation coverage or acceptance.
Roll back comparator/test only, preserving pending requests. Original Vault
sync remains pending outside the approved checkout.

## 2026-10-04: allowed response owner and pull credentials (offline)

Two disposable fake allowed responses gained a controller owner reference or
image-pull secret despite neither being submitted. Both incorrectly passed
before repair. The comparator now refuses these changes and accepts explicit
empty API forms. Focused observer 25 tests and full local verification passed.
Dedicated Colima was read-only observed stopped; no admission, archived request
replay, retained evidence/volume/backup modification or GitHub write. Unknown
responses retain pending requests. This does not authenticate a server, cover
arbitrary Pod mutation or establish operator acceptance. Roll back comparator
and test only; preserve unknown requests and historical evidence. Original
Vault sync pending outside the approved noninteractive checkout.

## 2026-10-04: allowed response Pod identity and placement (offline)

Six disposable responses injecting a service account, node name/selector,
toleration, affinity or alternate scheduler passed before repair. The allowed
comparator now refuses them while accepting the empty/default API forms.
Focused candidate CLI checks passed (70 observer/candidate and 23 CLI tests).
An unknown result retains the original pending request; never replay it.
Dedicated Colima was observed stopped; no live admission, retained evidence,
volume/backup mutation or GitHub write. Fake responses cannot authenticate the
API or prove arbitrary mutation coverage/operator acceptance. Roll back only
the comparator and test; preserve pending requests and stopped lab. Original
Vault sync pending outside this checkout.

## 2026-10-04: allowed response container execution defaults (offline)

Four disposable allowed responses injected a working directory, stdin, a
post-start lifecycle hook or a different termination message path and passed
before repair. Unsubmitted execution fields now fail closed; explicit inert
Kubernetes defaults remain supported. Focused observer tests passed (23).
The lab stayed stopped: no live admission or saved-request replay. No retained
volume/backup or GitHub write. Fake responses do not prove server authenticity,
all mutation coverage or operator acceptance. Roll back comparator/test only;
retain pending unknown requests. Original Vault sync pending outside checkout.

## 2026-10-04: allowed response port attributes (offline)

Three disposable dry-run responses injecting UDP protocol, host IP or a port
name were incorrectly classified allowed. The comparator now checks the full
supported port attribute set, preserving the harmless API TCP/empty defaults
and rejecting boolean ports. An unknown response retains its pending request;
do not replay it. Focused observer tests passed (22). Dedicated Colima was
observed stopped; no admission, saved request, retained volume/backup or GitHub
write. Fake responses cannot authenticate the API or establish general Pod
mutation coverage or operator acceptance. Roll back only the comparator/test;
preserve pending requests. Original Vault sync pending outside checkout.

## 2026-10-04: allowed response container security context (offline)

Three test-owned allowed dry-run responses injected an unsubmitted container
`allowPrivilegeEscalation: true`, `runAsUser: 0`, or `SYS_ADMIN` capability.
All passed before repair. The comparator now refuses unsubmitted container
security settings except explicit `privileged: false` (a harmless default),
and retains the pending unknown request without replay. The focused observer
suite passed (21 tests). Dedicated Colima was read-only observed stopped; no
live admission, archived request replay, retained evidence/backup mutation or
GitHub write. The fake server cannot authenticate actual admission or cover
arbitrary Pod mutations. Stop new checks or roll back the comparator/test only;
preserve pending unknown requests. Original Vault sync pending outside checkout.

## 2026-10-04: allowed response Pod security context (offline)

In a disposable fake server dry-run, changing a submitted Pod-level
`runAsUser` to 0, changing `runAsNonRoot` to false, or dropping `runAsUser`
still produced `passed/allowed` before repair. The allowed response now
requires the submitted Pod `securityContext` unchanged (or absent/empty when
none was submitted); an unexpected context fails closed as unknown with the
original pending request retained. Container checks and harmless empty API
default remain supported. Policy-focused 104 tests passed. No VM start,
admission call, archived dry-run replay, retained evidence/backup mutation or
GitHub write. This is a fake-response comparison, not independent admission
authenticity, arbitrary Pod mutation coverage or production acceptance. Stop
new candidate checks or revert this guard/test to roll back; keep pending
unknown requests without replay. Original Vault sync pending outside checkout.

## 2026-09-29: bounded live full-CLI privileged denial

専用`mac-studio-solo`をStoppedから設定SHA記録後に一時起動。固定contextでnative policy/binding 16件ready、既存loopback Qwenの期待model IDを確認し、既存の専用AgentHouse/Lokiだけを起動した。hostが`P01`だけを予測するprivileged Pod候補**1件**を新tokenのGo CLI `policy check --pod-stdin`に投入。model 1回でhost期待に一致し、server dry-run request_count=1、native拒否の報告も`P01`、`passed/denied`（CLI exit 2）。監査delivery4件・保存spans4件。保存Go `policy status`はread-onlyで`passed/denied`と`response_content_check=not-applicable`を示す。denyにallowed応答内容照合を誤って主張しない。別の固定context API readで対象Pod不存在、前後16件inventory詳細一致。入力manifest、API拒否原文、model原文は保存せず、private記録は`tmp/local-experiment/policy-full-cli-denied-checks/20260929/`および新tokenのprivate evidence。これは1候補のlive producerと同producer保存statusの整合であり、保存物の独立認証ではない。

今回起動したAgentHouse/Lokiを停止し、Colimaを通常停止してStoppedとprofile設定SHA不変を確認。起動時TCP 53 forward競合、停止時`/var/lib/cni/` busyと保持Pod volume unmountの出力あり。保持PVC/backup内容と停止後guest状態の独立照合なし。policy apply/delete・実workload作成・外部公開なし。複数違反、任意Pod、後日のcluster状態や本番/人間受入へ昇格しない。WP `local-partial`を維持し、未知dry-runは同要求を自動再POSTしない。

全回帰`make verify-local-experiment` 121 suites/3820実行＝3812PASS＋8skip、95.06秒、log SHA256 `0be1d921888b92296c1fd3c4417250fa5589aac7f637d3c372c045d4a91e79f8`。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-09-28: bounded live Go CLI / Qwen / admission path

専用`mac-studio-solo`をStoppedから一時起動し、固定contextのnative policy/binding 16件readyを確認。既存loopback Qwenは期待model IDを返した。Go CLIの初回tokenはAgentHouse/Loki停止により監査preflightで失敗（model_attempts=0、dry-run request_count=0）。同tokenを再送せず、既存の専用AgentHouse/Lokiを起動して新tokenで適合Pod **1件**を`policy check --pod-stdin`へ投入した。新tokenはmodel_attempts=1、model_matched=true、4件の監査deliveryと4 spans検証、server dry-run request_count=1、`passed/allowed`、v2 producer `matched`。別のGo `policy status`で`producer-checked`、固定contextの別API readで対象Pod不存在、実行前後の16件inventory詳細が一致した。最初の失敗と成功を合算してもPod dry-runは1件のみ。private記録は`tmp/local-experiment/policy-full-cli-live-checks/20260928/`およびtoken別のprivate evidence。raw API応答・モデル出力は保存せず、保存statusは現在pathの内部整合性であり独立認証ではない。

今回起動したAgentHouse/Lokiを停止してから専用Colimaを通常停止し、Stoppedとprofile設定SHA不変を確認。起動時TCP 53 forward競合、停止時`/var/lib/cni/` busyと保持Pod volume unmountの出力あり。保持PVC/backup内容と停止後guest状態は独立照合していない。実workload作成・policy変更・外部公開なし。これは1候補のローカル全経路であり、汎用候補集合、後日のcluster状態、本番/人間受入を示さない。WP `local-partial`を維持する。

全回帰`make verify-local-experiment` 121 suites/3817実行＝3809PASS＋8skip、log SHA256 `5f8c1b3498b3eb7aef84473f29dedf82492121710c1e39b7c94bc91013db781c`。design-context/secret scan・diff check PASS。

## 2026-09-28: bounded live allowed-response recheck

停止中の専用`mac-studio-solo`を設定SHA記録後に`--activate=false --save-config=false --ssh-config=false`で一時起動。固定contextのnative policy/binding 16件readyを確認し、適合Pod候補**1件だけ**を候補observerでserver dry-runした。request_count=1、`passed/allowed`、v2 `response_content_check=matched`、Podの前後不存在・policy inventory安定を確認。別のread-only `local-policy-status`では16件のinventory詳細が事前と同一、固定名Podは独立したAPI readでも不存在。Qwen・AgentHouse・CLI全経路は実行せず、policy apply/delete・workload作成なし。private記録は`tmp/local-experiment/policy-live-response-checks/20260928/`。これは**この1回のlive API応答に対するproducer照合**であり、保存物の独立認証、後日のcluster状態、モデル連携、本番受入ではない。WP local-partialを維持する。

通常`colima stop`後に専用profileがStoppedへ戻り、host profile設定SHA不変。起動時にhost TCP 53 forwardのbind競合警告、停止時のk3s killallに`/var/lib/cni/` busy警告とpod volume unmount出力があった。停止前のpolicy inventoryと対象Pod不存在は確認したが、保持PVC/backup内容や停止後のguest状態は独立照合していない。起動に伴う保持workloadの通常稼働やguestの変化まで不変とは主張しない。再実行は新tokenと固定contextで事前inventory確認後のbounded dry-runに限り、未知結果の自動再POSTは行わない。

全回帰`make verify-local-experiment` 121 suites/3808実行＝3800PASS＋8skip、log SHA256 `1cb6a2ae8456275b1bb6d306c0842bfd07b2f245e7b0aca3160a0f2fecefb1ff`。design-context/secret scanとdiff check PASS。live run固有の持続証拠はprivate summary/progressおよび前後inventory/statusで、raw API応答は保存していない。

## 2026-09-28: saved allowed-response coverage is revision-scoped

新しく強化したallowed応答内容照合を、以前の`passed`保存物へ遡及した証拠として扱わない。新producerのverification receipt v2に`response_content_check`を保存し、新規passed/allowedに限り`matched`、deniedは`not-applicable`、未完了・失敗は`not-checked`。read-only status/Go CLIは`producer-checked`、旧v1のpassed/allowedは`legacy-unrecorded`、passed/deniedは`not-applicable`、その他は`unknown`と区別。producerが自分で残したmarkerの内部相関であり、旧raw API応答の再評価や第三者認証ではない。旧保存3件の結果・file集合/SHA/inode/mode不変を確認し、旧passed/allowedを新判定へ自動昇格させない。保存receipt・保持run・backupの移行/書換えなし。Colima起動・live admission再試行なし、WP local-partial。復旧は新入口が不適ならGo/Python protocolを対で戻す。旧保存証拠を削除・再作成しない。private記録は`tmp/local-experiment/policy-saved-coverage-checks/20260928/`。

policy focused103件PASS、Go全663件PASS・policy race20件PASS・vet PASS、全回帰121 suites/3808実行＝3800PASS＋8skip、89.609秒、log SHA256 `1a789397c3165da69a4c86798209ac4d995cb16fb0a683e4d881c73b11cb5845`。design-context/secret scan PASS、専用Colima Stopped。Go protocol初回テストは旧fake応答の必須field欠損1件で失敗、fixture修正後全件通過。最後の負例assertion追加後に全回帰を再実行した。

## 2026-09-27: pin workflow evidence writes to owned output fd

policy focused101件PASS、全回帰121 suites/3806実行＝3798PASS＋8skip、90.172秒、log SHA256 `891e4be8b0312aa866a9ddefd4e029fdabc62ec4801f4518967f26cecbafe271`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-anchored-writes-checks/20260927/`。

前増分は出力directoryの**作成**までfdに固定したが、その後の`candidate.json`/他receipt/OTel span append/再読込が再びpathを辿った。隔離tempで作成直後に親を模擬保持treeへのsymlinkへ差し替えると、旧workflowは`candidate.json`を模擬保持先へ書く負例を再現。今回、workflowの作成後から完了まで開いた出力fdを保持し、JSONのatomic replace、span append/chmod、保存span再読込を同fd相対へ変更。append対象のsymlink・複数linkを拒否。差し替え後の完全workflowでは模擬保持treeは空、元dirにだけcandidate/result/spansが残り、終了時のpath identity不一致を`OUTPUT_PARENT_INVALID`で拒否。元dirを自動削除しない。保存statusは従来どおり現pathのread-only検証であり、この修正は外部同UID actorによる元dir内の内容改変や、保存物の独立認証・live admissionを証明しない。Kubernetes/モデル/実保持run/backup変更なし、Colima起動なし。WP local-partial。

## 2026-09-27: descriptor-bound output creation

policy focused98件PASS、全回帰121 suites/3803実行＝3795PASS＋8skip、87.595秒、log SHA256 `f84bca0f904bc6e8f1ee5302a962b20394cf707c4adc4a42b65fdb7fc9e80ab7`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-fd-output-checks/20260927/`。

既存の作成前symlink検査と`Path.mkdir`の間に隔離tempの親をsymlinkへ入れ替えると、旧実装は拒否しつつリンク先に新ディレクトリを作った。親の各componentを`O_DIRECTORY|O_NOFOLLOW`で開き、欠けた階層と最終token directoryを開いた親fd相対で作成。最後にpathが同じ親/子inodeを指すことを再確認し、差し替え時は`OUTPUT_PARENT_INVALID`で拒否。隔離tempの模擬保持treeでは差し替え先への新規directoryなし、通常のprivate nested creationと既存token拒否も確認。作成後にpathベースで続く保存処理までfdに固定したわけではなく、作成終了後の別actorによる継続的な差し替えは未防御。拒否されたpathや保持物は自動削除しない。Kubernetes/モデル/保持run/backup変更なし、Colima起動なし。WP local-partial。

## 2026-09-27: reject linked output ancestor before mkdir

policy focused96件PASS、全回帰121 suites/3801実行＝3793PASS＋8skip、89.067秒、log SHA256 `ad77fdba20917904274cb424d5d4737ea9a95b7daa13b574e25b2ca161ae157a`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-output-ancestor-checks/20260927/`。隔離tempの模擬保持treeのみを負例に使用し、実保持dataに変更なし。

Standalone候補workflowの`create_output()`は親pathのsymlink検査より先に`mkdir(parents=True)`を呼んでいた。保存先の祖先を別の保持treeへのsymlinkにすると、拒否エラーを返しながらリンク先へ新しいディレクトリを作ることを隔離tempで再現。既存祖先のsymlinkを作成**前**に拒否し、作成後の再検査も維持した。これにより既存リンク経由の意図しない書込みを防ぐ。競合する外部actorが検査と作成の間にpathを入れ替える状況までの原子的な防御は未証明。Kubernetes/モデル/保持run/backupには接続せず、root以外へ新しいartifactは残していない。復旧は拒否されたpathを自動で掃除せず、operatorが保存先を確認して新tokenで新規試行する。WP local-partial。

## 2026-09-27: candidate allowed response content guard (offline)

`--pod-stdin`のserver dry-runでexit 0のJSONを名前・namespaceだけで`allowed`にしていた。fake APIで同名別image、sidecar/init container、CPU request欠損、hostNetwork、hostPathを返すと誤通過することを修正前に再現。修正後は応答Podの限定policy featuresと送信したcontainer名/image、CPU・memory request/limitの意味値、ports、固定安全defaultを照合し、不一致は`unknown`とpendingを維持する。安全側のAPI defaultや等価quantity表現は受理する。生のPod/responseは保存しない。この照合は応答に含まれないcluster状態、すべてのmutation、任意のPod field、admission webhook不在の独立証明ではない。今回はColimaを起動せず、live admission受入の更新なし、WP local-partial。回復は新しいdry-run/保存証拠で行い、未知結果をallowに再分類・自動再送しない。

policy focused95件PASS、全回帰121 suites/3800実行＝3792PASS＋8skip、88.253秒、log SHA256 `b617aac864ae427ee0af592fe8de8f519578078d4a3616a8e61b4a2d7959f73d`。design-context/secret scan PASS。private検証記録は`tmp/local-experiment/policy-candidate-response-checks/20260927/`。

## Summary

Local実験では固定candidateのrule違反・適用範囲を実native admissionと照合する。
モデルは助言だけを返し、権限付与・本番Tier/PRリスク判断は未実装とする。

## 2026-09-18: bounded caller Pod candidate plan

固定15caseだけの実証から、利用者がstdinで渡す限定Pod1件を事前検査できる入口へ進める。
`policy check --pod-stdin`はhostの厳密なinput validation、safe featureだけを使うQwen分類、
同じ候補のnative server dry-runを順に照合する。権限付与・workload実作成・policy変更は行わない。

- [x] 16KiB以内のJSON1文書・Pod1件/container1個と限定fieldだけを受理する。
- [x] 不明field・Secret/環境変数/任意command等を推論前に拒否し、原候補を保存しない。
- [x] P01–P06のhost期待値を独立計算し、特にregistry portとresource値をnative CELと照合する。
- [x] モデルの全違反集合と実APIが報告する違反subsetを区別する。
- [x] protected namespaceと16policy/bindingの同一性、pending intent、dry-run後の不存在を検証する。
- [x] 適合・privileged違反・推論前拒否の3経路、保存status、取消・不明結果を実証する。

送信名はhostが生成し、固定の安全側defaultを補う場合は変換を記録する。
入力/送信bodyのhashと特徴・型付き結果だけを残し、raw manifest/image名/原API応答を保存しない。
原文を保存しないため、後日の再現には元JSONの再提出とhash一致が必要である。
未知結果をallowへ変換せず、自動再送しない。既存15case corpusと認可境界の評価は維持する。

## Context

このWork PackageはLevel L2「Agent第一陣」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- 設計・実装・設定の最小単位を定義する。
- 関連するREADME/Runbook/ADRを更新する。
- CIで検証できる形にする。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- 実装または設定ファイル
- ドキュメント更新
- テストまたは検証ログ
- 必要に応じてADR/Runbook

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l2`, `area:policy`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-POL-001: Policy Gatekeeper Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local development and fixed server dry-run verification.
The model identifies observations; it cannot grant authorization or weaken policies.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

既存native admission baselineの15 server dry-run corpusを再利用し、
モデルが固定candidateのどのrule違反を観測するかを実APIの結果と照合する。
候補はneutral IDsとsafe featureだけにし、allow/denyを含むcase名やhost expected answerを
モデル入力に渡さない。Model出力はviolation/scope-limit/verification IDsのみ。
モデルにallow/approve/Tier付与やpolicy緩和を決定させない。

- [x] 15固定candidateのモデル入力がexpected答えを含まないことを検証する。
- [x] Native policyのsource/manifest identityとready状態を照合し、固定server dry-runだけを行う。
- [x] モデルのrule/limit/verification選択をhost truthと実Admissionへ照合する。
- [x] default namespace controlのallowを安全性や実作成権限へ変換しない。
- [x] ADR-0034の範囲内/範囲外をhostが固定分類し、modelやK8s成功で権限を昇格しない。
- [x] 実Qwen評価、実API比較、Pawprintと保存証跡、README/Vault更新を確認する。

実Pod等の作成、policy apply/delete、GitHub書込、外部公開、追加費用、保持データ削除、
global host変更、全Colima resetはこのentrypointでは実行しない。
Local commitや新規使い捨て実験データ再作成には既存の包括承認があり、
この実装は都度owner承認やGitHub review待ちを再導入しない。
K8s baselineのnamespace適用範囲、image digest syntaxと署名/CVE承認の違い、
server dry-runと実workload動作の違いを明示する。
PRリスクや本番Tier判定・一般的な権限判断の品質は未達として残す。

## 2026-09-17 local functional evidence

実Qwenはnative 15候補（5×3batch）と操作境界10候補（1batch）をhost truthと完全一致で分類。
入力3907/出力1171 tokens、推論合計33.125秒。全分類完了後だけ固定15server dry-runを実行し、
allow2/deny12/対象外namespace control1が期待と一致した。権限付与・境界操作・保存workloadは0。
7件のAgentHouse/Loki/保存OTelとcompleted checkpointを確認した。

実workflowは`tmp/local-experiment/policy-bear/f37433c5b92d462a8ea69f4465c334b5/`。
04:31 UTCの独立再観測で保持6PodのUID/phase/ready/restartsと16policy/binding identitiesは不変、
両namespaceにdry-run残存objectは0。事前/事後証跡は
`tmp/local-experiment/policy-bear-preflight/fa25204603de4af38a659279a90a3dd0/`。
Pending candidateをAPI前に保存し、確認後に結果を保存する。途中失敗・signal・保存障害では
未知の結果と既知/未知token使用を保持し、次の推論/操作を止めることをfakeで確認した。

8合成case×2の実Qwen評価は16/16成功、16監査readback。
入力12454/出力2768 tokens、推論81.187秒、retry0。
`tmp/local-experiment/policy-evaluation/11754e69d7ce4ef89e8e231b5ad5a629/`に全attemptを保存。
Core12＋Bear18の新規tests、既存native17 testsを含む全回帰486 tests、設計73documentsが成功。

再実行は`make local-policy-bear-eval`と`make local-policy-bear-smoke`。
[Runbook](../../../../tools/local_experiment/bears/POLICY_GATEKEEPER.md)に境界・中断証跡・終了方法を記録。
今回の成功は固定25分類と15dry-runに限る。任意policy、一般的なリスク判断、
実workload作成、署名/CVE保証、本番受入、historical GitHub gatesの完了を主張しない。

## 2026-09-18 caller Pod policy CLI evidence

`policy check --pod-stdin`と`policy status TOKEN`を実装した。16KiB以内の限定Pod1件から
safe boolean featuresを作り、独立host期待値・Qwen分類・同じmemory bodyへのdry-runを照合する。
入力のraw bytesはargvや保存filesへ出さず、入力/body hash・変換ID・features・型付き結果を残す。
namespace/name、Never設定、service account token無効だけを補い、policy違反は修復しない。
limitだけでrequestがない入力と、hostNetworkの暗黙hostPortはnative defaultingで判定が変わるため
事前拒否する。resource数量は限定した正の文字列と大小関係を検証し、SI kiloは小文字`k`とする。

実compiled CLIで以下を確認した。各workflowはモデル1回、dry-run1回、retry/cloud呼出し0。

| 保存token | 結果 | Qwen入力/出力 tokens | 推論ms |
|---|---|---:|---:|
| `255b21eed54252825d90870c0811f86f` | passed / allowed、exit0 | 675 / 74 | 4080 |
| `d906b723859c1be5095da0c786ddabb6` | failed / unknown、exit1 | 675 / 84 | 3148 |
| `1e0a67ec0d8785febb8539f1580f5b9f` | passed / denied P01、exit2 | 675 / 84 | 3146 |

最初のprivileged試行はモデルP01一致後、APIのInvalid/422形式をForbidden/403専用parserが
認識できずunknownで停止した。元証拠を保持し、別の新候補によるmodel無しの診断dry-run1回も
unknownとして保持した。Kubernetes v1.33.4の一次sourceに沿ってexact Invalid単一cause形式を追加し、
新tokenで拒否を実証した。policy名・binding名・固定messageの完全一致を維持し、foreign/schema/
混在/余分な末尾は認識しない。未知の元試行を再送・成功への書換えはしていない。

未対応env入力 `02c0bd3c1089d36dd579130a0c2a7f21` はINPUT_INVALIDで推論/API/intent保存前に拒否。
namespace UID/label、空LimitRange、16policy/binding UID/generation/spec/owner/source hashを固定し、
API前のpending intentをfsync、後の不存在とbaseline不変を確認した。権限付与・保存workloadは0。
成功2回と失敗1回に各4件のAgentHouse/Loki/保存OTelを残した。モデル原文・raw API応答は保存しない。
3tokenのstatus各3回（計9回）で32のfiles/directoriesのhash/mode/mtimeが不変だった。
statusは保存features/host期待値/plan/型付き結果/spans/hash sealを検証し、原manifestの再構成や
最新admissionの再観測を主張しない。旧15case corpusは別入口のまま維持した。

Python新規78件（入力27/観測17/workflow13/CLI21）、Go91 top-level testsと59 subtests、
vet/raceが成功。実Python bridge＋実workflow/外部fakeの7経路でexit/取消/再送防止/非保存を検証。
全local回帰1658件（1657成功・既存optional OTel1skip）、設計83documents/関連14tests、secret検査も成功。
証拠は `tmp/local-experiment/policy-cli-checks/38ccaf07c7e64c93935b49fa18234d4d/`、
各workflowは `tmp/local-experiment/policy-candidate-bear/<token>/`、
独立監査は `policy-candidate-preflight/6abcff1fbcd841e9be85f43791c848b2/`。
[Runbook](../../../../tools/local_experiment/bears/POLICY_CLI.md)に入出力と停止時の扱いを記録した。
実証は限定PodのP01適合/違反と入力拒否であり、全P01–P06のcaller入力live網羅、任意manifest、
画像署名/CVE、scheduling、アプリ動作、権限判断、本番受入の完了とは区別する。

独立の軽量再観測でAgentHouseは453→465（上記3workflow各4件）、旧453件の全行hashは不変。
対応Loki各4件、保持3410files、14Pods/3Docker/MLflow identity、16policy/bindingとnamespaceは不変、
LimitRange空、candidate Pod残存0、既存tunnel閉鎖を確認した。深いDB/metadata/fixture観測は
直前のQuery/SRE監査の保存証拠を継承し、今回は各ストアの全内容を再走査していない。
