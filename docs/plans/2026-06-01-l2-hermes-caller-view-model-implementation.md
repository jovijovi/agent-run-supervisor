---
title: "L2 Hermes Caller + Offline Feishu View-Model Implementation Plan"
status: active
created_at: 2026-06-01
last_validated_at: 2026-06-01T00:00:00+0800
---
# L2 Hermes Caller + Offline Feishu View-Model Implementation Plan

> **Scope banner — plan-only.** This is the **L2 implementation plan** for a later,
> separately-approved coding phase that would build the **concrete Hermes caller** and an
> **offline Feishu rich-card view-model adapter** *above* the generic I1 boundary
> (`src/agent_run_supervisor/caller.py`). It **implements no runtime code in this PR**, changes
> no runtime module, CLI, config, or `caller.py`, and grants **no** new live/runtime approval.
> The Feishu document-check task remains a **scenario / presentation target only**: the planned
> code is **fake/local/offline only** — there is **no** real Feishu API, **no** IM delivery, **no**
> platform ingress, **no** Gateway/Sachima live behavior, and **no** automatic replies anywhere
> in this plan. Authoring or merging this plan does **not** approve execution. Execution requires
> a **separate explicit approval** (§17). All standing non-approvals
> (`docs/roadmap/current-status.md` §5) remain in force. This plan derives from the **closed L1
> design** (`docs/plans/2026-06-01-l1-concrete-caller-integration-design.md`) and may not
> redefine product scope.

## 1. Goal

Define, at execution-plan level only, **how a later coding phase would implement** the concrete
local caller (**Hermes**) and its **offline Feishu rich-card view-model adapter** on top of the
existing generic I1 caller boundary, covering **both** `exec` and `persistent session` modes for
the document-check scenario, so that:

- the supervisor stays **generic and unchanged** (no platform field enters
  `CallerInvocationSpec` / `CallerResult`; `business_verdict` stays `null`);
- the **business verdict** and **view-model + card rendering** stay **caller-owned**;
- everything is **fake/local/offline** — no real Feishu API, IM delivery, ingress, Gateway, or
  Sachima behavior;
- the work is **conservative stdlib-only** and lives in a **caller-side package** that depends on
  the I1 public surface only.

This plan turns the L1 design's adapters (intake/role adapter and presentation adapter) into a
concrete file list, dataclass/function signatures, and a TDD task breakdown — without writing the
code in this PR.

## 2. Source-of-truth trace

Read in authority order; this plan is **derivative** of all of them and redefines none.

- Product positioning: `GOAL.md` ("what this project owns" vs "what caller projects own").
- Product requirements: `docs/product/prd.md` — §2 (Hermes named as a primary AI-assisted
  caller), §3 (supervisor-not-business-judge), FR-4 (exec), FR-5 (persistent sessions), FR-6
  (normalized events), FR-7 (status ≠ verdict), FR-9 (generic local caller boundary / I1), §6
  (non-goals).
- System architecture: `docs/design/architecture.md` — §1/§1.1 (caller vs supervisor split;
  "AI-assisted controller e.g. Hermes"), §3 (exec lifecycle), §4 (session lifecycle), §6.2
  (non-approvals, incl. trusted Markdown/HTML rendering and Sachima behavior).
- Technical solution: `docs/design/technical-solution.md` — §3.10 (`caller.py`
  responsibilities), §8 (thin integration boundary).
- Caller-stable schema: `docs/design/result-event-schema.md` — §1 (run `result.json`), §2.1
  (session turn `result.json`), §2.2 (create/status/close projections), §2.5
  (`CallerResult.to_dict`), §3 (statuses/error codes), §4 (normalized event families), §8
  (`business_verdict` always `null`).
- Feature tracker: `docs/roadmap/features.md` — `F-INTEGRATION-001` (I1 Done; Hermes designed
  design-only in L1), `F-SESSION-001`, `F-NONGOAL-001`.
- Roadmap/status: `docs/roadmap/current-status.md` — §3 (I1, L1), §4
  (`ARS-CALLER-INTEGRATION`), §5 (non-approvals).
- Workflow & plan rules: `docs/AI_FLOW.md`, `docs/plans/README.md`, `CLAUDE.md` / `AGENTS.md`.
- **Closed L1 design (direct parent):**
  `docs/plans/2026-06-01-l1-concrete-caller-integration-design.md` (Hermes layering, exec +
  session flows, view-model mapping §12, ownership matrix §13, Sachima seam §14).
- I1 boundary surface (the only supervisor API this plan consumes):
  `src/agent_run_supervisor/caller.py` (`CallerInvocationSpec`, `CallerResult`, `invoke_caller`).
