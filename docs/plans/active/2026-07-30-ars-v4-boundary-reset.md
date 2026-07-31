---
title: "ARS V4 External AGENT Boundary Reset — proposed implementation plan"
status: active
revision: 2
created_at: 2026-07-30
implementation_authorized: true
implementation_authorized_at: 2026-07-30
profile_retirement_approved: false
production_authorized: false
plan_kind: proposed-implementation-plan
requires_decision_1_option: "a"
activation_authorizes: "planning status only — not source, test, documentation, Git, GitHub, or runtime work"
source_implementation_approval: "separate and distinct; recordable only after the Stage 0 gate (G0) merges"
baseline_commit: 79f30aeb255e6507afd001ef2a4bf190f54e42a3
baseline_tag: v0.5.3
design_source: .cache/ars-architecture-redesign/v4-recovery-attempt-1/design-v4.md
design_sha256: 419dad53dc99d221545a30b8f0b08259ced50da9c54b30a65e981311c6f5969d
review_source: .cache/ars-architecture-redesign/v4-review-attempt-1/review-v4.md
review_sha256: 3c799860dd9fe9b134672f0de312ac86ad0d37060e929a315a1fce8d6a304bc4
review_verdict: PASS (disclosed one-time independence waiver; not transferable)
plan_review_source: .cache/ars-v4-implementation-plan/review-attempt-1/review.md
plan_review_sha256: 91035c153e6acacb34c44d43e34cee3732db56ea84410560c71190289adecb64
authority_status: "V4 is reviewed but NOT tracked repository authority at plan authoring time"
human_decisions_open: [profile-retirement, cutover-and-legacy-session-load-refusal, legacy-line-lifetime]
---

# ARS V4 External AGENT Boundary Reset — proposed implementation plan

This plan turns the reviewed V4 architecture closure into an executable engineering sequence. It implements V4 exactly. It invents no architecture, opens no new scope, and authorizes nothing.

**Frontmatter semantics, stated once so no field can be read as an authorization.** `status: proposed` → `active` records only that this is the board-linked planning artifact; it authorizes no source, test, documentation, Git, GitHub, or runtime work. `implementation_authorized` stayed `false` until a **distinct explicit source-implementation approval** was recorded after the Stage 0 gate merged (§3 Gate B, §11 row 7); that approval was recorded on 2026-07-30 and the flag is now `true` as a statement of fact about Stages 1–3 **local source work only**. `profile_retirement_approved` stays `false` until the separate execution confirmation of §11 row 8. `production_authorized` stays `false` throughout; nothing in this plan can change it.

> **Activation record — 2026-07-30.** This plan is now the board-linked planning artifact
> (`status: active`), and Stage 0 is executing on branch `docs/v4-authority-reset` as a
> documentation-only candidate. Three decisions were recorded on 2026-07-30, and no others:
> **§11 row 2** — Decision 1 as option **(a)**; **§11 row 1** — plan activation, planning status only;
> **§11 row 3** — Stage 0 documentation execution (WP0.1–WP0.5, both READMEs included). **At that point
> every other row of §11 was still not approved**, and all three frontmatter flags were still `false`; the
> §11 table and the frontmatter now carry the live status, and the later same-day source-implementation
> record below is the only thing that changed since. §2 describes the pre-Stage-0 baseline as it stood at authoring time; the
> live authority chain has since moved to V4 and the board carries the authority-versus-source delta.
> Activation authorized no source, test, Git, GitHub, or runtime work, and merging Stage 0 will not either
> (§3 DoR-8, §11 row 7).

> **Source-implementation record (DoR-8) — 2026-07-30, after the G0 merge.** Stage 0 merged as PR #99;
> `docs/roadmap/current-status.md` links this plan and states the Stage 1→3 delta, so DoR-4…DoR-7 are
> satisfied. The decision owner then recorded the distinct source-implementation approval of **§11 row 7**
> — "批准开发 S1 ～ S3" — dated after that merge, and, separately, **§11 row 4** per-stage commit/push
> ("开发过程中，根据实际情况提交、推送代码"), exercised by the controller only after a stage candidate has
> passed its verification, independent review, and acceptance gates. `implementation_authorized` is
> therefore `true`. This record covers **local source, test, and status work for Stages 1–3 only**, taken
> serially at their stage gates. It does **not** approve **§11 rows 5 and 6** (PR creation, merge), row 8
> (profile-retirement execution at WP3.3), or any release, deployment, configuration write, service
> restart, migration/cutover, real-agent canary, or production action; those rows stand unapproved exactly
> as written below.

---

## 1. Task Contract

### 1.1 Objective

Replace the `v0.5.3` Binding/artifact runtime layer with V4's four-way boundary in ARS source and tracked authority, so that:

- ARS supervises external ACP AGENTs it does not own: one operator-owned TOML registry read once at daemon startup, one ARS-owned local `ManagedProcess` per Run, ACP JSON-RPC over stdio, `arsd` UDS ingress;
- the six review blockers stay closed in source: fail-closed load-only reuse plus callback-entry identity rejection (B1), complete environment-value non-persistence across every ARS-owned sink (B2), the correctly scoped filesystem boundary (B3), total ordered crash reconciliation (B4), the eight-operation API-version drain matrix (B5), and the one-file startup registry contract (B6);
- acceptance criteria A1–A17 are proven by real tests, not by prose.

### 1.2 Done criteria

| # | Criterion | Evidence |
|---|---|---|
| D1 | Tracked authority — including both public READMEs — describes V4, not the Binding architecture, and claims nothing V4 disclaims | Stage 0 acceptance; A14 claim scan |
| D2 | `native_acp/runtime_binding.py` and `native_acp/attestation.py` are deleted; no module re-creates artifact identity, promotion, digests, ownership/mode gates, or credential-root inspection under another name | Stage 3 acceptance; A4/A11 scans |
| D3 | Exactly one registry open per daemon lifetime; zero registry filesystem access on the Run, spawn, finalize, and reconcile paths | A13 instrumented open counters |
| D4 | A reuse request can never reach `driver.new_session`, structurally | A17 typed-plan call-graph + fault injection |
| D5 | No projected environment literal survives in any ARS durable artifact, hash input, log, exception, event, inspect response, or daemon API response | A16 whole-tree/hash-input/API sentinel scans |
| D6 | Reconciliation assigns exactly one row and one vocabulary outcome to all 216 artifact combinations under every Session-record state | A10 generated oracle |
| D7 | `api_version` 2 ships with the exact eight-operation drain matrix; `submit` refused at v1, the other seven accepted | A15 per-operation matrix |
| D8 | Runtime stays Python stdlib-only; `uv lock --check` and `tools/check_version_sync.py` stay green with no dependency, lockfile, or version change | Verification ladder |
| D9 | `main` is releasable at every merge point; no stage leaves a half-wired admission, spawn, or evidence path | Per-stage acceptance checklists |
| D10 | Every acceptance criterion A1–A17 maps to at least one named test that fails before its work package and passes after | §7 traceability |

### 1.3 Hard constraints

1. **v1 execution shape is fixed**: operator-installed local command → exactly one ARS-owned local `ManagedProcess` per Run → ACP JSON-RPC over stdio. `ManagedProcess` is non-optional after spawn.
2. **No endpoint/remote/attach/TCP/HTTP/SSE/WebSocket seam** — no key, field, branch, dependency, or abstraction anticipating one. `transport` is refused as an unknown registry key.
3. **External AGENT ownership stays outside ARS**: install, configuration, credentials, `HOME`, plugin/cache/Session state, and upgrades. ARS performs no ownership, mode, ancestor, symlink, or digest check on `command` or its ancestors.
4. **Registry is one required TOML file read exactly once at daemon startup** into an immutable snapshot. Any defect refuses to listen before any state write.
5. **Declared command semantics are preserved byte-for-byte**: `argv[0]` is the declared string; the image is located by `execvp`-style lookup over the child's projected `PATH`; no `executable=` override, no `/proc/self/fd/N` image, no realpath, no pre-flight resolution gate. Resolution facts are evidence with `authoritative: false`.
6. **B1–B6 and A1–A17 are the binding technical contract.** Where this plan and V4 disagree, V4 wins and the plan is wrong.
7. **Runtime is Python stdlib-only.** `tomllib` and `contextvars` are stdlib on `requires-python >= 3.11`; neither is currently imported in `src/`. No new dependency, no lockfile edit.
8. **acpx stays a bounded differential/comparison test reference only** — never product, runtime, fallback, or session store. `tests/native_acp/test_no_acpx_coupling.py` stays green throughout.
9. **No feature flags, compatibility shims, endpoint abstractions, deployment frameworks, or speculative security controls** beyond what V4 explicitly requires.
10. **Each behavior-bearing work package begins with a real failing test** that fails for the intended missing behavior, then minimal GREEN, then focused and full regression.
11. **Separate approvals**, none transitive: plan activation · Stage 0 documentation work · source implementation · commit/push · PR · merge · release · deployment/config · service restart · migration/cutover · profile-retirement execution · legacy-line retirement · the future repository-backed deploy/restart skill.

### 1.4 Non-goals

Remote transport, attach-to-running-agent, plugin loading, containers, sandboxing, ARS credential resolution, public/TCP/root ingress, multi-tenancy, distributed scheduling, broad RBAC, a second conversation database, workspace content-digest service, filesystem watcher, durable per-Run Worker, generalized Session rebind, cross-AGENT Session reuse, automatic replay/retry/resume, acpx removal work, Sachima/Gateway/IM integration, caller cutover, release/publication, and any `/opt`, Binding-root, artifact-tree, registration, credential, deployment, restart, or production action.

Explicitly deferred as V4 non-blocking follow-ups (§17), carried but not promoted to gates: `expected_binding_hash` disposition (F1), `agents doctor` version-probe mechanism (F2), the external authority recording the per-agent canary prerequisite (F3), A2's shim `$0`-rewriting test refinement (F4), and `arsd/operand.py` entry intent (F5).

---

## 2. Current position and authority gap

### 2.1 What the repository actually is at baseline

Verified by direct inspection at `79f30ae` (`v0.5.3`), clean `main`:

| Fact | Location |
|---|---|
| Four registered profiles: `opencode-native-acp`, `codex-acp-1.1.7`, `claude-agent-acp-0.63.0`, `standard-native-acp-v1` | `native_acp/profile.py:1136,1234,1376,1509` |
| Binding reader is the largest module in the tree (2,322 non-blank lines) | `native_acp/runtime_binding.py` |
| Attestation layer present (1,023 lines) | `native_acp/attestation.py` |
| `/opt/agent-run-supervisor/artifacts` is a source constant | `native_acp/profile.py:100` |
| `--binding-root` required in daemon mode and for unit rendering | `arsd/__main__.py:402-404,799-811,863-878`; `arsd/service_unit.py:103,152-168` |
| Reuse falls through to `session/new` when a Session record is absent or unbound | `native_acp/run_task.py:586-640,681-693` |
| Callback identity is a sticky observation, terminal/elicitation callbacks are varargs | `native_acp/client.py:64-73,170-188` |
| `_spawn_env` re-reads `os.environ` at spawn; `fixed_env`/`permission_env` values are serialized into `launch.json` | `native_acp/run_task.py:1063-1078`; `native_acp/spec.py:341-372` |
| Reconciliation collapses absent and corrupt, and checks one marker with `is_file()` | `arsd/reconcile.py:79-81,270-277` |
| `ARSD_API_VERSION = 1`; version rejected in the envelope decoder | `arsd/protocol.py:18-19,253-262` |
| Legacy launch hash recomputed over value-bearing records | `commands.py:588-599` |
| Two maintained public READMEs, both Binding-shaped; repository rule requires them to move together | `README.md:104-296`; `README.zh-CN.md:120-155`; `AGENTS.md:56` |
| Zero runtime dependencies | `pyproject.toml:26` |

### 2.2 Why this plan is proposed and not executable

Three independent blocks:

1. **V4 is not tracked authority.** It is a Git-ignored design artifact under `.cache/` (ignored by `.gitignore:127`). `GOAL.md` contracts 1, 9, 10, 11, PRD R13/R14, `docs/design/architecture.md` §3.1–§3.3, and `docs/design/technical-solution.md` §1.2–§1.4/§2 all still describe the Binding/artifact architecture and are the current development source of truth (`GOAL.md:167-179`, `docs/AI_FLOW.md:17-46`).
2. **`docs/roadmap/non-approvals.md:69-72` forbids the central act this plan performs**, verbatim: "retiring, deprecating, disabling, deleting, aliasing, or redirecting `opencode-native-acp` or any other registered profile, **or introducing any mechanism capable of doing so**", and records that adding such a mechanism and using it are two separate decisions, neither taken. V4 §4.2–§4.4 retires all three per-agent profiles. V4 §15 decision 1 recommends approval **(a)**; the decision belongs to the decision owner, not to this plan or to the architecture.
3. **The board still links a conflicting active plan.** `docs/roadmap/current-status.md:14` links `docs/plans/active/2026-07-29-standard-native-acp-v1.md`, whose scope explicitly protects the three profiles ("`opencode-native-acp` stays registered at r3, resolvable, admissible, launchable, and hash-identical"). Its source work has in fact merged — `standard-native-acp-v1` is registered at `profile.py:1509` on `v0.5.3` — so the board's "In review on branch" line (`current-status.md:26,37`) is stale.

The review's PASS also does not carry forward: it was granted under a disclosed one-time independence waiver bound to design hash `419dad53…` and authorizes "no future role-matrix, implementation, repository, runtime, or delivery action" (`review-v4.md:110`).

### 2.3 Decision 1 — this plan is conditional on option (a)

**This plan implements V4 §15 decision 1 option (a) — retirement of all three per-agent profiles — and only option (a).** Its Stage 0 authority edits delete `non-approvals.md:69-72`, its Stage 3 shrinks `native_acp/profile.py` to exactly two registered profiles, and its acceptance criteria (A4, A11, A13, and the two-profile registry assertion in WP3.3) are unsatisfiable under any other outcome. There is no branch, flag, or degraded mode in this plan for options (b) or (c), and none may be added.

