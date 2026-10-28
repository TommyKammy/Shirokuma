---
project: Shirokuma
doc_id: "ADR-0037"
title: "Use Bounded Database Creation Delegation and Runtime Handoff"
status: accepted
created: 2026-10-23
updated: 2026-10-23
version: "1.0"
area: adr
tags: [shirokuma, adr, database, authorization, workbench, operator]
---

# ADR-0037: Use Bounded Database Creation Delegation and Runtime Handoff

## Context

Database Phase 5 adds a WebUI path from registration to READY. Existing registration creates metadata only;
the public apply route saves a handoff request. Actual bootstrap/full application requires private materials,
an authorized operator, warehouse drain/CAS, and verification of both normal runtimes.
The existing operator authenticates the same actor as the request. A shared private executor cannot replace
that actor, and an HTTP request cannot own an apply that restarts its own server.

The [Phase 5 plan](../../guides/database-phase5-implementation-plan-2026-10-23.md) preserves two explicit
approvals, desired/effective grants, immutable plans, UNKNOWN reconciliation and human acceptance.
This ADR fixes the P5-0 design. It does not assert that the new APIs, migration or runtime bridge are implemented.

## Decision

1. Add a database-creation-specific durable root linking existing registration, metadata/grant Operations,
   bootstrap/full plans and public action requests. Existing Operation/step ledgers remain authoritative.
   Admit one active creation per configured environment/warehouse in the same transaction as registration
   and initial desired grants. Competing registrations fail before adding a DB. No generic workflow engine is added.
2. Keep public environment/profile/targets server-owned. Public clients submit names, existing principals,
   current revisions and explicit plan approvals. They cannot submit private paths, secrets, commands or targets.
   GET/poll never dispatches. Existing registration-only and manual-handoff semantics remain intact.
3. Add a private, typed P5 delegation resolved from durable approval and configured executor identity.
   Check current requester authority and independent executor authority at dispatch and commit boundaries.
   The core must recognize P5-associated Operations and active creation reservations, including calls from
   legacy entrypoints; optional callbacks alone cannot enforce this boundary.
   Non-P5 manual Operations retain the current same-actor operator contract.
4. Atomically stop a principal's new P5 changes/approvals and revoke unfinished approvals when authority loss
   is detected or private withdrawal is requested. Private authority-change procedures do this before editing
   account configuration. Restoration does not release the stop or revive an approval. Direct file changes
   that revoke and restore identical values entirely between checks remain outside the detection guarantee.
5. Extend the existing canonical Go Workbench/native owners with fixed private IPC and managed children.
   The Workbench owner launches one-shot operator work separately from its HTTP child. Each owner can switch
   only its own known Python child to the sealed candidate profile. The full verify intent contains the
   bounded runtime handoff and its private receipts; bootstrap does not switch runtimes. No seventh core step
   or new resident supervisor is introduced. Owner ACK alone never proves readiness.
6. Keep intent-before-effect, immutable receipts and no blind replay. UNKNOWN cannot be cleared by TTL,
   PID absence, an HTTP success code or read-only Job fencing. Private observation may continue while stopped,
   but it grants no new external mutation, SQL submission, effective grant, READY or OPEN transition.

## Consequences

Implementation must refactor transaction-owning registration/grant helpers for shared connections, add
P5-specific persistence and mandatory core gates, and separate Go owner/HTTP/operator lifetimes.
These are product paths; ignored acceptance helpers are not production dependencies.

Bootstrap/full remain separate reviews. The warehouse remains restricted while waiting for full approval.
Failure can require an explicit private repair or remain blocked when production evidence is insufficient;
the UI must show that limit instead of declaring success.

The configured local foundation is a prerequisite. The stopped, populated P4 clean environment is retained
as evidence, and a separate empty foundation is required for fresh P5 reconstruction acceptance.
Machine, browser, real operation and owner acceptance remain separate gates.

Exact API, persistence, delegation, handoff and recovery rules are in the
[P5-0 contract](../../guides/database-p5-0-contract-2026-10-23.md).
Current prerequisite evidence and remaining setup are in the
[P5-0 preflight report](../../guides/database-p5-0-preflight-2026-10-23.md).
This decision is additive to [ADR-0036](ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md);
its local-only authorization and Commander/Worker boundaries remain unchanged.