- I1 boundary plan: `docs/plans/2026-06-01-i1-local-caller-thin-integration.md`.

## 3. Scope

In scope (this PR = **plan document only**):

- A concrete, file-level implementation plan for the **Hermes caller** and the **offline Feishu
  view-model adapter** that a later approved phase would build above I1.
- Proposed **exact implementation files**, all stdlib-only, in a **caller-side package** separate
  from `agent_run_supervisor`.
- **Dataclass/function signatures** as code snippets (design sketches, not committed code).
- **TDD task breakdown** with explicit RED/GREEN/REFACTOR steps.
- **Exec flow** and **persistent-session flow** implementation shape.
- **Offline Feishu view-model mapping** (normalized event/result → view-model → card payload
  dict, with no delivery).
- **Fake/local fixture strategy** (fake runner/session-runtime injection + synthetic events).
- **Static forbidden-surface gates** to prove the planned code adds no platform/live surface.
- **Docs update steps** for the later implementation PR, **rollback**, **PR/review process**, and
  **next approval text**.

Explicitly **out of scope** for L2 (this PR), unchanged from current non-approvals:

- Any runtime code, tests, CLI commands, config, fixtures, or edits to `caller.py` /
  `SupervisorRunner` / `SessionRuntime` / parser internals **in this PR**.
- Real Feishu API calls, card delivery, message posting, or any IM delivery (in this plan **and**
  in the later implementation it describes).
- Platform ingress / webhook receipt / public endpoints.
- Sachima behavior integration, Gateway lifecycle, automatic replies, live/default-on behavior,
  `@all`, agent-to-agent / worker auto-routing.
- Adding any platform identifier (channel/card/message/webhook/recipient/Gateway/delivery state)
  to `CallerResult` or the generic supervisor API.
- Moving the business verdict into the supervisor.
- Trusted Markdown/HTML rendering of agent output.
- Editing generated `docs/INDEX.md` or `docs/lessons/_drift_report.md` by hand.

## 4. Non-goals / non-approvals

L2 inherits **every** non-approval in `docs/roadmap/current-status.md` §5 and PRD §6 verbatim and
adds nothing live. Authoring this plan does **not** approve or imply:

- Sachima behavior integration or real AGENT automatic replies;
- public ingress, real IM delivery, or any Feishu API / card delivery;
- Gateway restart/reload/replace or production config writes;
- live/default-on behavior, `@all` fanout, worker / agent-to-agent auto-routing;
- participant persistence or management UI;
- trusted Markdown/HTML rendering of untrusted agent output;
- treating `allowed_roots` as an OS/filesystem sandbox;
- per-run human approval as the default authorization model (authorization stays role-bound to
  `role_id`).

The planned Hermes caller and Feishu view-model adapter are **fake/local/offline only**. The
Feishu card is a **render-target view-model**, never a delivered message. Crossing the Sachima
seam (L1 §14) remains a **separate later approved phase**.

## 5. Proposed implementation files (later code; stdlib-only, caller-side)

The later implementation phase would add a **new caller-side subpackage** inside the existing distribution (`agent_run_supervisor.hermes_caller`) that depends **only** on
the I1 public surface (`agent_run_supervisor.caller`). It is packaged under `src/agent_run_supervisor/` so the current `pyproject.toml` include rule (`agent_run_supervisor*`) picks it up, but it is logically outside the supervisor core and must **not** import supervisor internals
(`runner`, `session_runtime`, `parser`, `policy`, `session`, …) directly, and the supervisor must
**not** import it. Static import guards enforce the L1 layering even though the subpackage ships in the same distribution.

Proposed layout (illustrative; final names confirmed at implementation time):

```text
src/agent_run_supervisor/hermes_caller/
  __init__.py            # package marker; exports the public Hermes caller surface
  task.py                # caller-side document-check task descriptor (intake input)
  intake.py              # intake/role adapter: task -> CallerInvocationSpec (no platform fields)
  verdict.py             # caller-owned business verdict derivation from CallerResult
  events.py              # caller-side read-only view over persisted normalized events (evidence)
  view_model.py          # progress/result view-model construction (presentation, no delivery)
  feishu_adapter.py      # OFFLINE view-model -> Feishu card payload dict (escaped; NO delivery)
  hermes.py              # orchestration: exec + persistent-session document-check flows

tests/hermes_caller/
  conftest.py                       # fake runner / fake session-runtime fixtures
  fixtures/                         # synthetic CallerResult + normalized-event JSON (redacted)
  test_intake.py                    # intake/role adapter unit tests
  test_verdict.py                   # verdict derivation unit tests
  test_view_model.py                # event/result -> view-model mapping tests
  test_feishu_adapter.py            # offline card payload + escaping + no-delivery tests
  test_hermes_exec_flow.py          # end-to-end exec flow with injected fakes
  test_hermes_session_flow.py       # end-to-end persistent-session flow with injected fakes
  test_no_forbidden_surface.py      # static forbidden-surface guard over src/agent_run_supervisor/hermes_caller/
```