**Recommendation to the decision owner: option (a).** The evidence is V4 §4.4: `codex-acp-1.1.7` contains no ACP-semantic deviation (every constant classifies as an environment value, a deployment path, a selector id, an ownership overreach, or the retired artifact layer), `opencode-native-acp` contributes only a source-owned mediation binding, and only `claude-agent-acp-0.63.0` carries a cited ACP-semantic deviation, which survives as `claude-agent-acp-compat-v1`.

**If the decision owner chooses (b) or (c), this plan stops.** Neither outcome makes this plan Ready, and neither may be treated as satisfying Stage 0 readiness:

| Outcome | Effect on this plan |
|---|---|
| **(a)** approve retirement as part of the reset | this plan may proceed to Stage 0 readiness |
| **(b)** keep the three profiles registered but unreachable on the new line | **this plan is withdrawn.** V4's §13.2 `profile.py` shrink, its §4.2 two-entry registry, and A4's no-deployment-facts scan all assume the three profiles are gone. A "registered but unreachable" line is a *different* architecture requiring its own design decision, its own source-impact map, and a **different, explicitly approved plan** |
| **(c)** block the reset | **this plan is withdrawn.** No stage runs; `docs/roadmap/non-approvals.md:69-72` stays in force verbatim; the Binding architecture remains tracked authority and target |

This plan records the recommendation and does not act on it. Decisions 2 and 3 are deployment-stage gates and do not block source work (§9).

---

## 3. Definition of Ready — two gates, not one

Readiness is split, because plan activation is not implementation authorization. **Gate A** must pass before Stage 0's documentation work. **Gate B** must pass before any edit under `src/` or `tests/`. Passing Gate A never implies Gate B.

### Gate A — Stage 0 readiness (documentation work only)

| # | Requirement | Verifiable by |
|---|---|---|
| DoR-1 | **Decision 1 approved as option (a)**, explicitly and in writing, by the decision owner. Silence, inference from this plan, and inference from the V4 review are all insufficient. An approval recording **(b)** or **(c)** does **not** satisfy this item: it withdraws this plan (§2.3) and requires a different, explicitly approved plan | recorded decision naming option (a) |
| DoR-2 | **This plan activated**: `status: proposed` → `status: active` and the board link updated. Activation changes the planning status and nothing else — `implementation_authorized` stays `false`, and no source, test, documentation, Git, GitHub, or runtime action is authorized by it | plan frontmatter + board |
| DoR-3 | **Stage 0 documentation work separately approved** (§11 row 3). This is a distinct decision from DoR-1 and DoR-2 | recorded approval |

### Gate B — source-implementation readiness

No file under `src/` or `tests/` may be created, edited, or deleted until all five hold.

| # | Requirement | Verifiable by |
|---|---|---|
| DoR-4 | **V4 promoted into tracked authority**: `GOAL.md`, `docs/product/prd.md`, `docs/design/architecture.md`, `docs/design/technical-solution.md`, `docs/design/result-event-schema.md`, new `docs/design/agent-registry.md`, **`README.md` and `README.zh-CN.md`**, `docs/roadmap/features.md`, `docs/roadmap/current-status.md`, `docs/roadmap/non-approvals.md`, new `docs/archive/binding-era-2026-07/` | Stage 0 acceptance |
| DoR-5 | **`docs/roadmap/non-approvals.md:69-72` replaced** by V4 §13.1's new clause set, and only after DoR-1 recorded option (a) | diff review |
| DoR-6 | **The conflicting active plan archived correctly**: `git mv docs/plans/active/2026-07-29-standard-native-acp-v1.md docs/plans/archive/`, `status: archived`, `archived_at` added, and a one-line note that its *implemented scope merged* while the *architecture it implemented* is retired by V4 — it is archived as completed, never used as target authority | `tools/check_roadmap_governance.py` |
| DoR-7 | **Board links this plan**: `active_plan: ../plans/active/2026-07-30-ars-v4-boundary-reset.md`, the stale "In review on branch" lines corrected, and the exact authority-versus-source delta stated for the Stage 1→3 window | `tools/check_roadmap_governance.py` |
| DoR-8 | **G0 merged, and a distinct explicit source-implementation approval recorded afterwards** (§11 row 7), flipping `implementation_authorized` to `true`. DoR-1, DoR-2, DoR-3, and the merge of G0 each individually and jointly do **not** imply it. This mirrors the standing repository rule that an active plan is not approval to land source (`docs/roadmap/non-approvals.md:52`) | **Satisfied 2026-07-30**, dated after the G0 merge — see the source-implementation record above and §11 row 7 |

**Stage 0 is documentation-only. It changes no source file, no test, no dependency, and no runtime surface.** It is also the point where V4's honest cutover consequences enter tracked docs (§9.2), including reviewer note 4's operator-visible evidence-loss tradeoff.

---

## 4. Recommended implementation strategy

### 4.1 The indivisible core, and what is genuinely separable

The reset itself cannot be split. Deleting `runtime_binding.py`, adding `agent_registry.py`, moving `AgentRunRequest` from `profile_id` to `agent_id`, and replacing `--binding-root` with `--agents-file` are one contract: any subset leaves `main` unable to admit a Run at all. That work is **one PR with internal checkpoints**, not several PRs that cannot stand independently.

Three bodies of work *are* independently valid, because each is a strict fail-closed or confidentiality strengthening of the architecture that is live today, needs nothing from the registry, and leaves `main` releasable:

| Body | Independently valid because | Reuse into the reset |
|---|---|---|
| **B1** closed `SessionStartPlan` + callback-entry identity rejection | closes a live defect: on `v0.5.3` a `reuse="reuse"` request whose Session record is absent creates one and calls `session/new` (`run_task.py:594-612,681-693`) | plan types, disjoint match arms, call-graph invariant, and the whole callback boundary carry over unchanged; only `validate_native_binding`'s argument set changes in Stage 3 |
| **B4** exhaustive reconciliation | classifies artifacts that already exist; no schema, hash, API, or wire movement | carries over whole; shrinks the Stage 3 diff in `arsd/reconcile.py` to zero |
| **B2a** `RunTextGuard` + dynamic sink boundary | the set of final projected child values exists today via `_spawn_env`; guarding child-controlled text is strictly better than baseline at every sink | Stage 3 only swaps the *source* of the value set (`_spawn_env` → `ResolvedEnvironment`); all sink wiring and tests are already in place |

Sequencing the guard **before** the reset is deliberate: Stage 0 promotes V4's §6.3 guarantee into tracked authority, so any window where docs claim the boundary and source does not enforce it is an A14 honesty violation. Landing B2a first closes that window without merging a 10,000-line PR.

### 4.2 Critical path and dependency order

```
Gate A (DoR-1 option (a) · DoR-2 activation · DoR-3 Stage 0 approval)
        │
        └─► Stage 0 authority alignment (docs only)  ──► G0 merges
                    │
                    └─► Gate B: DoR-4…DoR-7 satisfied by G0, then DoR-8
                        (distinct source-implementation approval, dated after the merge)
                            │
                            ├─► Stage 1  B1 + B4 fail-closed hardening        (architecture-neutral)
                            │        run_task/client/driver/session/reconcile/storage/admission
                            │
                            ├─► Stage 2  B2a environment-value guard          (architecture-neutral)
                            │        redaction/safe_logging/server/events/event_writer/
                            │        permissions/result/event_store/handlers
                            │        depends on Stage 1 only for merge order, not semantics
                            │
                            └─► Stage 3  boundary reset + B2b + B5 + B6       (indivisible; needs a
                                     second explicit approval to execute retirement)
                                     delete runtime_binding/attestation · new agent_registry ·
                                     shrink profile · rebuild agent_registration · spec/launch
                                     schema · once-only env · api_version 2 · --agents-file ·
                                     agents/run CLI · legacy value-blind inspection
```

Stage 2 has no semantic dependency on Stage 1; the order shown is chosen so `reconcile.py` and `client.py` reach target shape before the reset touches them. If review capacity forces it, Stage 1 may be split into **1a (B1)** and **1b (B4)** — they share only the strict Session-record validation seam in `session.py`, and 1b consumes 1a's version of it. Do not split Stage 2 or Stage 3.

### 4.3 Work packages versus PR gates

A **work package** is a TDD unit: one RED, one minimal GREEN, one focused regression. A **PR gate** is a merge boundary with a full verification ladder, an independent review, and its own approval.

| PR gate | Branch (per `docs/AI_FLOW.md:55-71`) | Work packages | Readiness gate | Releasable `main` after merge? |
|---|---|---|---|---|
| **G0** | `docs/v4-authority-reset` | WP0.1–WP0.5 | Gate A | yes — docs only |
| **G1** | `feat/v4-failclosed-hardening` | WP1.1–WP1.7 | Gate B | yes — strict strengthening, no schema/API/hash movement |
| **G2** | `feat/v4-env-value-guard` | WP2.1–WP2.7 | Gate B | yes — strict confidentiality strengthening |
| **G3** | `feat/v4-boundary-reset` | WP3.1–WP3.12 | Gate B + retirement-execution approval | yes, but the caller wire moves to `api_version` 2 and the daemon flag changes; no cutover is authorized |

One task branch = one task = one PR, started from a clean `origin/main` worktree. No direct commits to `main`.

### 4.4 Mutation and guard boundaries

**Ignored temporary evidence — read-only, never tracked, never copied verbatim:**

- `.cache/ars-architecture-redesign/**` and `.cache/ars-v4-implementation-plan/**` (`.gitignore:127`) — the V4 design, the V4 review, and this plan's independent review are bound inputs. They are never `git add`ed, never edited, and never pasted wholesale into tracked docs. Stage 0's `docs/archive/binding-era-2026-07/` snapshot is authored from **tracked** authority text plus a short retirement rationale, not from the 150 KB ignored design file.
- `.hermes/**` (`.gitignore:388`) — controller workspace. This proposed plan lives there until DoR-2; Stage 0 copies the approved text to `docs/plans/active/2026-07-30-ars-v4-boundary-reset.md`.
- `outputs/`, `.tmp/`, `.agent-run-supervisor/`, `dist/`, `build/`, `src/agent_run_supervisor.egg-info/` — build and runtime scratch. `make verify` creates several of these; none may be committed.

**Tracked candidate files:** only the paths enumerated per stage in §5. Stage every path explicitly (`git add <path>`); never `git add -A` (`docs/AI_FLOW.md:161`).

**Test write discipline:** every new or changed test writes exclusively under pytest `tmp_path`. No test may create a file inside the repository tree, under `$HOME`, or under any real Binding/artifact path. Reconciliation, registry, and guard fixtures build synthetic trees in `tmp_path` only.

### 4.5 Out of the current source candidate

The **repository-backed ARS deploy/restart skill** — a skill stored in this repository and installed by reference — is a separately approved, post-runtime-contract deliverable. It is named here so it is not forgotten and is explicitly **not** part of Stages 0–3, not part of any work package, and not part of any PR gate in this plan.

---

## 5. Stages

Command convention: `uv run pytest -q …` is canonical (it matches `scripts/verify_local.sh:15` and supplies the pinned `agent-client-protocol==0.11.1` from `.venv`). `python3 -m pytest -q …` works for stdlib-only suites because `pyproject.toml:53` sets `pythonpath = ["src", "."]`; SDK-dependent suites (`driver`, `client`, fake-agent L2) require the `native` extra and therefore `uv run`.

**RED discipline for every work package below:** run the new test, capture the failure text, and confirm it names the intended missing behavior (a missing symbol, a wrong count, a serviced callback that should have been refused). A RED caused by an unrelated import error, a collection error, or a fixture typo does not count and must be fixed before writing GREEN.

---

### Stage 0 — Authority alignment (documentation only)

**Goal.** Make V4 the tracked authority chain, retire the Binding-era authority into a cold snapshot, bring both public READMEs onto the V4 boundary, and put the plan board in a state where Gate B can be reached.

**Dependencies.** Gate A complete: DoR-1 (option (a)), DoR-2 (activation), DoR-3 (Stage 0 work approved). Nothing else.

**New human approval required before this stage: YES** — all three Gate A items. Activation alone (DoR-2) does not authorize these edits.

**Paths likely to change**

