---
title: "Session reuse acceptance test plan"
status: active
created_at: 2026-08-07
last_validated_at: 2026-08-07
---

# Session reuse acceptance test plan

## 1. Purpose

This document defines the reusable acceptance procedure for ARS Session continuity. It verifies more than a
successful second Run: the same durable ARS Session must bind the same external AGENT Session, later Runs
must use a real `session/load`, conversation context must survive process boundaries, and every invalid or
unsafe reuse attempt must fail closed without falling back to `session/new`.

Authority: [`GOAL.md`](../../GOAL.md) load-bearing contract 3, PRD R4/R5/R10/R11/R13, architecture §4,
and the technical solution's closed `SessionStartPlan` and reconciliation rules.

## 2. Scope

The acceptance matrix applies to every operator-registered ACP AGENT intended for use through ARS. The
minimum deployment matrix currently includes:

- Claude Code through its registered ACP adapter;
- Codex CLI through its registered ACP adapter;
- OpenCode native ACP;
- Cursor CLI native ACP.

This plan tests Session continuity, binding, concurrency, idempotency, and recovery. Permission enforcement
is a separate acceptance domain. A Session result does not waive the mandatory per-agent denied-action
canary, and a permission failure does not by itself disprove Session continuity.

## 3. Isolation and prerequisites

Execute every case against a disposable, isolated ARS instance with:

- the exact package version under test;
- a private Unix socket and empty supervisor root;
- a read-only copy of the operator registry outside the repository;
- one isolated workspace per AGENT and per isolation case;
- no production Run, Session, socket, registry, service unit, or state mutation;
- real local AGENT commands and their normal operator-owned conversation stores;
- bounded Run deadlines and a controller-owned cleanup path.

Before the first model call, record:

- ARS distribution version and `server_info` API version;
- registered AGENT ID, local CLI/adapter version, requested model, and requested effort;
- registry snapshot identity without environment values;
- supervisor root, socket, and workspace identities in the local evidence bundle;
- service/process baseline and existing child-process count.

Random continuity values must be generated per case and per Session. Reports retain only their SHA-256;
raw values stay inside bounded test prompts and Run evidence.

## 4. Common pass criteria

A positive reuse case passes only when all applicable conditions hold:

1. Run 1 omits `session_id`, reaches `completed`, and emits `session_new_requested`.
2. The submit acknowledgement returns one non-empty ARS Session ID.
3. The durable Session record contains one non-empty external AGENT Session ID.
4. A later Run supplies the exact ARS Session ID returned by Run 1.
5. The later Run emits `session_load_requested` and never emits `session_new_requested`.
6. The later acknowledgement returns the same ARS Session ID.
7. The external AGENT recalls the expected context exactly across distinct Run processes.
8. Requested model/effort equal effective readback for every Run.
9. The Session carries no quarantine evidence and remains reusable.
10. Every Run process and descendant in ARS's process group is reaped after terminal publication.

A process exit code, a `completed` envelope, transport-level load success, or equal Session IDs alone is not
acceptance evidence.

## 5. Test cases

### P0 — Core continuity

#### SR-01 — Two-Run continuity

For each registered AGENT:

1. Run 1 creates a Session and asks the AGENT to remember a random value.
2. Wait for Run 1 to reach a trustworthy terminal and for its child process to be reaped.
3. Run 2 reuses the returned ARS Session ID and asks for the value without restating it.

Expected: all common pass criteria hold. This is the minimum release/deployment continuity gate.

#### SR-02 — Multi-Run continuity

Execute five strictly serial Runs in one Session. Each Run adds one independently generated value; the fifth
Run must return all values in order.

Expected: Run 1 uses `session/new`; Runs 2–5 use `session/load`; no value is missing, duplicated, reordered,
or invented; the Session remains reusable after Run 5.

#### SR-03 — Session isolation

Create Session A and Session B for the same AGENT in separate workspaces. Store different random values and
query both Sessions in interleaved order.

Expected: each Session recalls only its own values. No event, response, or durable binding crosses between
A and B.

### P1 — Refusal and binding boundaries

#### SR-04 — Unknown or invalid Session

Submit reuse requests with an absent safe-form ID and malformed IDs.

Expected: stable pre-dispatch refusal (`SESSION_NOT_FOUND_FOR_REUSE` or request validation); no Session
record, child process, `session/new`, `session/load`, or prompt marker is created.

#### SR-05 — Cross-AGENT reuse

Create a Session with AGENT A, then submit the returned ID against AGENT B.

Expected: `SESSION_BINDING_MISMATCH` before spawn/prompt; the original Session remains unchanged and
reusable by AGENT A.

#### SR-06 — Workspace or authority drift

Reuse a Session with a different workspace binding, owner, or namespace.

Expected: fail-closed binding refusal before lease mutation and before ACP work; no replacement Session is
created.

#### SR-07 — Missing, corrupt, unbound, or quarantined record