Conservatism rules for the later code:

- **stdlib-only** (`dataclasses`, `enum`, `typing`, `html`, `pathlib`, `json`); no third-party deps
  unless a future phase explicitly approves them (per `CLAUDE.md` tooling expectations).
- Depend on `agent_run_supervisor.caller` **only**; never reach into supervisor internals.
- No network, no subprocess, no filesystem writes outside test temp dirs; the adapter consumes
  `CallerResult` and reads already-persisted artifact paths as **evidence references** (it does
  not parse raw acpx streams — that stays supervisor-owned).
- All identifiers in fixtures/examples are `[REDACTED]`.

## 6. Dataclass / function signatures (design sketches, not committed code)

These are **design sketches** for the later phase, not code added in this PR. Final shapes are
confirmed during implementation TDD.

### 6.1 `task.py` — caller-side intake descriptor

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class DocCheckTask:
    """Caller-side document-check task descriptor (intake input only).

    These fields are caller-owned correlation/business inputs. They are NEVER
    forwarded to the supervisor as platform fields and never enter
    CallerInvocationSpec / CallerResult.
    """
    task_id: str            # caller-side correlation only; "[REDACTED]" in examples
    document_ref: str       # local path/handle the caller resolves to content
    check_profile: str      # e.g. "completeness+xref"; caller business config
    requested_by: str       # caller-side identity; "[REDACTED]"; never sent to supervisor
    surface: str = "feishu_card"   # presentation target hint; caller-owned
```

### 6.2 `intake.py` — intake/role adapter (task → `CallerInvocationSpec`)

```python
from __future__ import annotations
from pathlib import Path
from agent_run_supervisor.caller import CallerInvocationSpec
from agent_run_supervisor.role import AgentRoleSpec
from .task import DocCheckTask

def resolve_document(task: DocCheckTask) -> str:
    """Resolve a caller-side document_ref to local check context text.

    Local-only read; no network. Returns caller-owned `context` text. Sensitive
    values stay caller-side and are redacted in artifacts.
    """

def build_check_prompt(task: DocCheckTask) -> str:
    """Build caller-owned check instructions (the `prompt`)."""

def build_exec_spec(
    task: DocCheckTask,
    *,
    role: AgentRoleSpec,
    cwd: str | Path,
    runs_dir: str | Path | None = None,
    dry_run: bool = False,
) -> CallerInvocationSpec:
    """Map a task to an exec (or exec_dry_run) CallerInvocationSpec.

    Adds NO platform fields. Populates only mode/role/prompt/context/cwd/runs_dir.
    """

def build_session_create_spec(
    task: DocCheckTask, *, role: AgentRoleSpec, session_id: str,
    cwd: str | Path, sessions_dir: str | Path | None = None,
    session_name: str | None = None,
) -> CallerInvocationSpec: ...

def build_session_send_spec(
    task: DocCheckTask, *, role: AgentRoleSpec, session_id: str,
    prompt: str, cwd: str | Path, sessions_dir: str | Path | None = None,
) -> CallerInvocationSpec: ...

def build_session_status_spec(...) -> CallerInvocationSpec: ...
def build_session_close_spec(...) -> CallerInvocationSpec: ...
```

### 6.3 `verdict.py` — caller-owned business verdict

```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from agent_run_supervisor.caller import CallerResult

class BusinessVerdict(str, Enum):
    PASS = "PASS"
    NEEDS_REVISION = "NEEDS_REVISION"
    BLOCK = "BLOCK"

@dataclass(frozen=True)
class VerdictDecision:
    verdict: BusinessVerdict
    rationale: str          # caller-owned, derived from status + redacted final_message
    supervisor_status: str | None   # carried as EVIDENCE, never equated with the verdict

def derive_verdict(result: CallerResult) -> VerdictDecision:
    """Derive the caller-owned document-check verdict from a CallerResult.

    The supervisor's business_verdict stays null; this lives entirely caller-side.
    Status is evidence, not a pass/fail.
    """