| Path | Action |
|---|---|
| `GOAL.md` | rewrite product identity to V4 §1.1; contract 1 → the four-way boundary of §5.4; contracts 2–8 survive; **delete contracts 9, 10, 11** and replace with one profile/registry contract; delete the artifact-closure paragraphs and the `/opt` prefix; add §6.5, §8.1, §8.4 language |
| `docs/product/prd.md` | rewrite R1, R4, R11, R12; keep R3 (+ live-advertised option set is the domain authority) and R7 (+ §8.3/§8.4); **delete R13, R14**; add R13 agent registry + preserved command semantics, R14 non-authoritative observed evidence, R15 environment projection and sink non-persistence; rewrite §4 staged delivery and §6 non-goals |
| `docs/design/architecture.md` | §1 context → V4 §2; §3 admission → §5; **delete §3.1–§3.3 wholesale**; §4 → §7; §7 → §6.2/§8; §8 storage → §8.1's two writable surfaces (drop `attestation.json` and all Binding-root prose); §9 → deployment stages; §10 → §12. Keep §2, §5, §6 nearly as-is |
| `docs/design/technical-solution.md` | rewrite §0, the §1.2–§1.4 module tables per V4 §13.2, §2 data model (`ResolvedEnvironment` vs value-blind `EnvProjection`; closed `SessionStartPlan`), §5 ACP flow, §7 credentials and sink guard, §8 storage seam and safe projection types, §9 exhaustive reconciliation, §10 test families, §11 rollback boundaries. **Must state reviewer note 5 explicitly**: workspace/`cwd` binding fields are independently derived authority facts, not environment-value flow, and are deliberately outside the guarded sink list |
| `docs/design/result-event-schema.md` | audit for launch/provenance/attestation fields and any environment value; specify guard-produced dynamic keys/text, categorical withholding metadata, the policy-warning event shape, and legacy text withholding |
| **new** `docs/design/agent-registry.md` | the operator-facing document: schema, grammar, bounds, refusal rules, environment layers/precedence/evidence, startup-read and restart semantics, the `session_epoch` continuity rule (including "adding it for the first time cuts continuity, because absent ≠ 1"), worked placeholder examples |
| `README.md` **and** `README.zh-CN.md` | **both are maintained public documentation and both are required** — V4 §13.1 names neither, but A14 governs every public-facing document and `AGENTS.md:56` requires the two READMEs to move together whenever CLI usage, API, or install/dev instructions change. Both are Binding-shaped today (`README.md:104-109,133-135,169,190-191,220,233-296,343-345`; `README.zh-CN.md:120-155`, which still prescribes `--binding-root`, Binding promotion, and frozen artifact identity). Rewrite the authority table, the `arsd` flags, the operator CLI surface, and the compatibility notes in **both**, keeping them semantically equivalent |
| `docs/roadmap/features.md` | `F-RUNTIME-BINDING-001/002/003` → Retired (superseded); `F-NATIVE-ADAPTER-CODEX-001` → Superseded by an operator-registered adapter command; `F-NATIVE-ADAPTER-CLAUDE-001` → Superseded by the evidenced compat profile; `F-STANDARD-NATIVE-ACP-001` → Superseded by the registry. New rows: agent registry, boundary reset, observed-evidence model, environment-evidence redaction, ordered reconciliation, api_version 2. Keep every evidence cell ≤120 chars |
| `docs/roadmap/current-status.md` | new position; delete the artifact/promotion/re-acceptance gate list; new open gates are V4 §15; `active_plan:` → this plan; correct the stale "In review on branch" lines; state the authority-versus-source delta for the Stage 1→3 window. Keep ≤180 lines and free of 7–40-hex tokens |
| `docs/roadmap/non-approvals.md` | delete the Binding/artifact/registration-era clauses **including lines 69-72**, only after DoR-1 recorded option (a); add V4 §13.1's new clause set (no artifact installation/hosting; no ARS-managed AGENT home; no credential-store inspection; no ARS credential resolution; no environment-value persistence; no attestation/integrity/isolation claim; no remote transport/attach/plugin/container/sandbox/multi-tenancy in v1; no silent replay or fallback; no wire-supplied command/argv/env; no operator-authored or operator-disabled mediation env; no automatic `session_epoch` bump). **Retain the standing clause that an active plan is not approval to land source** — this plan depends on it (DoR-8) |
| **new** `docs/archive/binding-era-2026-07/` | cold snapshot of retired authority — GOAL contracts 9–11, PRD R13/R14, architecture §3.1–§3.3, the archived Binding plans — recorded with *why* it was retired |
| `docs/plans/active/2026-07-29-standard-native-acp-v1.md` | `git mv` → `docs/plans/archive/`; `status: archived`; `archived_at`; completed-scope note |
| **new** `docs/plans/active/2026-07-30-ars-v4-boundary-reset.md` | this plan, activated |
| `docs/INDEX.md`, `docs/lessons/_drift_report.md` | regenerated artifacts |
| `docs/AI_FLOW.md` | unchanged (process document) |

**Ordered work packages**

| WP | Work | Verification |
|---|---|---|
| WP0.1 | Author the cold snapshot `docs/archive/binding-era-2026-07/` from tracked text, with frontmatter (`title`, `status: archived`, `created_at`) | `uv run python tools/build_docs_index.py --write` then `--check` |
| WP0.2 | Rewrite the authority chain in order: `GOAL.md` → `prd.md` → `architecture.md` → `technical-solution.md` → `result-event-schema.md` → new `agent-registry.md` → **`README.md` → `README.zh-CN.md`**. The two READMEs are rewritten in the same work package and must land in the same commit, so neither can describe a different architecture than the other | targeted A14 claim read-through over **every** authority and public-facing document, both READMEs included: no document claims artifact integrity, supply-chain verification, hostile-code isolation, sandboxing, unconditional descendant termination, retroactive legacy erasure, transformed-disclosure prevention, or that no sensitive value reaches the child; and no document still prescribes `--binding-root`, Binding promotion, or frozen artifact identity |
| WP0.3 | Update `features.md`, `current-status.md`, and `non-approvals.md` (DoR-5 order: retirement clause removal happens here, never earlier) | `uv run python tools/check_roadmap_governance.py` |
| WP0.4 | Archive the completed plan; copy in and activate this plan; relink the board | `uv run python tools/check_roadmap_governance.py` |
| WP0.5 | Regenerate the derived docs artifacts | `uv run python tools/build_docs_index.py --check`; `uv run python tools/docs_drift_signal.py --write` then `--check` |

**Constraints and non-approvals for this stage.** No source, test, script, dependency, lockfile, `pyproject.toml`, CI, or workflow change. No version bump, tag, release, or CHANGELOG release-section work. No Binding-root, artifact, `/opt`, registration, credential, deployment, restart, or production action. **Merging this stage does not authorize source implementation** — that is DoR-8 and §11 row 7. Normative registry examples and service templates use placeholders only, so an example can never become a supported-version gate; naming an external AGENT/adapter/provider where compatibility evidence, migration notes, or troubleshooting genuinely require it stays allowed and never becomes a gate.

**Acceptance checklist**

- [ ] DoR-1 recorded as option (a) before any authority edit; `non-approvals.md:69-72` removal is in the same commit as the profile-retirement authority change, never before it
- [ ] No tracked file under `src/`, `tests/`, `scripts/`, `tools/`, or `pyproject.toml`/`uv.lock` appears in the diff
- [ ] **Both `README.md` and `README.zh-CN.md` rewritten, in the same commit, and mutually consistent**: same authority table, same `arsd` flag set, same operator CLI surface, same compatibility notes; neither retains `--binding-root`, Binding promotion, or frozen artifact identity
- [ ] `uv run python tools/check_roadmap_governance.py` → OK; exactly one active plan besides `README.md`
- [ ] `uv run python tools/build_docs_index.py --check` → OK; `uv run python tools/docs_drift_signal.py --check` → OK
- [ ] `uv run python tools/static_safety_scan.py` → `ok: true`
- [ ] `git diff --check` clean
- [ ] Board states the Stage 1→3 authority-vs-source delta in plain words
- [ ] A14 claim read-through recorded across both READMEs and every authority document, including reviewer note 4's operator-visible evidence-loss tradeoff and reviewer note 5's workspace/`cwd` clarification

**Rollback boundary.** Documentation-only; `git revert` of one merge commit restores the Binding-era authority chain exactly, including `non-approvals.md:69-72` and both READMEs. No source, runtime, or deployment state is touched, so revert is complete and side-effect-free.

---

### Stage 1 — Fail-closed reuse and total reconciliation (B1, B4)

**Goal.** Make a reuse request structurally unable to reach `session/new`, reject every conflicting ID-bearing callback at entry before any side effect, and give reconciliation one exhaustive first-match algorithm over classified artifact states.

**Dependencies.** Gate B complete: G0 merged (DoR-4…DoR-7) **and** DoR-8 recorded afterwards. Architecture-neutral otherwise: touches no registry, no profile, no schema version, no hash material, no wire version.

**New human approval required before this stage: YES** — DoR-8, the distinct source-implementation approval (§11 row 7). This is the first stage that edits source; neither plan activation nor the G0 merge authorized it. **Recorded 2026-07-30 after the G0 merge**, together with §11 row 4 (per-stage commit/push after verification, review, and acceptance). PR and merge remain separate, unapproved decisions on top of it.

**Paths likely to change**

| Source | Change |
|---|---|
| `native_acp/run_task.py` | replace `ctx.reuse_load` / `ctx.load_external_id` with the closed `SessionStartPlan` union; reuse opens the Session **existing-only** and calls `validate_native_binding(..., for_load=True)` **before** lease acquisition; `_startup_sequence` pattern-matches with disjoint arms, no default arm, no conversion |
| `native_acp/client.py` | `_observe_session_id` → synchronous fail-closed `_require_session_id`; replace `*args/**kwargs` on all five terminal callbacks and both elicitation callbacks with the pinned SDK signatures |
| `native_acp/driver.py` | accept a typed load plan / exact stored ID; keep `load_session()` returning `None`, expected ID set before the call, options seeded from `LoadSessionResponse.config_options`, and `_check_identity()` as defense in depth |
| `session.py` | `for_load=True` enforced before the lease; add strict record validation used by reconciliation actionability; preserve symmetric equality semantics at `session.py:1070-1077` |
| `arsd/reconcile.py` | full rewrite: bounded no-follow `VALID/ABSENT/CORRUPT` classification of `S`/`L`/`U`; `lstat` over **both** markers; Spec-first / submission-fallback attribution; the eleven-row first-match table; fence → quarantine → progress → terminal ordering |
| `native_acp/storage.py` | add bounded `O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK` + `fstat` readers returning `VALID/ABSENT/CORRUPT`, retaining the existing `NativeTerminalKind` trichotomy (`storage.py:120-159`) |
| `arsd/admission.py` | centralize the strict submission writer/validator shared by admission and reconciliation, including exact run/owner/namespace/Session attribution |

| Tests | Change |
|---|---|
| **new** `tests/native_acp/test_session_start_plan.py` | typed-plan constructors, AST/call-graph reachability, fault injection at every load-arm await, reuse truth table |
| **new** `tests/native_acp/test_callback_identity_boundary.py` | wrong-ID matrix over `session_update`, `request_permission`, `read_text_file`, `write_text_file`, all five terminal calls, and both Session-scoped elicitation forms; pinned-signature conformance; Request-scoped and `complete_elicitation` assert no ID is invented |
| **new** `tests/arsd/reconcile_fixtures.py` | the single factory for all terminal/marker/Spec/launch/submission/Session variants |
| **new** `tests/arsd/test_reconcile_oracle.py` | the generated partition proof and the Session-state product (§7.3) |
| rewrite `tests/arsd/test_reconcile.py` | named regression fixtures for V4 §9.3's resolved ambiguous trees; crash injection at each write boundary; pre-bind daemon test; no-replay call trace |
| extend `tests/native_acp/test_sdk_contract.py` | pin `ElicitationMode` as a four-leaf `Union` and `ElicitationSessionScope.session_id` |
| extend | `tests/native_acp/test_run_task.py`, `tests/native_acp/test_native_session_record.py`, `tests/test_session_store.py`, `tests/arsd/test_admission.py` |

**Exact interfaces where ambiguity would cause a wrong implementation**

```python
SessionStartPlan = NewSessionPlan | LoadSessionPlan

@dataclass(frozen=True)
class NewSessionPlan:
    # Constructible ONLY from a request whose immutable reuse intent is "none".
    ar_session_id: str

@dataclass(frozen=True)
class LoadSessionPlan:
    # Captured exactly from an already-existing Native Session record.
    ar_session_id: str
    external_session_id: str = field(repr=False)
```

Invariants that must hold structurally, not by convention:

1. `driver.new_session` is reachable only from the `NewSessionPlan` match arm.
2. `NewSessionPlan.__init__` is reachable only from the non-reuse admission path.
3. `_startup_sequence` has no default arm and no plan-type conversion.
4. The load arm passes the stored ID with **no** trimming, Unicode normalization, parsing, case conversion, canonicalization, or regeneration, and reads **no** ID from the response — `LoadSessionResponse` has no `session_id` field in `agent-client-protocol==0.11.1`.
5. `_require_session_id(session_id)` ordering: compare → on unbound-or-different, record only the categorical `SESSION_IDENTITY_VIOLATION` and raise `SessionIdentityViolation` with no IDs in its text → only on match may the callback normalize, enqueue, invoke a handler, touch the filesystem, or formulate any response, **including an unsupported-surface response**. No `finally` block may service or persist a rejected callback; delivery counters that unblock shutdown may advance but carry no payload.
6. **Reviewer note 1 (binding):** the elicitation accessor is `mode.session_id`, selected by `isinstance` over the two Session-scoped leaf types `ElicitationFormSessionMode` and `ElicitationUrlSessionMode` (both inherit `ElicitationSessionScope`, `acp/schema.py:280-285,5319,456,5606-5611`). **Never `mode.root.session_id`** — `ElicitationMode` is a plain `Union` and the SDK router passes a leaf instance, so there is no `.root`. A Request-scoped mode carries only `request_id` and is simply unsupported.
7. **Reviewer note 8 (binding):** dispatch is `PRESENT` when `lstat` finds **either** `prompt-dispatch-started` **or** `prompt-accepted` (`run_task.py:74-75`), regardless of contents or file type. Symlink, directory, malformed marker, and any I/O result that cannot prove clean absence are all `PRESENT`. This is a strengthening of `reconcile.py:79`'s single-marker `is_file()`, not a rename.
8. `ABSENT` is reachable only from a clean no-such-path result. Every other present-or-indeterminate state — race, symlink, FIFO, directory, oversize, short read, failed read, error after observed presence — is `CORRUPT`, never a second chance to become absent, and the open never blocks.
9. Attribution priority: `S=VALID` is authoritative and `U` is ignored for attribution even when absent, corrupt, or conflicting; `U=VALID` is a fallback only when `S` is not valid; `launch.json`, `result.json.session_id`, directory names other than the deterministic `f"{run_id}-ephemeral"` derivation, progress, events, locks, and marker contents are never attribution authority.
10. "Actionable" attribution requires an already-existing, strictly readable Native Session record whose session id, owner, and namespace match and whose state is open/active or already quarantined. Reconciliation never creates, reopens, or repairs a Session.

**Ordered work packages**