In isolated roots, test a missing record, malformed JSON, missing external AGENT Session ID, and valid
quarantine evidence.

Expected: each case is classified distinctly where the API contract distinguishes it, but every case refuses
new work and emits neither `session/new` nor a prompt marker.

#### SR-08 — Deliberate continuity cut

Create a Session, stop the isolated daemon, and change only a semantic identity choice in the isolated
registry: add/bump `session_epoch`, change `agent_id`, or select a different ACP profile. Restart the isolated
daemon and attempt reuse.

Expected: stable binding refusal. As a control, an identity-preserving command/args/environment edit plus
restart must not invalidate the Session by itself.

### P1 — Concurrency, duplicate prevention, and recovery

#### SR-09 — Same-Session concurrency

Keep one Run active and concurrently submit another Run using the same Session ID.

Expected: exactly one active lease; the competing Run is refused as `SESSION_BUSY`; no second prompt is
dispatched and the Session remains usable after the first Run terminates.

#### SR-10 — Different-Session concurrency

Run independent Sessions concurrently up to the configured daemon capacity, including the full registered
AGENT matrix.

Expected: independent Sessions make progress without cross-talk; capacity behavior is deterministic; all
children are reaped.

#### SR-11 — Idempotent retransmission

Repeat an accepted submit using the same authenticated principal and `request_id`, both while the original
Run is active and after it reaches terminal.

Expected: byte-equivalent accepted facts with the same Run and Session IDs; exactly one Run directory, one
child process, and one prompt dispatch.

#### SR-12 — Daemon restart after a completed Run

Complete Run 1, stop the isolated daemon cleanly, start a new daemon process against the same isolated root
and registry, then submit Run 2 with Run 1's Session ID.

Expected: startup reconciliation succeeds, Run 2 uses real `session/load`, context is recalled, and no prompt
is replayed during startup.

#### SR-13 — Caller disconnect

Submit a Run and close the caller connection. Reconnect, query the accepted Run to terminal, then reuse its
Session.

Expected: caller disconnect neither cancels nor replays the Run; status/events remain queryable and reuse
works normally.

#### SR-14 — Cancel or timeout after dispatch

For separate Sessions, force cancellation and timeout only after `prompt-dispatch-started`. Ensure the child
requires escalation long enough to exercise the uncertainty path.

Expected: the Run becomes `unknown`, the Session receives quarantine evidence, `retryable=false`, and every
later reuse request is refused. No automatic replay or successor Run is created.

## 6. Evidence bundle

Persist one sanitized machine-readable receipt per case and one suite summary. Each case receipt includes:

- test case ID and matrix leg;
- ARS package/API version and registered AGENT/version observations;
- requested/effective model and effort;
- ARS Session ID and ordered Run IDs;
- proof that a non-empty external AGENT Session ID is bound, without publishing that ID;
- terminal status, detail code, retryability, and quarantine state;
- fully paginated normalized event-family counts;
- `session/new` versus `session/load` assertions;
- dispatch-marker assertions;
- continuity-value SHA-256 and exact-recall verdict;
- each Run's observed child-process identity, proving distinct process-per-Run execution;
- process/cgroup cleanup verdict;
- test start/finish timestamps and isolated-instance identity.

Raw prompts, random continuity values, environment values, credentials, auth stores, raw stderr, and external
Session IDs must not appear in the suite summary or committed repository material.

## 7. Execution order and stopping rules

Execute in this order:

1. SR-01 through SR-03 for all registered AGENTs.
2. SR-04 through SR-08 against isolated state and registry copies.
3. SR-09 through SR-14 against isolated daemon instances.
4. Re-read every result, Session record, and complete event stream from disk; then verify daemon/service and
   process cleanup independently of the harness verdict.

Stop a matrix leg immediately when:

- a reuse Run emits `session/new`;
- context crosses Session boundaries;
- a prompt is dispatched after a pre-dispatch refusal condition;
- a possibly dispatched prompt lacks a trustworthy terminal but the Session is not quarantined;
- the harness touches a production runtime surface;
- evidence is partial, overwritten, or cannot be bound to one test attempt.

A failed leg does not erase completed evidence from other legs. Report the concrete product/contract defect,
minimal reproduction, affected AGENTs, and exact retained artifact state.

## 8. Suite verdict

- **PASS:** every mandatory case passes for every applicable registered AGENT, all evidence is complete, and
  independent cleanup verification passes.
- **PARTIAL:** core continuity passes but one or more recovery/boundary cases are unexecuted or unsupported;
  name them explicitly.
- **FAIL:** any executed case violates its expected behavior, even if all model responses looked correct.
- **BLOCKED:** prerequisites, authentication, model availability, or safe isolation prevent execution before
  the behavior under test is reached.

A suite verdict is technical acceptance evidence only. It does not authorize release, deployment, service
restart, production cutover, caller integration, or permission-policy acceptance.