```

### 6.4 `events.py` — caller-side read-only view over persisted normalized events

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class NormalizedEventView:
    """Read-only projection of one persisted normalized event (evidence).

    Exposes STRUCTURAL signals only (family, kind, status, text_length, counts) —
    never bulk agent content beyond the already-redacted final_message.
    """
    family: str             # e.g. run_started, tool_started, agent_message_delta, run_completed
    kind: str | None
    status: str | None
    text_length: int | None
    summary: str | None     # forward-compatible key_summary for unknown_update

def load_events(artifact_dir: str | Path) -> list[NormalizedEventView]:
    """Load persisted normalized events for a run/turn as read-only evidence.

    Local read of already-redacted artifacts; does NOT parse raw acpx streams.
    """
```

### 6.5 `view_model.py` — progress/result view-model (presentation, no delivery)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from agent_run_supervisor.caller import CallerResult
from .events import NormalizedEventView
from .verdict import VerdictDecision

class CardPhase(str, Enum):
    PREPARING = "preparing"
    RUNNING = "running"
    NEEDS_PERMISSION = "needs_permission"
    COMPLETED = "completed"
    ERROR = "error"
    VERDICT = "verdict"

@dataclass(frozen=True)
class ProgressItem:
    label: str              # structural activity line (kind/status only; no file content)
    state: str              # in_progress | done | failed

@dataclass(frozen=True)
class CardViewModel:
    phase: CardPhase
    title: str
    progress: list[ProgressItem] = field(default_factory=list)
    supervisor_status: str | None = None      # evidence chip, NOT a verdict
    findings_text: str | None = None          # redacted final_message, treated as untrusted
    verdict: VerdictDecision | None = None     # caller-owned banner
    session_lifecycle: str | None = None       # opened | alive | closed (session flows)
    evidence_ref: str | None = None            # local run_dir/session_dir; NO upload

def build_progress_view_model(
    events: list[NormalizedEventView], *, session_lifecycle: str | None = None,
) -> CardViewModel:
    """Map normalized-event evidence to a running/preparing progress view-model."""

def build_result_view_model(
    result: CallerResult, events: list[NormalizedEventView], decision: VerdictDecision,
) -> CardViewModel:
    """Map a completed CallerResult + verdict to a result view-model.

    findings_text is the already-redacted final_message, carried as UNTRUSTED text.
    """
```

### 6.6 `feishu_adapter.py` — OFFLINE view-model → card payload dict (no delivery)

```python
from __future__ import annotations
import html
from typing import Any
from .view_model import CardViewModel

def escape_untrusted(text: str | None) -> str:
    """Escape untrusted agent text for safe plain rendering.

    NEVER renders trusted Markdown/HTML (architecture.md §6.2). Uses html.escape.
    """
    return html.escape(text or "")

def to_feishu_card_payload(view_model: CardViewModel) -> dict[str, Any]:
    """Build an OFFLINE Feishu rich-card payload dict from the view-model.

    Returns a plain dict only. It is NEVER sent: no Feishu SDK/import, no HTTP,
    no token, no webhook, no delivery. All text fields are escaped/plain.
    The returned dict carries NO channel/message/webhook/recipient identifiers.
    """
```

### 6.7 `hermes.py` — orchestration (exec + session flows)

```python
from __future__ import annotations
from pathlib import Path
from agent_run_supervisor.caller import (
    CallerResult, CallerInvocationSpec, invoke_caller,
)
from agent_run_supervisor.role import AgentRoleSpec
from .task import DocCheckTask
from .view_model import CardViewModel

class HermesDocCheckCaller:
    """Concrete local caller orchestrating a document-check above the I1 boundary.

    Optional `runner` / `session_runtime` are injected for fake/local testing and
    are passed straight through to invoke_caller; nothing here parses raw streams,
    delivers messages, or carries platform identifiers.
    """
    def __init__(self, *, runner=None, session_runtime=None) -> None: ...

    def run_exec(
        self, task: DocCheckTask, *, role: AgentRoleSpec, cwd: str | Path,
        runs_dir: str | Path | None = None, dry_run: bool = False,
    ) -> tuple[CallerResult, CardViewModel]:
        """One-shot document-check: invoke exec, derive verdict, build result view-model."""

    def run_session(
        self, task: DocCheckTask, *, role: AgentRoleSpec, session_id: str,
        turn_prompts: list[str], cwd: str | Path,
        sessions_dir: str | Path | None = None,
    ) -> tuple[list[CallerResult], CardViewModel]:
        """Interactive multi-turn check: create -> send* -> status -> close.

        Returns each turn/management CallerResult plus the final result view-model.
        """