| WP | RED | GREEN | Expected evidence |
|---|---|---|---|
| WP1.1 | `uv run pytest -q tests/native_acp/test_session_start_plan.py::test_plan_types_are_closed -x` → `ImportError: cannot import name 'LoadSessionPlan'` | add the two frozen dataclasses and the union alias in `run_task.py`; no call-site change yet | RED names the missing type, not an unrelated import |
| WP1.2 | `uv run pytest -q tests/native_acp/test_session_start_plan.py -x` — reuse-with-absent-record and reuse-with-missing-external-ID assert zero `session/new`, zero created Session record, zero lease → currently FAIL (baseline creates a record and calls `session/new`) | rewrite `_bind_session` into existing-only reuse open + `for_load=True` before lease + plan construction; rewrite `_startup_sequence` into disjoint arms | `session/new` spy count 0 on every reuse failure; `SESSION_NOT_FOUND_FOR_REUSE` / `SESSION_RECORD_INVALID` / `SESSION_EXTERNAL_ID_MISSING` / `SESSION_BINDING_MISMATCH` distinct and pre-dispatch |
| WP1.3 | `uv run pytest -q tests/native_acp/test_callback_identity_boundary.py -x` → wrong-ID `create_terminal` currently returns the unsupported response and the varargs signature check fails | `_require_session_id` + pinned SDK signatures for all nine ID-bearing surfaces + the two Session-scoped elicitation leaves | zero downstream handler/sink/filesystem calls on mismatch; request callbacks get no success response; the notification path fails the connection without pretending to reply; matching IDs reach existing behavior unchanged |
| WP1.4 | `uv run pytest -q tests/arsd/test_reconcile.py -x` with new absent-vs-corrupt and both-marker fixtures → currently identical outcomes for missing and unparseable Spec | add the bounded no-follow classifying readers to `storage.py`; add the strict submission validator to `admission.py`; wire `lstat` over both markers | `ABSENT` and `CORRUPT` produce different rows; symlink/FIFO/directory/oversize/read-error all `CORRUPT`; symlink/directory/malformed markers all `PRESENT` |
| WP1.5 | `uv run pytest -q tests/arsd/test_reconcile_oracle.py -x` → no oracle module | implement the eleven-row first-match table, §9.2.1 attribution, actionability, and §9.3.1 write ordering | exactly one row and one vocabulary outcome per case; row counts partition 216 as 54 + 54 + 54 + 27 + 27 |
| WP1.6 | `uv run pytest -q tests/arsd/test_reconcile.py -k crash_injection -x` | make fence → quarantine → progress → terminal idempotent and crash-convergent | trusted terminals byte-identical across reruns; a crash at each boundary resumes the same row; step 4 skipped forever for a trusted-unknown row |
| WP1.7 | `uv run pytest -q tests/arsd/test_reconcile.py -k "no_replay or before_bind" -x` | assert-only hardening | zero registry, ACP, prompt, process, Session-create, lease-acquire, lease-delete calls during reconciliation; the algorithm completes before bind or the socket never listens |

**Constraints and non-approvals.** No schema version, digest version, launch schema, `profile_hash`, `adapter_contract_hash`, or `api_version` movement — every live Session identity field stays byte-identical, so no promoted generation and no existing Session is invalidated by this stage. No registry, profile, or retirement work. No dependency change. No deploy, restart, or cutover.

**Acceptance checklist**

- [x] DoR-8 recorded, dated after the G0 merge, before the first RED
- [ ] A17 fully proven: every reuse intent fails before create/new; successful reuse sends the stored ID unchanged, consumes a real `LoadSessionResponse.config_options`, reads no response ID, prompts with zero `session/new`; every load-arm failure has zero `session/new`
- [ ] Wire capture asserts each serialized `session/load.params.sessionId` decodes to the exact stored string for IDs with leading/trailing whitespace, case distinctions, slashes, and composed/decomposed Unicode
- [ ] A10 fully proven: 216 combinations × Session-state product, exactly one row and outcome each (§7.3)
- [ ] Existing goldens unchanged: `uv run pytest -q tests/native_acp/test_spec.py tests/native_acp/test_profile.py tests/arsd/test_binding_admission.py` all green with no golden edits
- [ ] Full ladder (§8) green, including `make verify`

**Rollback boundary.** One revertable merge commit. Reverting restores baseline reuse and reconciliation behavior with no durable artifact change, because this stage writes no new schema and rewrites no existing terminal. The single non-revertable side effect class is a *durable* quarantine or fence written by the new reconciliation before revert; those are pre-existing, idempotent, irreversible-by-design facts (`no unquarantine tool`) and are correct outcomes under both versions.

---

### Stage 2 — Environment-value sink boundary (B2a)

**Goal.** Install the ephemeral per-Run literal guard and route every child-controlled or exception-controlled ARS text, event, log, error, storage, and API boundary through it, so no projected environment literal echoed by a child can survive in any ARS-owned sink.

**Dependencies.** Stage 1 merged. Semantically independent of Stage 1; ordered after it so `client.py` is already at target shape.

**New human approval required before this stage: NO** new decision — covered by DoR-8 (§11 row 7), which spans Stages 1–3 local source work. Commit/push, PR, and merge remain separate approvals.

**Paths likely to change**

| Source | Change |
|---|---|
| `redaction.py` | add ephemeral `RunTextGuard`, string and `os.fsencode` exec-byte exact matchers, recursive dynamic-key/value guarding, postcondition rescan, whole-field/record suppression, and value-blind reports; existing static patterns remain defense in depth. **Reviewer note 6 (binding):** `redact_argv` currently records the redacted argument itself at `redaction.py:88-93` (`note=str(arg)`); the note must become value-blind |
| **new** `arsd/safe_logging.py` | the mandatory **handler-level** filter, installed before serving and before any diagnostic CLI spawns an ACP child. It guards complete ARS-authored `msg + args`, clears raw `args`/`exc_info`, replaces every dependency/ACP-SDK-originated record in inherited SDK context with a categorical record, and suppresses any Run-tagged record lacking a guard as `UNSANITIZED_RUN_LOG_SUPPRESSED` |
| `arsd/server.py` | install the filter before serving; UDS create/chmod/replace/unlink unchanged as the second writable surface (`server.py:197-202`) |
| `native_acp/run_task.py` | guard final-message ingestion with a rolling `max_literal_chars-1` carry; guard effective/discovery evidence, every failure projection, result/progress/redaction report, and stderr bytes then text |
| `native_acp/client.py` | guard callback payload and error projections |
| `native_acp/driver.py` | on `session/new`, check the returned external ID against the guard **before** assigning `client.expected_session_id`, returning it, or updating the Session record; translate SDK failures to stable codes or guard them |
| `native_acp/events.py`, `event_writer.py`, `permissions.py` | normalize child fields through the guard; `EventWriter` guards again before sequence assignment, in-memory fan-out, and `events.jsonl` append; permission/fs evidence and handler errors guarded |
| `native_acp/storage.py`, `result.py`, `event_store.py` | free-form Run text writes accept a guard-produced safe projection type, so an unguarded `str` is not accepted at those seams |
| `arsd/handlers.py` | live data crosses the guard before fan-out; handlers never return raw exceptions or raw stored objects |
| `managed_process.py` | guard stderr bytes before retained diagnostic output; never format the environment mapping |
| `native_acp/__init__.py` | keep the existing root-**logger** `_RootExceptionDetailRedactor` (`__init__.py:20-67`) as defense in depth beneath the new handler-level filter, which also sees propagated records |
| `tools/static_safety_scan.py` | add the first new rules: no raw environment `repr`, no hash/digest over a value set, no unsafe storage signature |

| Tests | Change |
|---|---|
| **new** `tests/test_run_text_guard.py` | L1: string/byte matchers, longest-first overlap, duplicate removal by direct equality, recursive dynamic keys, key-collision record suppression, postcondition rescan, coarse counters only, and a spy proving no cryptographic or runtime hash of a sensitive value is computed even transiently |
| **new** `tests/native_acp/test_env_value_sinks.py` | L2 echo matrix over every §6.3.3 sink row (§7.4) |
| **new** `tests/arsd/test_safe_logging.py` | Run-tagged unguarded suppression, dependency-record wholesale replacement, and **reviewer note 3 (binding)**: `contextvars` do not cross thread boundaries, so a record originating off-loop must be proven either Run-tagged-and-suppressed or structurally value-free |
| **new** `tests/native_acp/test_workspace_fields_not_guarded.py` | **reviewer note 5 (binding)**: `spec.json`'s canonical root and effective cwd retain their complete literal text and remain covered by `spec_hash`, even when the workspace lives under `$HOME` — they are independently derived authority facts, not environment-value flow |
| extend | `tests/test_redaction.py`, `tests/native_acp/test_sdk_handler_logging_containment.py`, `tests/native_acp/test_event_writer.py`, `tests/native_acp/test_permissions.py`, `tests/test_result_event_schema.py`, `tests/test_event_store.py`, `tests/arsd/test_handlers_registry.py` |

**Exact invariants**

1. `RunTextGuard.from_environment(resolved)` receives every **non-empty** final value after precedence, including base, pass-through, overlay, and mediation values. Empty strings contribute no bytes.
2. The guard keeps its sensitive set in memory only, with `repr=False`, no serializer, no equality/hash implementation, and no diagnostic enumeration. Matching is a bounded direct scan / `startswith` walk — never a regex cache, set/dict key, Bloom filter, digest, or any operation that hashes a sensitive value.
3. The replacement token is a fixed source literal containing no input data. Only coarse sink-local integers may be recorded: matched occurrences, suppressed fields, suppressed records. Original or replaced byte/character lengths are **not** recorded.
4. If safe replacement cannot be established, the boundary suppresses the whole field or record and emits only a stable categorical withholding marker. Confidentiality wins over evidence completeness; no minimum secret length exists and no value is waived for inconvenience.
5. The external Session ID returned by `session/new` **cannot be redacted** because it must later be replayed unchanged. A match therefore yields categorical `SESSION_EXTERNAL_ID_SENSITIVE_COLLISION`, connection teardown, no ID persistence, no callback servicing, no prompt, and no API exposure.
6. stderr/stdout: byte matcher over the joined bounded buffer (or a streaming carry of `max_literal_bytes-1`) **before decode**, text matcher again after decode, then bounded safe retention. Undecodable or unsafe input is replaced wholesale with a categorical marker.
7. The guard stays installed through SDK close, cancellation/join of every inherited task, persistence, logging flush, and final response projection; only then is the context cleared and the carrier dereferenced. This is lifetime minimization, **not** a claim that Python can zero immutable strings.

**Ordered work packages**

| WP | RED | GREEN | Expected evidence |
|---|---|---|---|
| WP2.1 | `uv run pytest -q tests/test_run_text_guard.py -x` → no `RunTextGuard` | implement the guard, both matchers, recursion, suppression, rescan, value-blind report, and the `redact_argv` note fix | overlapping values matched longest-first then rescanned; colliding guarded keys suppress the enclosing record; no length-by-value recorded |
| WP2.2 | `uv run pytest -q tests/native_acp/test_env_value_sinks.py -k "final_message or events" -x` → sentinel survives in `result.json` and `events.jsonl` | guard final-message ingestion with the rolling carry and guard at both the enqueue and `EventWriter` boundaries | a value deliberately split across chunks is caught before accumulation; `EventWriter` is the last common boundary, not the only one |
| WP2.3 | `… -k "permission or filesystem or effective or stderr" -x` | guard permission/fs evidence, `effective.json`, initialize/discovery evidence, and stderr bytes-then-text | no sentinel in any file under the Run tree |
| WP2.4 | `uv run pytest -q tests/arsd/test_safe_logging.py -x` | new `arsd/safe_logging.py` installed at handler level in `arsd/server.py` and around `agents doctor`'s successor path | every dependency/SDK record in Run context replaced wholesale; every Run-tagged unguarded record suppressed categorically; the off-loop case proven |
| WP2.5 | `… -k "exception or spawn_error or cleanup_error" -x` | translate known failures to stable codes; guard the safe projection otherwise; outer daemon and CLI boundaries replace unhandled exceptions with stable codes | no raw `repr`, args, frame bytes, traceback locals, or environment mapping emitted anywhere |
| WP2.6 | `… -k "external_session_id_collision" -x` | implement the refusal inside the driver before `expected_session_id` assignment | no persistence, no callback servicing, no prompt, no API exposure; categorical code only |
| WP2.7 | `uv run pytest -q tests/native_acp/test_workspace_fields_not_guarded.py -x` | assert-only; add the storage safe-projection types so unguarded `str` is rejected at free-form seams | workspace/`cwd` fields intact and hash-covered; reviewer note 5 encoded as a test, not a comment |

**Constraints and non-approvals.** No schema, digest, launch-schema, or `api_version` movement in this stage — the *structural* value-blindness of launch material and the legacy value-blind inspection branch belong to Stage 3 (B2b), because they depend on the new launch schema. No registry, profile, or retirement work. No dependency change. No new endpoint, transport, or security control.

**Acceptance checklist**

- [ ] Every §6.3.3 sink row has at least one passing L2 echo test (§7.4)
- [ ] L1 proves no hash of a sensitive value is computed even transiently
- [ ] Static scan additions green: no raw environment `repr`, no value-set hash, no unsafe storage signature
- [ ] Existing evidence goldens either unchanged or changed only by documented categorical withholding markers
- [ ] Full ladder (§8) green, including `make verify`

**Rollback boundary.** One revertable merge commit. Reverting restores baseline redaction. Durable evidence written while the guard was active keeps its categorical withholding markers; those records remain readable and schema-valid under the reverted code, so revert is safe and does not corrupt history.

---

### Stage 3 — The boundary reset (B2b, B5, B6, profile retirement)

**Goal.** Land the V4 architecture: operator TOML registry read once at startup, two source profiles, value-blind sealed launch material with once-only environment resolution, `api_version` 2 with the eight-operation drain matrix, `--agents-file`, the `agents`/`run inspect` operator surface, and legacy value-blind inspection.

**Dependencies.** Stages 0, 1, 2 merged.

**New human approval required before this stage: YES.** DoR-1 recorded the *policy* decision and Stage 0 landed the *authority* change; `docs/roadmap/non-approvals.md:69-72` treats introducing a retirement capability and using it as two separate decisions. Deleting three registered profiles from source is the second act and requires its own explicit confirmation (§11 row 8) before WP3.3 runs. DoR-8 alone does not cover it.

**Paths likely to change**

| Source | Change |
|---|---|
| **delete** `native_acp/runtime_binding.py` | 2,322 lines; the entire Binding layer |
| **delete** `native_acp/attestation.py` | 1,023 lines |
| **new** `native_acp/agent_registry.py` | strict `tomllib` parse, bounded validation, typed `REGISTRY_*`/`ENTRY_*`/`MEDIATION_KEY_COLLISION` refusals, one read at daemon startup, immutable snapshot, zero per-Run filesystem access, and the config-hygiene check (resolve symlinks; require a regular file that is not group- or world-writable) |
| `native_acp/profile.py` | shrink hard from 1,428 lines to `AcpCompatProfile` + `AgentInstance` + a two-entry registry (`standard-native-acp-v1`, `claude-agent-acp-compat-v1`) + the mediation table and its global `RESERVED_MEDIATION_KEYS`; remove `_REGISTERED_EXECUTABLES`, `WrappedRuntimeArtifacts`, `BindingSlot`, `SLOT_DESCRIPTOR_FIELDS`, `VersionProbeRule`-as-gate, `launch_kind`, `ARTIFACT_MATERIALIZATION_PREFIX`, `NODE_NO_GLOBAL_SEARCH_PATHS`, and every per-agent value domain |
| `native_acp/agent_registration.py` | rebuild around V4 §3: gains `command`/`args`/`env_passthrough`/`env_overlay`/`mediation`/selector hints/`forbidden_capabilities`/`session_epoch`; loses `contract_identity`, `adapter_contract_hash`, `registration_hash`-as-freeze, `acp_agent_name`, value domains, provenance receipts. Stays pure — no filesystem access |
| `native_acp/spec.py` | launch snapshot replaces `ResolvedLaunchSpec`; introduce the non-serializable `ResolvedEnvironment` and the durable value-blind `EnvProjection`; delete `SealedRuntimeIdentity`, `RuntimeProvenance`, `seal_runtime_identity`, `seal_runtime_provenance`; simplify `RunSpecAssembler`; `ObservedRuntime` extends `EffectiveRunState`; new `SPEC_SCHEMA_VERSION`, `DIGEST_SCHEMA_VERSION`, and launch-schema versions. `launch_spec_hash` on the Spec is **retained and load-bearing** (`spec.py:466`). `expected_binding_hash` disposition is follow-up F1 and is **not** silently changed |
| `native_acp/run_task.py` | remove attestation and `exec_fd`; keep the Spec-then-launch write order (`run_task.py:570-575`); replace `_spawn_env` with once-only `ResolvedEnvironment` + guard construction at step 11; `agentInfo` name/version become evidence, retiring the gates at `run_task.py:757-772`; **no endpoint indirection**; markers, timeout, cancel, kill/reap, and terminal meanings unchanged |
| `managed_process.py` | drop `interpreter_fd` / `/proc/self/fd` exec (`managed_process.py:182,201-204`) so declared `command`/`argv[0]` survive; accept `ResolvedEnvironment` only at the spawn seam; preserve child-exec errno through `ManagedProcessError` so `ENOENT → COMMAND_NOT_FOUND`, `EACCES → COMMAND_NOT_EXECUTABLE`, other → `SPAWN_FAILED` are classified without embedding raw exception text |
| `native_acp/config_fidelity.py` | domains come from live discovery; drop source-domain preflight |
| `native_acp/driver.py` | selector ids come from the instance; source-domain preflight dropped |
| `session.py` | identity gate becomes V4 §7.1 (`agent_id`, profile identity, owner, namespace, `workspace_hash`, optional operator `session_epoch`); delete `adapter_contract_hash`, ARS-derived `session_compatibility_epoch`, and `agent_registration_hash` as identity; preserve symmetric equality; legacy identity fields stay status-readable and **load-refused** |
| `arsd/admission.py` | `resolve_runtime_binding` → snapshot `resolve_agent_entry` (pure in-memory, zero filesystem); digest material becomes value-blind; extend `FORBIDDEN_RUNTIME_SELECTION_FIELDS` (`admission.py:33-43`) |
| `arsd/protocol.py`, `handlers.py`, `arsd/client.py` | `ARSD_API_VERSION = 2`, `SUPPORTED_API_VERSIONS = (1, 2)` during the window; request drops `profile_id`, requires `agent_id`; `server_info` reports `supported_api_versions`. **Reviewer note 7 (binding):** the version check moves **off the envelope decoder** (`protocol.py:253-262`) and onto per-operation dispatch, so `submit` is refused at v1 while the other seven are accepted; the separate shutdown drain (`server.py:288-292,364-368`) still refuses every frame including `server_info` with `SHUTTING_DOWN` |
| `arsd/__main__.py`, `arsd/service_unit.py` | `--binding-root` → `--agents-file`, required in daemon mode and when rendering a unit; startup order registry-parse → reconcile → bind, same fail-closed discipline |
| `arsd/operand.py` | **plan deviation from V4 §13.2, stated openly:** V4 marks this module "unchanged", but `capture_binding_root` (`operand.py:102-126`) is the shape-checking capture behind both `--binding-root` doors. The *rule* is unchanged and must not be duplicated; the *entry* is re-pointed to the agents file and its docstring's Binding references corrected. This is the resolution of follow-up F5 — the entry names a real module and the intent is that it remains the single operand-admission seam |
| `cli.py`, `commands.py` | delete the `runtime-binding` group (`cli.py:59,63-157,295-298`); add `agents validate`, `agents doctor`, `run inspect`; install the Stage 2 guard/log boundary around `agents doctor`; implement value-blind legacy inspection with **no** `_recompute_launch_hash` call on a legacy schema (`commands.py:588-599`) |
| `native_acp/__init__.py` | drop exports of the deleted modules |
| `tools/static_safety_scan.py` | final rule set: no deployment facts in `src/` (A4), no endpoint/transport/attach/remote token (A12), no acpx runtime path (reinforcing `test_no_acpx_coupling.py`) |
| `README.md`, `README.zh-CN.md`, `docs/design/agent-registry.md`, `docs/roadmap/current-status.md`, `docs/roadmap/features.md` | reconcile the Stage 0 text with the landed source position. **Both READMEs move together and in the same commit** (`AGENTS.md:56`): the CLI surface actually changes here (`runtime-binding` → `agents validate`/`agents doctor`/`run inspect`, `--binding-root` → `--agents-file`), so the post-implementation documentation sync obligation is triggered for both languages |

| Tests | Change |
|---|---|
| **delete** | `tests/native_acp/test_runtime_binding.py` (3,166), `tests/native_acp/test_attestation.py` (1,518), `tests/arsd/test_binding_admission.py` (376), `tests/test_runtime_binding_cli.py` (757), `tests/native_acp/test_standard_native_acp_v1.py` (587), `tests/native_acp/binding_fixtures.py` (518) — V4 §14's retired families |
| **new** | `tests/native_acp/registry_fixtures.py`; `tests/native_acp/test_agent_registry.py`; `tests/native_acp/test_env_projection.py`; `tests/native_acp/test_command_semantics.py`; `tests/native_acp/test_observation_evidence.py`; `tests/arsd/test_api_version_matrix.py`; `tests/test_agents_cli.py`; `tests/test_reset_boundary_scans.py` |
| **rewrite** | `tests/native_acp/test_profile.py`, `test_spec.py`, `test_agent_registration.py`, `test_session_epoch.py` (operator epoch, no auto-bump), `test_no_agent_name_coupling.py` (strengthened for A4); `tests/arsd/test_admission.py`, `test_protocol.py`, `test_service_unit.py`, `test_client_daemon.py`, `test_handlers_registry.py`; `tests/test_cli_commands.py` |
| **re-point, do not delete** | `tests/arsd/test_codex_acceptance_contract.py` (617) and `tests/arsd/test_codex_socket_acceptance.py` (1,870). **Named because V4 §13.2 does not:** these opt-in L3 suites are tied to the retired `codex-acp-1.1.7` profile. Retire only their attestation/credential-root/artifact tamper legs (the V4 §14 retired families) and re-point the real-agent ACP legs — `session/new`, real `session/load`, exact config fidelity, denied-action canary — onto a registry-entry fixture against `standard-native-acp-v1`. The existing "derived profile with `CODEX_HOME` → ephemeral home" becomes an `env_overlay` in a test registry file, which the new model expresses natively. Deleting the coverage instead would silently drop the only real-agent continuity evidence |

**Exact interfaces where ambiguity would cause a wrong implementation**

Registry field set — complete and closed, nothing else: `profile` (required), `command` (required), `args`, `mediation`, `env_passthrough`, `env_overlay`, `model_selector`, `effort_selector`, `forbidden_capabilities`, `session_epoch`. `schema_version` exactly the supported value. Bounds exactly as V4 §3.2: `agent_id` matches `[a-z0-9][a-z0-9._-]{0,63}`; `command` non-empty, ≤4096 bytes, no NUL, either absolute or a single basename with no path separator; `args` ≤32 tokens each ≤1024 bytes, no NUL, **passed as an argv list, never through a shell**; `env_passthrough` ≤32 names matching `[A-Za-z_][A-Za-z0-9_]*`; `env_overlay` ≤32 pairs with values ≤4096 printable bytes; `forbidden_capabilities` ≤16 bounded names; `session_epoch` a positive integer; file ≤1 MiB. Unknown key at any level refused. `transport`, `secret_refs`, `version_probe`, `registered_models`, `allowed_efforts`, selector value domains, `default_model`, `default_effort`, expected `agentInfo` fields, and any digest/path/tree-hash/ownership/mode expectation are absent by construction. **No secret-shaped-name heuristic exists** — V2's `*TOKEN*`/`*SECRET*`/`*PASSWORD*`/`*API_KEY*` refusal is deleted as unsound in both directions.

Environment resolution, once, in memory, at step 11 — before sealing and before spawn:

```python
resolved = resolve_environment(
    arsd_env=admission_environment_snapshot,   # copied once at step 11
    base_names=profile.base_allowlist,         # layer 1
    passthrough_names=entry.env_passthrough,   # layer 2
    overlay=entry.env_overlay,                 # layer 3
    mediation=source_mediation_pairs(entry.mediation),   # layer 4, applied LAST
)
guard = RunTextGuard.from_environment(resolved)
launch_env = resolved.value_blind_projection()
managed_process.start(argv=argv, env=resolved.exec_mapping)
```

`ResolvedEnvironment` is ephemeral and non-serializable: its value mapping is `repr=False`, excluded from equality and hashing, exposes no `to_dict`, and is accepted **only** by the process-spawn seam and `RunTextGuard.from_environment`. `EnvProjection` is the separate durable, value-blind name/source/precedence shape. `SSH_AUTH_SOCK` is deliberately **not** in the layer-1 base set — forwarding it is an explicit per-agent `env_passthrough` opt-in.

Mediation authority: a closed source table maps `mediation_id → frozen ((KEY, VALUE), …)`. `RESERVED_MEDIATION_KEYS` is the union of every key in **any** registered binding, not only the selected one. A collision in `env_overlay` or `env_passthrough` fails the registry parse with `MEDIATION_KEY_COLLISION` and **the daemon refuses to listen**; `agents validate` applies the identical check offline. A profile-registry construction invariant asserts the base allowlist and the reserved key set are disjoint. There is no `mediation = off` and no per-entry key/value form. Mediation is applied last anyway, as defense in depth, and both properties are tested independently.

Startup order, strictly sequential and fail-closed at every step: parse `--agents-file` once → reconcile → bind the UDS `0600` inside `0700` and accept. The registry is never opened again for the daemon's whole lifetime.

**Ordered work packages — one PR, twelve internal checkpoints**

| WP | RED | GREEN | Expected evidence |
|---|---|---|---|
| WP3.1 | `uv run pytest -q tests/native_acp/test_agent_registry.py -x` → no module | `agent_registry.py`: strict parse, bounds, typed refusals, immutable snapshot, config hygiene | every refusal names a stable rule; a world-writable or non-regular resolved target is refused; a dotfiles symlink below `$HOME` works |
| WP3.2 | `… -k "mediation_collision or reserved_keys_global or precedence" -x` | reserved-key union, parse-time collision refusal, layer-4-last precedence | A6 (i)–(iv): startup refusal with the named rule; offline `agents validate` refusal; with the collision check disabled in a harness the source value still survives; disjointness invariant |
| WP3.3 | `uv run pytest -q tests/native_acp/test_profile.py -x` → registry still holds four profiles | **requires the §11 row 8 retirement-execution confirmation, recorded before this checkpoint.** Shrink `profile.py` to two profiles; rebuild `agent_registration.py` | exactly two registered profiles; `claude-agent-acp-compat-v1` keeps the frozen `_meta` on both session calls and the required `mode` selector proven by readback; `-v1` refuses a contract whose frozen major disagrees |
| WP3.4 | `uv run pytest -q tests/native_acp/test_env_projection.py -x` | `ResolvedEnvironment` + `EnvProjection` + once-only resolution replacing `_spawn_env` | A16's structural half: value carrier non-serializable and rejected by every Spec/launch/event/result/log/exception/API serializer; `resolved_count`, `names[]`, `declared_absent[]` present; **no** value, digest, keyed digest, length, prefix, suffix, equality token, or matcher table in hash material; the exec mapping stays byte-identical after ambient `os.environ` mutation |
| WP3.5 | `uv run pytest -q tests/native_acp/test_command_semantics.py -x` → `interpreter_fd` path still available | drop `interpreter_fd`/`/proc/self/fd` exec; preserve errno | A2: bare `command` → `argv[0]` equals the declared string exactly; the image is located through the child's projected `PATH`; a registered shim and a symlink both work; structural scan asserts no `executable=` override and no `/proc/self/fd` exec. Shim-interpreter `$0` rewriting stays follow-up F4 |
| WP3.6 | `uv run pytest -q tests/arsd/test_api_version_matrix.py -x` → all v1 ops accepted at the envelope | move the version check to per-operation dispatch; `ARSD_API_VERSION = 2`; request drops `profile_id`, requires `agent_id` | A15: all eight ops exercised at `api_version: 1` — `submit` refused with `UNSUPPORTED_API_VERSION`, the other seven accepted, `server_info` reporting `supported_api_versions` including 2; the shutdown drain separately refuses every frame with `SHUTTING_DOWN` |
| WP3.7 | `uv run pytest -q tests/arsd/test_admission.py -k "snapshot or zero_filesystem" -x` | `resolve_agent_entry` against the startup snapshot; value-blind digest; extended forbidden-selection fields | A13: exactly one registry open per daemon lifetime; zero opens during admission, spawn, finalization, and reconciliation; a mid-serve registry edit has no effect until restart. A5: no request field names a command, argv token, env key/value, path, digest, version, or secret, and `agent_id` passes its grammar before any resolution |
| WP3.8 | `uv run pytest -q tests/native_acp/test_session_epoch.py -x` | Session identity → V4 §7.1; delete the three retired identity fields; legacy fields load-refused, status-readable | no automatic epoch bump anywhere; a scan proves no code path derives, increments, or infers an epoch from an observation, digest, version, or file bytes; absent ≠ 1 asymmetry is documented and tested |
| WP3.9 | `uv run pytest -q tests/native_acp/test_observation_evidence.py -x` → `agentInfo` still gates | retire the `run_task.py:757-772` gates to evidence; add the policy-warning event | A11: a Run succeeds after `agentInfo.name` and advertised capabilities change between two Runs of one Session, emitting a recorded warning and no refusal; the only observation-based refusals are protocol major, required/forbidden capabilities, exact config readback, and the compat profile's required mode |
| WP3.10 | `uv run pytest -q tests/arsd/test_service_unit.py -k agents_file -x` | `--binding-root` → `--agents-file`; startup order parse → reconcile → bind | daemon and unit rendering both refuse without `--agents-file`; the registry parse and reconciliation both complete before bind or the socket never listens |
| WP3.11 | `uv run pytest -q tests/test_agents_cli.py -x` | delete the `runtime-binding` group; add `agents validate`, `agents doctor`, `run inspect` with legacy value-blind inspection | schema classified **before** the verifier is selected; a monkeypatched `_recompute_launch_hash` that raises proves the legacy branch never calls it; legacy responses set `legacy_value_bearing=true`, `environment_values_withheld=true`, `launch_seal_verification="not_performed_value_bearing_legacy"`, and withhold environment fields, raw documents, embedded seal material, and free-form text as `LEGACY_TEXT_EVIDENCE_WITHHELD`; no `promote`, no `rollback`, no `--force` |
| WP3.12 | `uv run pytest -q tests/test_reset_boundary_scans.py -x` | delete `runtime_binding.py` and `attestation.py`; finalize the static scan rule set; re-point the two Codex L3 suites; reconcile both READMEs and the board with the landed source | A4: no `src/` file contains an absolute path to, digest of, or version of any external agent, adapter, or interpreter. A12: one `ManagedProcess` per Run from spawn to reap; no endpoint/transport/remote/attach key, field, branch, or dependency; `transport` refused as an unknown registry key. A3: the in-process write interceptor sees only `<supervisor_root>` and the UDS runtime path; a child that mutates its own `HOME`/cache/config/Session state completes normally; registry-below-`$HOME`-via-symlink and command-below-`$HOME`-via-PATH-shim both work read-only |