```

## 7. Exec flow (later implementation shape)

For a bounded, single-pass document check (no follow-up turns):

1. `HermesDocCheckCaller.run_exec` builds an exec spec via `intake.build_exec_spec` (mode `exec`,
   the `doc-check-reviewer` role, caller-owned `prompt`/`context`, local `cwd`/`runs_dir`).
2. It renders a **running** progress view-model **before** invocation (no events yet → coarse
   "running" phase) and a **result** view-model after — matching L1 §7's two-phase card.
3. It calls `invoke_caller(spec, runner=<injected fake or default>)`; the supervisor compiles a
   default-deny policy + argv (no shell), validates cwd intent, runs one `acpx exec` under the
   watchdog, parses stdout into normalized events, classifies a status, and persists redacted
   artifacts — **all supervisor-owned and unchanged**.
4. It receives a `CallerResult` (`business_verdict: null`), loads persisted normalized events as
   read-only evidence (`events.load_events`), **derives its own verdict**
   (`verdict.derive_verdict`), and builds the result view-model.
5. It builds the **offline** Feishu card payload dict via `feishu_adapter.to_feishu_card_payload`
   and **stops** — no delivery, no Feishu API.

A `dry_run=True` path uses mode `exec_dry_run` to preview the compiled policy/argv with **no AGENT
launch**, producing a "configuration preview" view-model without running anything.

## 8. Persistent-session flow (later implementation shape)

For an interactive, multi-turn review:

1. `run_session` builds and invokes a `session_create` spec; the card shows
   "session opened / preparing" (`session_lifecycle = "opened"`).
2. For each prompt in `turn_prompts`, it builds and invokes a `session_send` spec; each turn
   returns its own `CallerResult`, giving a natural per-turn boundary to update the progress
   view-model with interim findings (`session_lifecycle = "alive"`).
3. It invokes `session_status` (read-only liveness/summary) and reflects "checking / alive".
4. It invokes `session_close` (atomic local `closed` transition); the card switches to the final
   result + caller-derived verdict (`session_lifecycle = "closed"`).
5. Binding revalidation, lease handling, closed-session refusal, and redaction stay
   **supervisor-owned** (I1 / S1). Hermes only composes specs and presentation.

Abort/list remain lower-level `SessionRuntime` features not surfaced by I1's caller boundary;
L2 does not add them to the boundary (L1 §8).

## 9. Offline Feishu view-model mapping (caller-owned)

The later adapter maps supervisor evidence (normalized event families per
`docs/design/result-event-schema.md` §4, plus result fields) into the view-model, then into an
**offline** card payload dict. The Feishu column is the **presentation target only — no delivery**
(carried verbatim from L1 §12).

| Supervisor signal (event family or result field) | Caller-owned view-model element | Feishu card presentation (target only) |
|---|---|---|
| `run_started` / `session_new_requested` / `session_prompt_sent` | `phase = "running"` | header + spinner |
| `tool_started` (`kind`) | activity line "reading/inspecting" (kind only, **no file content**) | progress list item |
| `tool_updated` / `tool_completed` (`status`) | checklist item state (in-progress / done / failed) | list item status |
| `agent_message_delta` (`text_length`) | "drafting findings… (N chars)" — **length only** | streaming indicator |
| `agent_thought_delta` | optional "thinking…" indicator | subtle status text |
| `usage_updated` | token/turn meter | footer meter |
| `available_commands_updated` | optional capabilities note | (usually none) |
| `permission_requested` (`option_ids`) / `permission_denied` (`option_id`) | "permission needed/denied" badge | warning chip |
| `run_completed` (`stop_reason`) | `phase = "completed"` | switch to result view |
| `run_failed` (`code`, `acpx_code`) | `phase = "error"` + supervisor `error_code` | error state |
| `unknown_update` (`update_type`, `key_summary`) | generic "update" line (forward-compatible) | no special element |
| result `status` / `error_code` / `detail_code` (§3) | supervisor-status chip (**evidence, not verdict**) | status line |
| result `final_message` (redacted) | findings rendered as **untrusted, escaped plain text** — **never trusted Markdown/HTML** | card body (escaped) |
| session `create`/`status`/`close` projections (§2.2) | session lifecycle chip ("opened" / "alive" / "closed") | card lifecycle state |
| `CallerResult` artifact paths (`run_dir`/`session_dir`) | local "evidence" reference | "view evidence" affordance (local, no upload) |
| **Hermes business verdict** (derived, caller-owned) | `PASS` / `NEEDS_REVISION` / `BLOCK` banner | verdict banner |

### 9.1 Rendering safety rules (carried from non-approvals)

- `final_message` and any observed text are **untrusted**: render escaped/plain via
  `html.escape`, **never** as trusted Markdown/HTML (architecture.md §6.2).
- The view-model exposes **structural** signals (lengths, kinds, statuses, counts), not bulk agent
  content beyond the already-redacted `final_message`.
- The verdict is always **caller-derived**; the supervisor status is shown separately as evidence.
- The card payload dict carries **no** channel/message/webhook/recipient/Gateway/delivery field.

## 10. TDD task breakdown (RED → GREEN → REFACTOR)

The later phase implements each module test-first. Every task lists its RED (write failing test),
GREEN (minimal code to pass), and REFACTOR (tidy, keep green) steps. No production line is written
before a failing test exists.

### T1 — intake/role adapter (`intake.py`)

- **RED:** `test_intake.py` asserts `build_exec_spec` returns a `CallerInvocationSpec` with
  `mode="exec"`, the supplied role, non-empty `prompt`/`context`, and **no** attribute carrying
  `task_id`/`document_ref`/`requested_by`/`surface`; assert session-spec builders set the correct
  modes and require `session_id`. Tests fail (module absent).
- **GREEN:** implement the builders mapping `DocCheckTask` → spec fields only.
- **REFACTOR:** factor shared spec construction; assert frozen-dataclass immutability.

### T2 — verdict derivation (`verdict.py`)

- **RED:** `test_verdict.py` feeds synthetic `CallerResult`s (success status + clean findings →
  `PASS`; success + issues → `NEEDS_REVISION`; failure/non-success status → `BLOCK`) and asserts
  `derive_verdict` returns the expected `BusinessVerdict` with `supervisor_status` carried as
  evidence. Assert it never reads a non-null `business_verdict` from the supervisor.
- **GREEN:** implement deterministic derivation from status + redacted `final_message`.
- **REFACTOR:** extract status/finding heuristics; keep status≠verdict explicit in code + tests.

### T3 — normalized-event evidence view (`events.py`)

- **RED:** `test_view_model.py` (or a dedicated `test_events.py`) loads a synthetic redacted event
  fixture and asserts only structural fields are exposed (`text_length`, `kind`, `status`,
  `key_summary`) and that no bulk content leaks. Fails (module absent).
- **GREEN:** implement read-only loading/projection of persisted events.
- **REFACTOR:** make the projection total over all §4 families incl. `unknown_update`.

### T4 — view-model construction (`view_model.py`)

- **RED:** assert `build_progress_view_model` maps event families to the §9 elements (phase,
  progress items, badges) and `build_result_view_model` produces `phase=COMPLETED`/`ERROR`,
  carries the verdict banner, the supervisor-status **evidence** chip, and the **untrusted**
  `findings_text`. Fails.
- **GREEN:** implement the mapping table from §9.
- **REFACTOR:** table-drive the mapping; cover the card state lifecycle (L1 §12.1).

### T5 — offline Feishu adapter (`feishu_adapter.py`)

- **RED:** `test_feishu_adapter.py` asserts `to_feishu_card_payload` returns a plain `dict`, that
  untrusted text is **escaped** (inject `<script>`/markdown and assert it is neutralized), that the
  payload contains **no** delivery/channel/webhook/recipient keys, and (static) that the module
  imports **no** network/SDK symbol. Fails.
- **GREEN:** implement escaped, delivery-free payload construction.
- **REFACTOR:** centralize escaping; assert idempotent escaping and stable key set.

### T6 — exec flow (`hermes.py`, `test_hermes_exec_flow.py`)

- **RED:** inject a **fake runner** into `HermesDocCheckCaller.run_exec`; assert it calls
  `invoke_caller` with an exec spec, derives a verdict, and returns a result view-model; assert the
  `dry_run=True` path uses `exec_dry_run` and launches nothing. Fails.
- **GREEN:** implement `run_exec` orchestration over `invoke_caller`.
- **REFACTOR:** dedupe progress/result view-model assembly shared with the session flow.

### T7 — persistent-session flow (`hermes.py`, `test_hermes_session_flow.py`)

- **RED:** inject a **fake session-runtime**; assert `run_session` drives
  create → send×N → status → close, returns one `CallerResult` per call, advances
  `session_lifecycle`, and builds the final result view-model. Fails.
- **GREEN:** implement `run_session` orchestration.
- **REFACTOR:** assert lease/binding/redaction stay supervisor-owned (not re-implemented here).

### T8 — forbidden-surface guard (`test_no_forbidden_surface.py`)

- **RED:** a static test (see §12) scans `src/agent_run_supervisor/hermes_caller/` and fails on any forbidden import or
  platform/delivery token used as a real field/call. Initially fails on a deliberately-seeded
  violation in a scratch fixture, then guards the real tree.
- **GREEN:** ensure the package is clean; the guard passes.
- **REFACTOR:** keep the allowlist of permitted (prose/comment-only) terms minimal and documented.

## 11. Fake / local fixture strategy

All tests are **fake/local/offline**; no real acpx, Feishu, network, or Sachima.

- **Injected fakes for the boundary.** `invoke_caller` already accepts `runner` and
  `session_runtime` parameters. Tests pass fakes that return canned `RunOutcome` /
  `Session*Outcome` objects with redacted `result` payloads, exercising Hermes orchestration
  **without** launching acpx. This mirrors I1's fake-executor/dry-run testing posture.
- **Synthetic CallerResult fixtures** under `tests/hermes_caller/fixtures/` as JSON matching
  `docs/design/result-event-schema.md` (run `result.json` §1, session turn §2.1, projections
  §2.2), always `business_verdict: null`, all identifiers `[REDACTED]`.
- **Synthetic normalized-event fixtures** covering each §4 family (incl. `unknown_update`,
  `permission_requested`, `run_failed`) for the view-model mapping tests.
- **No real document material.** `document_ref` resolves to a local temp fixture file in tests;
  no production paths, secrets, or platform identifiers are committed.
- **Adversarial rendering fixtures.** Findings containing `<script>`, markdown, and control chars
  to prove escaped/plain rendering (no trusted Markdown/HTML).
- **No delivery doubles.** There is intentionally **no** Feishu client/transport (real or fake) —
  the adapter stops at a payload dict, so there is nothing to deliver and nothing to mock for
  delivery.

## 12. Static forbidden-surface gates (later implementation)

In addition to the repo gates (§6 of current-status), the later PR adds a **static guard** proving
the planned code introduces no platform/live surface:

- **Import allowlist.** `test_no_forbidden_surface.py` parses each `src/agent_run_supervisor/hermes_caller/*.py` with
  `ast` and asserts imports come **only** from stdlib + `agent_run_supervisor.caller` +
  `agent_run_supervisor.role` (for the role type). It **fails** on any import of supervisor
  internals (`runner`, `session_runtime`, `parser`, `policy`, `session`, `commands`) or any
  networking/HTTP/SDK module (`socket`, `http`, `urllib`, `requests`, `feishu`/`lark`, etc.).
- **Token scan as fields/calls.** Grep changed `src/agent_run_supervisor/hermes_caller/` for platform/live terms
  (`delivery`, `webhook`, `gateway`, `Sachima`, `@all`, `auto_reply`, `public_ingress`, `feishu`
  as an API client, `send_card`, `post_message`). They may appear **only** in non-approval /
  scenario / comment prose, **never** as a field, function call, or import.
- **Generic-contract invariant.** Assert the package adds **no** attribute to
  `CallerInvocationSpec` / `CallerResult` and never sets a non-null `business_verdict`.
- **No-delivery payload invariant.** Assert `to_feishu_card_payload` output has no
  channel/message/webhook/recipient/Gateway/delivery key.
- **Repo gates** still run unchanged:
  ```bash
  python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
  python3 -m pytest -q
  python3 -m compileall -q src scripts tests
  PYTHONPATH=src python3 -m agent_run_supervisor doctor
  PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
  python tools/build_docs_index.py --check
  python tools/docs_drift_signal.py --check
  git diff --check
  ```

## 13. Docs update steps for the later implementation PR

When the later approved phase lands code, that PR (not this one) must:

1. Add an **L2 implementation phase** entry to `docs/roadmap/current-status.md` §3 with evidence
   (files, tests, gates), and move `ARS-CALLER-INTEGRATION` from "PARTIAL / plan exists" toward
   "concrete caller implemented (local/offline)" while keeping all live surfaces parked.
2. Update `docs/roadmap/features.md` `F-INTEGRATION-001` from "L2 plan is the next implementation
   plan" to "Hermes caller + offline Feishu view-model implemented (local/offline)", citing the
   new package + tests; keep `F-NONGOAL-001` unchanged.
3. Update `docs/design/technical-solution.md` / `docs/design/architecture.md` only if the caller
   package needs a pointer; do **not** add platform fields to the supervisor contract.
4. Regenerate `docs/INDEX.md` via `python tools/build_docs_index.py --write` and the drift signal
   via `python tools/docs_drift_signal.py --write`; **never hand-edit** generated outputs.
5. Bump `last_validated_at` on any knowledge doc the PR cites (use-driven validation, `CLAUDE.md`).

This L2 plan (this PR) performs **only** the plan addition plus minimal pointer edits in §2/§3 of the task (status +
features), then lets Hermes regenerate `docs/INDEX.md` / `docs/lessons/_drift_report.md` with repo tools; generated files are never hand-edited.

## 14. Rollback

L2 (this PR) is plan-only and rollback-trivial:

- Revert this plan file and the minimal pointer edits in `docs/roadmap/current-status.md` and
  `docs/roadmap/features.md`.
- Regenerate `docs/INDEX.md` / `docs/lessons/_drift_report.md` via the repo tools (Hermes).
- **No** runtime code, tests, CLI, config, external service, Gateway, Sachima, Feishu, or
  data-migration rollback exists, because nothing live or runtime was implemented and `caller.py`
  was not changed.

For the **later implementation** phase, rollback is: revert the `src/agent_run_supervisor/hermes_caller/` package and
`tests/hermes_caller/` tree (a self-contained caller-side package), regenerate docs indexes, and
revert that PR's status/feature edits. Because the package depends only on the I1 public surface
and the supervisor does not import it, removing it leaves the supervisor untouched.

## 15. Verification plan (this plan PR)

L2 ships **no code**, so verification is documentation-centric. Confirm:

1. **Authority alignment** — the plan is derivative of and consistent with
   `GOAL.md`/PRD/architecture/technical-solution/features/current-status and the **closed L1
   design**; it redefines no product goal and grants no live approval.
2. **Doc gates** (run by Hermes; do not hand-edit generated outputs):
   ```bash
   python tools/build_docs_index.py --check
   python tools/docs_drift_signal.py --check
   git diff --check
   ```
   then regenerate with `--write` as part of the docs PR.
3. **Boundary / non-approval scan** — platform/live terms appear in changed docs **only** in
   non-approval / scenario / seam / plan-of-record prose, never as approved behavior or as a
   supervisor field.
4. **Generic-contract invariants** — the plan adds **no** field to `CallerInvocationSpec` /
   `CallerResult`, keeps `business_verdict: null`, and keeps verdict + view-model + rendering
   caller-owned.
5. **No code drift** — `git diff` shows only docs changes; `src/agent_run_supervisor/caller.py`
   and all runtime modules and tests are untouched in this PR.

## 16. PR / review process

- **Branch / PR:** one plan task = one PR on `docs/l2-hermes-caller-viewmodel-plan-2026-06-01`,
  targeting `main`.
- **Roles:** Claude Code authors as Architect + Documentation Engineer; **Codex CLI** is the
  fresh-context primary reviewer; **Hermes** owns scope control, verification, evidence, and
  arbitration, and runs the doc generators.
- **Reviewers must check** PRD/design/feature/roadmap + **L1** alignment (not only that gates
  pass): the supervisor stays generic, the verdict stays caller-owned, the Feishu card stays a
  caller-owned **offline** view-model with no delivery, the planned caller-side subpackage is stdlib-only and
  depends only on the I1 surface, and the Sachima seam stays defined-but-unapproved.
- **PR body must include:** summary; source-of-truth docs touched; feature/roadmap status impact
  (L2 plan-only — `ARS-CALLER-INTEGRATION` gains a next-implementation plan; implementation/live/
  Sachima/Feishu/Gateway parked); plan-only statement; no-secret statement (`[REDACTED]`
  placeholders); and an explicit non-approval boundary statement confirming no real delivery /
  ingress / Gateway / Sachima / auto-reply / live behavior was added or approved.

## 17. Next approval text

> **Requested next approval (not granted by this PR):** Approve an **L2 implementation phase** to
> build the concrete **Hermes caller** and an **offline Feishu rich-card view-model adapter** as a
> new **stdlib-only, caller-side subpackage** (`src/agent_run_supervisor/hermes_caller/`) above the generic I1 boundary,
> covering `exec` and `persistent session` document-check flows, **fake/local/offline only**.
> Explicitly **excluded** from the requested approval and still parked: real Feishu API / IM
> delivery, platform ingress, Gateway lifecycle, Sachima behavior integration, automatic replies,
> live/default-on behavior, `@all`, agent-to-agent / worker auto-routing, platform fields in the
> generic supervisor contract, supervisor-side business verdict, and trusted Markdown/HTML
> rendering. Crossing the Sachima seam (L1 §14) remains a **separate** later approval with its own
> authority-chain entries. Until this approval is granted, no `src/agent_run_supervisor/hermes_caller/` code is written.