**Constraints and non-approvals.** No `/opt`, Binding-root, artifact, registration, or credential action. No deploy, restart, rollout, migration, or cutover. No version bump, tag, release, GitHub Release, PyPI, or CHANGELOG release-section work. No caller cutover — the in-repo `arsd/client.py` moves to v2, and external callers keep seven of eight operations at v1 during the drain window. No dependency, lockfile, or `pyproject.toml` change: `tomllib` is stdlib. No acpx removal work; `tests/native_acp/test_no_acpx_coupling.py` stays green. No new endpoint, feature flag, compatibility shim, or speculative security control. Follow-ups F1–F5 stay non-blocking: F1's `expected_binding_hash` is left explicitly undisposed rather than silently changed, and F5 is resolved as a rename-only correction with the rule unchanged.

**Acceptance checklist**

- [ ] §11 row 8 retirement-execution confirmation recorded before WP3.3
- [ ] A1: two Runs across a fake-agent version change behind one unchanged registered command, with a restart-count assertion, plus a mid-serve registry-edit no-effect
- [ ] A2, A3, A4, A5, A6, A7, A11, A12, A13, A15 all green with the tests named above
- [ ] A16 complete: the structural half from WP3.4 plus Stage 2's dynamic half, including value-bearing legacy Run and Session fixtures that return no value, no external ID, no free text, and no value-bearing seal, and invoke no hash function
- [ ] `native_acp/runtime_binding.py` and `native_acp/attestation.py` absent from the tree; no import of either remains
- [ ] Exactly two registered profiles; `docs/roadmap/features.md`, the board, **and both `README.md` and `README.zh-CN.md`** reflect the landed position, with the two READMEs mutually consistent and updated in the same commit (`AGENTS.md:56`)
- [ ] `uv lock --check` green; `uv run python tools/check_version_sync.py` green; `pyproject.toml` and `uv.lock` unchanged in the diff
- [ ] Full ladder (§8) green, including `make verify`

**Rollback boundary.** One revertable merge commit. Reverting restores the Binding line in source; `/opt` artifact trees and Binding roots were never written by this stage and are therefore intact. Sessions created under the reset line are automatically refused by a reverted runtime, because `validate_native_binding` compares epoch and contract hash by symmetric equality (`session.py:1070-1077`) and a reset-line record carries neither. Rollback is fail-closed in both directions with no new mechanism, no dual-write, no dual-read, no shim, and no alias. Terminal Run facts and sealed launch records are immutable across a revert in either direction.

---

## 6. Source-impact ledger

Every module named in V4 §13.2 appears exactly once as **primary ownership**. Primary = the stage that owns the module's structural shape; other touching stages are listed as consumers.

| Module | V4 §13.2 disposition | Primary stage | Cross-stage consumers | Notes |
|---|---|---|---|---|
| `native_acp/runtime_binding.py` | **delete** | S3 (WP3.12) | — | 2,322 lines; replaced by `agent_registry.py` |
| `native_acp/agent_registry.py` | **new** | S3 (WP3.1–3.2) | — | one startup read, immutable snapshot, no per-Run I/O |
| `native_acp/attestation.py` | **delete** | S3 (WP3.12) | — | 1,023 lines |
| `native_acp/profile.py` | **shrink hard** | S3 (WP3.3) | — | needs the §11 row 8 retirement-execution confirmation |
| `native_acp/agent_registration.py` | **rebuild** | S3 (WP3.3) | — | stays pure; no filesystem access |
| `native_acp/spec.py` | change | S3 (WP3.4) | — | `launch_spec_hash` retained and load-bearing; `expected_binding_hash` = F1 |
| `native_acp/run_task.py` | change | **S1** (WP1.1–1.2, control-flow shape) | S2 (WP2.2–2.5 sinks), S3 (WP3.4 once-only env, WP3.9 evidence gates, WP3.12 attestation removal) | most distributed change in the tree |
| `managed_process.py` | change | S3 (WP3.5) | S2 (WP2.3 stderr bytes) | drop `interpreter_fd`; preserve errno |
| `native_acp/driver.py` | change | **S1** (WP1.2, load-arm structure) | S2 (WP2.6 new-session ID check), S3 (selector ids from instance) | `load_session()` keeps returning `None` |
| `native_acp/client.py` | changed | **S1** (WP1.3) | S2 (WP2.5 payload/error projections) | pinned SDK signatures; `mode.session_id` by `isinstance` |
| `native_acp/config_fidelity.py` | change | S3 (WP3.6 live domains) | S2 (value-blind exceptions) | — |
| `redaction.py` | changed | **S2** (WP2.1) | — | includes the `redact_argv` note fix (RN6) |
| `native_acp/events.py`, `event_writer.py`, `permissions.py` | changed | **S2** (WP2.2–2.3) | — | `EventWriter` = last common boundary |
| `native_acp/storage.py` | changed | **S1** (WP1.4 classifying readers) | S2 (safe-projection types), S3 (new-schema readers/allowlists) | terminal trichotomy retained |
| `result.py`, `event_store.py` | changed | **S2** (WP2.3, WP2.7) | — | free-form writes require safe projections |
| `arsd/admission.py` | change | **S1** (WP1.4 strict submission writer/validator) | S3 (WP3.7 `resolve_agent_entry`, value-blind digest, forbidden fields) | — |
| `arsd/protocol.py`, `handlers.py`, `arsd/client.py` | change | S3 (WP3.6) | S2 (WP2.3, WP2.5 guarded projections) | version check moves to dispatch (RN7) |
| `arsd/reconcile.py` | changed | **S1** (WP1.4–1.7) | — | full rewrite; zero S3 delta expected |
| `arsd/__main__.py`, `arsd/service_unit.py` | change | S3 (WP3.10) | — | `--agents-file`; parse → reconcile → bind |
| `arsd/server.py` + **new** `arsd/safe_logging.py` | change / new | **S2** (WP2.4) | S3 (startup-order call site) | handler-level filter, installed before serving |
| `arsd/operand.py` | V4: unchanged | S3 (WP3.10) | — | **plan deviation, stated:** rename-only re-point of `capture_binding_root`; rule unchanged; resolves F5 |
| `cli.py`, `commands.py` | change | S3 (WP3.11) | S2 (guard boundary around `agents doctor`'s successor) | classify-schema-before-verifier |
| `session.py` | change | **S1** (WP1.2 `for_load` + strict record validation) | S2 (WP2.6 external-ID certification), S3 (WP3.8 identity field set) | symmetric equality preserved |
| `process_liveness.py`, `exit_classifier.py` | unchanged | — | — | asserted unchanged in every stage diff |
| acpx-era modules (`runner.py`, `parser.py`, `session_runtime.py`, `caller.py`, `hermes_caller/`, …) | out of scope | — | — | separately authorized removal unaffected |
| **additions not in V4 §13.2, surfaced from source inspection** | | | | |
| `native_acp/__init__.py` | change | S3 (WP3.12) | S2 (root-logger filter retained beneath the handler-level filter) | drop deleted-module exports |
| **`README.md` and `README.zh-CN.md`** | change (**both**, always together) | **S0** (WP0.2) | S3 (WP3.12 reconcile with landed source, same commit) | A14 governs every public-facing document and both are maintained public READMEs; `AGENTS.md:56` requires them to move together on any CLI/API/install change — which Stage 3 makes (`runtime-binding` → `agents`/`run inspect`, `--binding-root` → `--agents-file`). `README.zh-CN.md:120-155` still prescribes `--binding-root`, Binding promotion, and frozen artifact identity, so omitting it would leave A14 unprovable |
| `tools/static_safety_scan.py` | change | S3 (WP3.12 final rule set) | S2 (WP2.1 first new rules) | A4/A12/A16 scan rules |
| `tests/native_acp/test_no_agent_name_coupling.py` | change | S3 (WP3.12) | — | strengthen for A4 after the reset |
| `tests/arsd/test_codex_acceptance_contract.py`, `test_codex_socket_acceptance.py` | re-point, not delete | S3 (WP3.12) | — | retire attestation/credential legs; keep real-agent ACP legs on a registry-entry fixture |
| `docs/design/result-event-schema.md`, `docs/INDEX.md`, `docs/lessons/_drift_report.md` | change / regenerated | **S0** (WP0.1, WP0.5) | every stage regenerates the derived artifacts | — |
| `pyproject.toml`, `uv.lock` | **unchanged** | — | — | asserted by `uv lock --check` + `check_version_sync.py` in every stage |
| Future repository-backed deploy/restart skill | out of scope | — | — | separately approved, post-runtime-contract, installed by reference |

---

## 7. Acceptance traceability

### 7.1 Review blockers B1–B6

| Blocker | Stage | Gate tests |
|---|---|---|
| **B1** fail-closed load-only reuse + callback identity | S1 | `test_session_start_plan.py` (constructors, AST call-graph, fault injection at every load-arm await, reuse truth table) · `test_callback_identity_boundary.py` (nine ID-bearing surfaces + two Session-scoped elicitation leaves) · `test_sdk_contract.py` (pinned signatures + `ElicitationMode` leaves) · wire capture of `session/load.params.sessionId` |
| **B2** no environment value in any ARS sink | S2 (dynamic) + S3 (structural, legacy) | `test_run_text_guard.py` · `test_env_value_sinks.py` · `test_safe_logging.py` · `test_workspace_fields_not_guarded.py` · `test_env_projection.py` · `test_agents_cli.py` legacy branch |
| **B3** filesystem boundary | S3 | A3 legs in `test_reset_boundary_scans.py`: in-process write interceptor, AGENT-state-management negative scan, and the two positive Runs (registry below `$HOME` via symlink; command below `$HOME` via PATH shim; child mutating its own `HOME`/cache/config/Session state) |
| **B4** total ordered reconciliation | S1 | `test_reconcile_oracle.py` + rewritten `test_reconcile.py` + `reconcile_fixtures.py` |
| **B5** eight-operation drain matrix | S3 | `test_api_version_matrix.py` |
| **B6** one startup-read TOML registry | S3 | `test_agent_registry.py` + A13 open counters in `test_admission.py` / `test_client_daemon.py` |

### 7.2 Acceptance criteria A1–A17

| # | Stage | Primary test(s) |
|---|---|---|
| A1 | S3 | `test_client_daemon.py` two-Run version-change + restart-count; mid-serve registry-edit no-effect |
| A2 | S3 | `test_command_semantics.py` (argv[0], symlink, shell shim, structural no-`executable=`/no-`/proc/self/fd`). F4 refinement noted inline |
| A3 | S3 | `test_reset_boundary_scans.py` (interceptor + negative scan + two positive Runs) |
| A4 | S3 | `test_reset_boundary_scans.py` + `tools/static_safety_scan.py` |
| A5 | S3 | `test_admission.py` dataclass structural + grammar-ordering tests |
| A6 | S3 | `test_agent_registry.py` collision matrix + profile-registry construction invariant + startup-refusal test |
| A7 | S3 | `test_driver_config_fidelity.py` fake-agent matrix (extended for live domains) |
| A8 | later | L3 denied-action canary, mandatory per registered agent; opt-in, never in CI. Prerequisite recording is F3 |
| A9 | S1 | `test_run_task.py` fault injection + `test_reconcile.py` crash legs |
| A10 | S1 | `test_reconcile_oracle.py` (§7.3) |
| A11 | S3 | `test_observation_evidence.py` + structural scan |
| A12 | S3 | `test_reset_boundary_scans.py` + registry unknown-key test asserting `transport` is refused |
| A13 | S3 | `test_agent_registry.py` + instrumented open counters across a full daemon lifecycle |
| A14 | S0 (+ S3 reconcile) | documentation review gate + targeted claim scan over **every authority document and both public READMEs (`README.md` and `README.zh-CN.md`)**; both must be free of artifact-integrity, supply-chain, isolation, sandboxing, unconditional-termination, retroactive-erasure, and transformed-disclosure claims, and free of `--binding-root`/Binding-promotion/frozen-artifact-identity prescriptions; no blanket lexical ban |
| A15 | S3 | `test_api_version_matrix.py` + legacy record fixtures in both directions + shutdown-drain test |
| A16 | S2 + S3 | full sink inventory (§7.4) |
| A17 | S1 | `test_session_start_plan.py` + `test_callback_identity_boundary.py` |

### 7.3 The 216 × Session-state reconciliation matrix (A10, reviewer note 2)

`T × D × S × L × U` = `4 × 2 × 3 × 3 × 3` = **216** artifact combinations. Row selection for rows 2/3, 4, and 5/6 additionally depends on Session-record state through "actionable `A`", so the oracle is properly a **216 × Session-state product**. It is realized in three parts, all in `tests/arsd/test_reconcile_oracle.py` over `tests/arsd/reconcile_fixtures.py`:

| Part | Shape | Asserts |
|---|---|---|
| **P1 — full literal product** | 216 artifact combinations × the 9 Session states from V4 §9.3.2 (matching valid/open, missing, corrupt, closed, owner mismatch, namespace mismatch, id mismatch, already fenced, already quarantined) = **1,944 cases**, driven as one in-process loop over a single `tmp_path` root with one subdirectory per case, so wall time stays bounded | exactly one matching row and exactly one permitted vocabulary outcome (`authoritative terminal` \| `unknown + quarantine` \| `pre-dispatch failed/reusable` \| `refuse to listen`) for every case |
| **P2 — parametrized partition proof** | 216 combinations × {actionable, non-actionable} = **432 pytest params**, for readable per-case failures and row-count arithmetic | row 1 = 54, rows 2–3 = 54, row 4 = 54, rows 5–6 = 27, rows 7–11 = 27; pre-dispatch splits exactly (S=VALID 6+3; S=CORRUPT 9; S=ABSENT 6+3) |
| **P3 — actionability predicate + composition** | the 9 Session states × the three identity sources (Spec-valid, submission-fallback, neither) | actionable ⇔ an already-existing, strictly readable record whose id/owner/namespace match and whose state is open/active or already quarantined; and the predicate is the **only** Session-derived input to row selection for the 135 artifact combinations where it is load-bearing |

Plus, in the rewritten `tests/arsd/test_reconcile.py`: named regression fixtures for each resolved ambiguous tree (trusted `unknown` + unattributable → row 3; valid Spec + corrupt submission + no marker → row 7; valid Spec + corrupt launch + no marker → row 8; dispatch + corrupt Spec + valid submission + matching Session → row 5; dispatch + valid Spec + corrupt submission or launch → row 5; all-absent + corrupt submission → row 11 refusal); crash injection after fence, quarantine, progress, and terminal write proving convergence and byte-level terminal immutability; a call-trace test asserting zero registry, ACP, prompt, process, Session-create, lease-acquire, and lease-delete calls; and a daemon test asserting the algorithm completes before bind or the socket never listens.

### 7.4 Environment-value sink inventory (A16) — every sink, no exceptions

| # | Sink (V4 §6.3.3) | Boundary required | Stage | Test |
|---|---|---|---|---|
| 1 | ACP final/agent/thought text + final-message accumulator | guard on ingestion with a rolling `max_literal_chars-1` carry; retain no unguarded chunk beyond that carry; guard the assembled message again before `result.json` | S2 | `test_env_value_sinks.py::final_message*` incl. a split-across-chunks fixture |
| 2 | normalized updates + lifecycle/tool/config/permission evidence | guard all dynamic keys and strings before `EventWriter.enqueue`; `EventWriter` guards again before sequence assignment, fan-out, and append | S2 | `…::events*`, `test_event_writer.py` |
| 3 | permission and filesystem evidence | guard child `tool_call_id`, kind, reason, path/content summaries, option fields, handler exceptions, denial diagnostics | S2 | `…::permission*`, `…::filesystem*`, `test_permissions.py` |
| 4 | `effective.json` + initialize/discovery evidence | guard `agentInfo`, capability/config structures, selector ids, observations, and every child-supplied string before storage | S2 | `…::effective*` |
| 5 | external Session ID from `session/new` | cannot be redacted; **refuse** categorically before `expected_session_id`, persistence, callbacks, prompt, or API exposure | S2 | `…::external_session_id_collision` |
| 6 | stderr/stdout diagnostic capture | byte matcher over the joined bounded buffer (or a streaming `max_literal_bytes-1` carry) **before decode**; text matcher after decode; undecodable/unsafe input replaced wholesale | S2 | `…::stderr_bytes*`, `…::stderr_text*` |
| 7 | `result.json`, `progress.json`, redaction report, terminal/failure detail | guard before the storage call; terminal codes stay stable and value-blind | S2 | `…::result*`, `test_result_event_schema.py` |
| 8 | spawn, ACP, callback, timeout, cleanup, SDK exceptions | stable codes for known failures; guarded safe projection otherwise; daemon and CLI outer boundaries replace unhandled exceptions | S2 | `…::exception*`, `…::spawn_error`, `…::cleanup_error` |
| 9 | daemon and SDK logging | handler-level filter; complete preformatted `msg + args` guarded; raw `args`/`exc_info` cleared; **every** dependency/SDK record in Run context replaced wholesale; Run-tagged unguarded → `UNSANITIZED_RUN_LOG_SUPPRESSED` (RN3: off-loop/off-thread proven) | S2 | `test_safe_logging.py`, `test_sdk_handler_logging_containment.py` |
| 10 | live `run_events`, terminal response, status/list, `run inspect`, other API projections | live data crosses the guard before fan-out; completed new-schema data read only from guarded stores through an explicit response-field allowlist; handlers never return raw exceptions | S2 + S3 | `test_handlers_registry.py`, `test_agents_cli.py` |
| 11 | startup/registry validation (pre-guard by construction) | structurally value-blind: refusals name a stable rule and at most a field path or environment **name**; successful `agents validate` prints only entry ids, counts, names, source classes, rule outcomes | S3 | `test_agent_registry.py`, `test_agents_cli.py` |
| 12 | structured launch/spec/hash material (§6.3.1) | names + source class + precedence + redaction status only; no value, digest, keyed digest, length, prefix, suffix, equality token, or matcher table in any hash input; `fixed_env`/`permission_env` rejected by a schema-level allowlist | S3 | `test_env_projection.py`, `test_spec.py` goldens |
| 13 | legacy value-bearing records (§6.3.4) | classify schema **before** selecting a verifier; categorical allowlist; withhold environment fields, raw documents, value-bearing seals, external `agent_session_id`, and free-form text; never call `_recompute_launch_hash` | S3 | `test_agents_cli.py` with a monkeypatched hash function that raises |
| — | **not a sink (RN5)** — workspace canonical root and effective `cwd` | deliberately **not** guarded: independently derived authority facts, hash-covered, required by workspace binding, reconciliation attribution, and audit | S2 | `test_workspace_fields_not_guarded.py` |

Sentinel discipline for every row: unique per-fixture sentinels drawn from all four source classes, including short, overlapping, Unicode, JSON-metacharacter, non-ASCII exec-byte, and deliberately split-across-chunk shapes; assertions scan the **complete** new Run/Session tree, a hash-input spy, live and completed API/inspect output, captured logs/tracebacks/errors, and the suppression report. Independently derived public facts with identical bytes are not treated as value-derived flow — taint-directed call paths, not lexical coincidence, prove the boundary.

---

## 8. Verification ladder

Run in this order at every internal checkpoint and in full at every PR gate.

```bash
# 1. focused — the work package's own tests (SDK-dependent suites need uv)
uv run pytest -q tests/native_acp/test_session_start_plan.py -x
uv run pytest -q tests/arsd/test_reconcile_oracle.py -x
python3 -m pytest -q tests/test_run_text_guard.py -x        # stdlib-only suites

# 2. relevant suites
uv run pytest -q tests/native_acp
uv run pytest -q tests/arsd
uv run pytest -q tests/test_cli_commands.py tests/test_redaction.py tests/test_session_store.py

# 3. compile / import smoke
uv run python -m compileall -q src scripts tests
uv run python -c "import agent_run_supervisor.native_acp, agent_run_supervisor.arsd"

# 4. docs generation and drift
uv run python tools/build_docs_index.py --check
uv run python tools/docs_drift_signal.py --check
uv run python tools/check_roadmap_governance.py

# 5. static, secret, and boundary scans
uv run python tools/static_safety_scan.py
uv run python tools/check_version_sync.py
uv run pytest -q tests/test_static_safety_scan.py tests/native_acp/test_no_acpx_coupling.py \
                 tests/native_acp/test_no_agent_name_coupling.py tests/test_reset_boundary_scans.py

# 6. full gate (identical to CI)
make verify           # = uv sync --locked --extra dev --extra release --extra native, then:
./scripts/verify_local.sh

# 7. whitespace
git diff --check
```

Notes that matter in practice:

- `pyproject.toml:50-53` sets `testpaths = ["tests"]`, `addopts = "-q"`, `pythonpath = ["src", "."]`, so `python3 -m pytest -q` resolves the package from source. Suites importing `acp` need the `native` extra and therefore `uv run`.
- `make verify` runs `uv lock --check` first (`verify_local.sh:9`). Any accidental dependency edit fails there, which is the intended tripwire for constraint 7.
- `tools/docs_drift_signal.py` builds its report from `git log --since=…` over `docs/lessons/` and `docs/practices/`, so `--check` can go stale with the passage of time alone. If it fails, regenerate with `--write` and commit the regenerated report; do not edit it by hand.
- `tools/check_roadmap_governance.py` enforces: board ≤180 lines, no 7–40-hex tokens in the board, ≤5 board PR references, no `*.md` in `docs/plans/` root, ≤3 active plans, features evidence cells ≤120 chars, and `status: archived` in every archived plan. **Keep the baseline commit hash out of `docs/roadmap/current-status.md`** — it belongs in this plan's frontmatter, which is not scanned.
- Documentation sync is a gate, not tail work (`AGENTS.md:48-64`): whenever a gate changes CLI usage, library API, or install/dev instructions, `README.md` **and** `README.zh-CN.md` must be updated in that same gate, and `tools/build_docs_index.py --write` plus `tools/docs_drift_signal.py --write` re-run afterwards.
- `verify_local.sh` builds into `dist/`, `build/`, and `src/agent_run_supervisor.egg-info/` and runs the installed-wheel smoke. Those paths are ignored and must never be staged.
- Additional per-gate scans, asserted as tests rather than shell greps so they cannot be skipped: **no new endpoint** (no `transport`/`endpoint`/`attach`/`remote` key, field, branch, or dependency), **no acpx runtime** (no acpx import, invocation, or session-store read from `native_acp/` or `arsd/`), **no deployment facts** (no absolute external-agent path, digest, or version in `src/`), **no secret** (existing `SECRET_PATTERNS` plus the new value-set rules).

---

## 9. Migration and compatibility boundary

### 9.1 What this plan changes and what it does not

**Source compatibility, in scope:** `api_version` 2 with the eight-operation drain matrix; `AgentRunRequest` v2 requiring `agent_id` and dropping `profile_id`; new `SPEC_SCHEMA_VERSION`, `DIGEST_SCHEMA_VERSION`, and launch-schema versions, because the digest material genuinely changes and environment values leave the launch snapshot; legacy value-blind inspection of pre-reset records; legacy Session records refused for `session/load` with a stable code while staying owner-scoped `status`/`list`/`close`-readable through the value-blind projection.

**Explicitly not in scope, in any stage:** no runtime cutover, no service restart, no live-traffic move, no `/opt` or Binding-root deletion (they simply stop being referenced), no retroactive erasure of old bytes, no dual-write, no dual-read, no shim, no alias, and no silent fallback in either direction.

### 9.2 Operator-visible changes that must be documented at Stage 0 — in both READMEs and the operator docs

Seven, not five — V4 §12.3 lists five, reviewer note 4 adds the sixth, and reviewer note 5's clarification is the seventh:

1. the AGENT project-config workspace refusal disappears (V4 §4.5);
2. operators must author `env_passthrough` / `env_overlay` for anything the layer-1 base set does not cover — `PATH` is the single most likely cause of "works in my shell, fails under ARS", and `SSH_AUTH_SOCK` is deliberately opt-in;
3. new `launch.json` records carry names, source classes, and precedence only; legacy value-bearing and free-form inspection is withheld;
4. a registry edit takes effect at the **next daemon start**, not the next Run — while an agent upgrade behind an unchanged registered command costs nothing at all;
5. adding `session_epoch` to an entry for the first time cuts that agent's existing Sessions, because absent ≠ 1;
6. **reviewer note 4** — short, common layer-1 base values (`TERM`, `LANG`, `TZ`, `USER`, `HOME`, `PATH` elements) are in the guard's literal set, so guarding them will erase substantial evidence from Run text. Confidentiality wins over evidence completeness; there is no minimum secret length and no inconvenience waiver. This tradeoff belongs in the cutover list beside the other five;
7. **reviewer note 5** — workspace canonical root and effective `cwd` remain complete literals in `spec.json` and remain hash-covered. They are independently derived authority facts, not environment-value flow, and are deliberately outside the guarded sink list.

### 9.3 Later human gates, unchanged by any source work here

- **Decision 2 — cutover and the one-time legacy-Session load refusal.** Every live Session at cutover ends; continuing that work means a new Session with caller-owned context handoff. Recommendation: (a) accept the loss at a scheduled cutover, or (b) delay until Sessions drain naturally. (c) building a legacy-identity compatibility path requires the new runtime to model identities it deliberately deleted. **Does not block source implementation:** Stage 3 implements the fail-closed refusal; when to move live traffic is decided later.
- **Decision 3 — legacy line lifetime.** (a) cut over and freeze `v0.5.x` as maintenance-only, (b) run both lines for a period, (c) keep `v0.5.x` indefinitely. Recommendation (a). **Does not block source implementation:** it governs release and branch policy, which is a separate approval anyway.
- **Deploy sequence, for reference only and authorized by none of this plan:** package upgrade → operator authors the registry → `agents validate` → `agents doctor` per agent → mandatory denied-action canary per agent → re-render the service unit (`--agents-file` replaces `--binding-root`) → restart `arsd` → registry parse → reconcile-only → accept new submits. Restarts recur only when the registry itself changes.
- **Rollback:** stop new submits → revert the package → the old daemon starts with its `--binding-root` and its promoted generations intact, because nothing in this plan wrote to them.

---

## 10. Risk and stop-loss register

Concrete risks only. Each carries a stop-loss: the condition under which the correct response is to stop and return to architecture rather than point-patch.

| # | Risk | Likelihood | Mitigation | Stop-loss |
|---|---|---|---|---|
| R1 | **Guarding common base values erases so much Run evidence that operators cannot diagnose failures.** `PATH`, `HOME`, `TERM` elements are all in the literal set | high (certain to be visible) | document it as cutover change #6 in both READMEs and the operator docs; coarse suppression counters make erasure measurable rather than invisible | If operators cannot triage a failed Run at all, that is an **evidence-model** question (which names belong in layer 1, whether a source class may be exempt), not a guard bug. Stop and take it back to architecture; do not add per-value length hints, prefixes, or "short value" exemptions, which V4 §6.3.2 forbids |
| R2 | **New reconciliation refusals turn a startable daemon into a non-startable one.** Rows 8/9/10 refuse to listen for corrupt Spec, corrupt referenced launch, and launch-without-Spec — states baseline tolerated | medium | this is fail-closed by design; document it in the board and the deploy sequence; `reconcile_fixtures.py` gives operators a reproduction path | If real-world Run trees routinely hit these rows, the **classification** may be wrong (e.g. a legitimate write interleaving misread as corrupt). Stop; do not add a "tolerate once" escape hatch or a repair path — reconciliation never repairs |
| R3 | **Stage 3's diff is large enough that review quality drops.** ~3,300 source lines and ~6,900 test lines deleted, plus substantial new code | high | twelve internal checkpoints, each with its own RED→GREEN and a focused-suite gate; the pre-landed Stages 1–2 remove `reconcile.py` and the sink wiring from the diff entirely | If reviewers cannot form an opinion on the whole, the answer is **more checkpoints and a longer review**, not splitting into PRs that leave `main` unable to admit a Run |
| R4 | **`contextvars` gaps let a value-bearing log record escape off-loop.** Executor and watcher threads do not inherit the Run context (RN3) | medium | the categorical `UNSANITIZED_RUN_LOG_SUPPRESSED` backstop for any Run-tagged unguarded record; `ManagedProcess` never formats the mapping; `test_safe_logging.py` proves the off-loop case | If a value-bearing record can originate both **off-loop and untagged**, the log-tagging model is insufficient. Stop; do not chase individual call sites |
| R5 | **Retiring `codex-acp-1.1.7` silently drops the only real-agent continuity evidence** if the two opt-in L3 suites are deleted rather than re-pointed | medium | WP3.12 re-points the real-agent ACP legs onto a registry-entry fixture and retires only the attestation/credential/artifact legs | If the ACP legs cannot be expressed against a registry entry, the registry's expressiveness is short of the acceptance model. Stop and re-open §3/§4 rather than reintroducing a per-agent profile |
| R6 | **Authority-versus-source drift window** between Stage 0 and Stage 3: tracked docs and both public READMEs describe the registry while source still has Bindings | certain (structural) | Stage 0 states the delta explicitly on the board; `features.md` rows read "Superseded (source retirement pending)" until Stage 3 lands; both READMEs carry the same one-sentence delta note so neither language over-claims | If the window has to stay open longer than the Stage 1–3 sequence, revisit the sequence, not the honesty of the docs |
| R7 | **A registry read-count regression** reintroduces per-Run filesystem access through a helper or a lazily-evaluated property | low | A13 instrumented open counters across a full daemon lifecycle, run at every gate | A second read is a **contract violation**, not a performance bug. Stop and fix the seam, never cache around it |
| R8 | **`expected_binding_hash` (F1) is touched by accident** while moving digest material | medium | WP3.4's golden tests pin its presence and value contribution explicitly; the field stays undisposed by design | If digest material cannot move without disposing it, F1 has become blocking. Stop and take the disposition decision explicitly rather than deciding it in a diff |
| R9 | **A rolled-back runtime accepts a reset-line Session** because symmetric equality was weakened | low | `session.py:1070-1077` symmetric equality is preserved and tested in both directions | Any asymmetry here breaks fail-closed rollback. Stop; this is architecture, not a test fix |
| R10 | **Decision 1 returns as (b) or (c) after work has begun** | low | §2.3 makes (b)/(c) a stop condition; DoR-1 must be recorded before any Stage 0 edit, and Gate B cannot be reached without a merged Stage 0 | (b) or (c) **withdraws this plan** at whatever point it arrives. Revert the merged stages by their stated rollback boundaries and author a different, explicitly approved plan. Do not retrofit a "keep-but-unreachable" mode into this one |

**Non-blocking follow-ups stay non-blocking.** F1–F5 are carried in this plan's constraints and in R8. None is a gate; none may be converted into a new architectural requirement mid-implementation; none blocks any stage.

---

## 11. Approval matrix

Every row is a separate, narrow, non-transitive decision. **Approval of any row transfers approval to no other row.** In particular, row 1 (plan activation) authorizes nothing beyond the planning status, and row 6 (merging G0) does not authorize row 7.

| # | Action | Status | Gate |
|---|---|---|---|
| 1 | **Plan activation** — sets `status: proposed` → `active` and updates the board link, and **nothing else**. Authorized no source, test, documentation, Git, GitHub, or runtime action | **approved 2026-07-30** | decision owner (DoR-2); requires row 2 |
| 2 | **Decision 1 — profile-retirement policy.** Must be recorded as option **(a)**. Recording (b) or (c) withdraws this plan (§2.3) | **approved 2026-07-30** as option (a) — policy only | decision owner (DoR-1); blocks all of Stage 0 |
| 3 | **Stage 0 documentation-work execution** (branch `docs/v4-authority-reset`, WP0.1–WP0.5, both READMEs included) | **approved 2026-07-30**; executed and merged | decision owner (DoR-3); requires rows 1 + 2 |
| 4 | **Commit and push**, per branch, every gate | **approved 2026-07-30**, per stage, and only after that stage candidate passes verification, independent review, and acceptance | controller; one branch = one task; carries no PR or merge authority |
| 5 | **PR creation**, per gate | **not approved** | controller |
| 6 | **Merge**, per gate | **not approved** | controller; green CI + independent review |
| 7 | **Source implementation** (Stages 1–3 local work on task branches) — a **distinct explicit approval**, recordable only **after** G0 merges, flipping `implementation_authorized` to `true`. Not implied by rows 1, 2, 3, or 6 | **approved 2026-07-30**, dated after the G0 merge; local Stages 1–3 source/test/status work only, serial at the stage gates | decision owner (DoR-8); mirrors `docs/roadmap/non-approvals.md:52` |
| 8 | **Profile-retirement execution in source** (deleting three registered profiles, WP3.3) | **not approved** | separate explicit confirmation on top of rows 2 and 7; `non-approvals.md`'s two-decisions rule |
| 9 | **Version bump, tag, GitHub Release, PyPI, CHANGELOG release section** | **not approved** | separate release decision |
| 10 | **Deployment / configuration write** (registry file authoring, caller mappings, unit re-render) | **not approved** | operator |
| 11 | **Service restart** (`arsd`) | **not approved** | operator; requires draining in-flight Runs |
| 12 | **Migration / cutover** — decision 2, including the one-time legacy-Session load refusal | **not approved** | operator |
| 13 | **Legacy `v0.5.x` line retirement** — decision 3 | **not approved** | decision owner |
| 14 | **`/opt` artifact tree and Binding-root deletion** | **not approved** | operator; a later decision this plan never performs |
| 15 | **Per-agent denied-action canary against a real agent** (A8) | **not approved** | operator; mandatory before that agent's use; prerequisite recording is F3 |
| 16 | **Future repository-backed ARS deploy/restart skill** | **not approved** | separate post-runtime-contract deliverable; stored in the repository, installed by reference; no implementation in this plan's source candidate |
| 17 | **Sachima / Gateway / caller cutover / acpx removal** | **not approved** | out of scope entirely |

---

## 12. Execution handoff

### 12.1 First implementation assignment after approval

**Assignment name:** Stage 0 — V4 authority alignment (documentation only).

**Preconditions, all verified before the first edit:** DoR-1 recorded in writing as **option (a)** (an approval naming (b) or (c) stops here — see §2.3 and R10); DoR-2 activation recorded, with `implementation_authorized` still `false`; DoR-3 Stage 0 work approved (§11 row 3); `origin/main` clean at the then-current head; a fresh task worktree on branch `docs/v4-authority-reset`.

**Scope:** exactly WP0.1–WP0.5 and exactly the paths in Stage 0's table — which **includes both `README.md` and `README.zh-CN.md`**. Zero changes under `src/`, `tests/`, `scripts/`, `tools/`, `pyproject.toml`, or `uv.lock`.

**Exit condition:** Stage 0's acceptance checklist fully green, including both READMEs rewritten in the same commit and mutually consistent, `tools/check_roadmap_governance.py`, `tools/build_docs_index.py --check`, `tools/docs_drift_signal.py --check`, `tools/static_safety_scan.py`, and `git diff --check`; the A14 claim read-through recorded across both READMEs and every authority document; and the board stating the Stage 1→3 authority-versus-source delta.

**Then, after G0 merges — and only after DoR-8 (§11 row 7) is separately recorded, dated after that merge:** Stage 1 (`feat/v4-failclosed-hardening`), starting at WP1.1's RED. Merging G0 is not itself permission to edit source.

### 12.2 Intended role and one-writer rule

- **Architect** (this role) owns the plan text and any plan revision. It does not implement.
- **Implementer** owns source and test edits inside one stage, on one branch, one task, one PR.
- **One writer per branch at a time.** No concurrent agent or human writes to the same worktree, branch, or file set. A handoff is a merge or an explicit branch transfer, never an overlap.
- **Controller (Hermes)** owns scope, deterministic gates, evidence arbitration, and every push/PR/merge/runtime side effect (`docs/AI_FLOW.md:131-137`). The implementer never performs a Git or GitHub mutation on its own authority.

### 12.3 Independent review and acceptance

- Each PR gate requires an **independent, fresh-context blocker review** covering GOAL/PRD/design/features/board/active-plan alignment, not only whether tests pass (`docs/AI_FLOW.md:131-137`).
- **The V4 architecture waiver does not carry.** `review-v4.md:110` binds the one-time independence waiver to design hash `419dad53…` and authorizes no implementation or delivery action. Implementation reviews need their own independence decision, taken per gate.
- **No ceremonial quotas.** One competent independent reviewer per gate is the bar; a second is warranted only where a gate's evidence is genuinely contested.
- **Gate G0** additionally requires a reviewer to confirm that both public READMEs landed together and that no authority or public-facing document still prescribes the retired architecture (A14).
- **Gate G3** additionally requires the profile-retirement execution confirmation (§11 row 8) to be recorded *before* WP3.3, and a reviewer explicitly confirming that no retirement *mechanism* was introduced — V4 retires profiles by deleting them, not by adding a capability to disable them.
- **Evidence each PR must carry** (`docs/AI_FLOW.md:138-150`): summary; source-of-truth docs touched; feature-tracker/roadmap impact; test plan with commands and results, including the RED failure text per work package; review evidence; secret-safety statement; and an explicit boundary statement against `docs/roadmap/non-approvals.md`.

---

## Authorization boundary

**This plan authorizes only what §11 records as approved.** The plan text itself approves nothing: authority comes from the recorded rows. As of 2026-07-30 those are rows 1, 2, 3 (Stage 0), 4 (per-stage commit/push after verification, review, and acceptance), and 7 (local Stages 1–3 source implementation). Every other row stands unapproved, so this plan still performs and approves no PR, merge, or tag, no dependency or lockfile change, no service install/enable/restart, no deployment, configuration write, migration, or cutover, no release or publication, no `/opt`, Binding-root, or artifact-tree action, no registration change, no credential access or management, no profile retirement, and no other production action.

**Activation is not implementation authorization.** Setting `status: active` recorded only that this is the board-linked planning artifact. Source and test work additionally required the distinct approval of §11 row 7 (DoR-8), recordable only after the Stage 0 gate merged — recorded 2026-07-30 after that merge. Deleting the three registered profiles still additionally requires §11 row 8; and PR/merge, release, deployment/config, service restart, migration/cutover, `/opt` and Binding-root deletion, the real-agent canary, and every production action each remain separate, non-transitive, unapproved decisions.

**This plan is conditional on V4 §15 decision 1 option (a).** An approval recording option (b) or (c) does not make it Ready and does not satisfy Stage 0 readiness — it withdraws this plan and requires a different, explicitly approved plan. The other two human decisions remain open and untaken: **production cutover with its one-time legacy-Session load refusal**, and **legacy-line lifetime**. `profile_retirement_approved: false` and `production_authorized: false` in this plan's frontmatter are statements of fact, not placeholders, and `implementation_authorized: true` is likewise a statement of fact about §11 row 7 alone.

Baseline `79f30aeb255e6507afd001ef2a4bf190f54e42a3` (`v0.5.3`) remains clean `main`. The V4 design, the V4 review, and this plan's independent review were read only, from Git-ignored paths, and were not modified or copied into any tracked file. Production `v0.5.3` and the live `/opt` and Binding trees remain untouched migration source. Nothing in this session modified any file, Git state, branch, worktree, service, runtime, Binding or artifact tree, registration, credential, or production surface.
