<!-- Hero -->
<p align="center">
  <img src="docs/assets/branding/readme-hero.png" alt="Agent Run Supervisor" width="860">
</p>

<!-- Language links -->
<p align="center">
  <b>English</b>
  &nbsp;·&nbsp;
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  A small, <b>local-first</b> Python library &amp; dev CLI that supervises<br>
  ACP/acpx external AGENT runs and turns runner behavior into <b>redacted, auditable evidence</b>.
</p>

<p align="center">
  <code>Python&nbsp;≥&nbsp;3.11</code>
  &nbsp;·&nbsp;
  <code>stdlib-only</code>
  &nbsp;·&nbsp;
  <code>local-first</code>
  &nbsp;·&nbsp;
  <code>MIT</code>
  &nbsp;·&nbsp;
  <code>status:&nbsp;pre-release&nbsp;(0.0.0)</code>
</p>

---

## What it does

Every project that drives an external AGENT through **ACP/acpx** re-implements the same
plumbing: launching and babysitting the runner subprocess, compiling a permission policy,
parsing a stream of observed events, classifying exit behavior, and redacting artifacts
before anything touches disk. Done ad-hoc, each caller grows its own subtly-unsafe copy.

`agent-run-supervisor` factors that into one independent, **local** supervisor layer. A caller
picks a role, a prompt, and a working directory; the supervisor validates the role, compiles a
default-deny policy and a shell-free argv, supervises the run, parses observed output into
normalized events, classifies a **supervisor-owned status**, and writes **redacted,
restrictive-permission local artifacts**. The caller gets auditable evidence — not a tangle of
runner-lifecycle code.

The product covers **two execution modes**: one-shot exec (implemented) and persistent sessions
(product-required, planned — see [Roadmap](#roadmap)). It is deliberately **not** Sachima, a
Gateway plugin, an IM adapter, or a daemon, and it never emits a business verdict
(`business_verdict` is always `null`).

## How it works

<p align="center">
  <img src="docs/assets/diagrams/how-it-works.svg" alt="How agent-run-supervisor validates a role, supervises ACP/acpx, observes an external AGENT, and writes redacted local artifacts" width="900">
</p>

Four principles keep it honest:

- **Supervisor, not business judge.** Runner/protocol completion is never a business verdict;
  `business_verdict` stays `null` and caller-owned.
- **Auditable by default.** Runs produce deterministic, redacted artifacts with restrictive
  permissions (`0700` dirs, `0600` files, atomic final writes).
- **Fail closed on uncertainty.** Invalid roles, cwd-outside-roots, malformed stdout, protocol
  drift, denied permissions, and watchdog timeouts all resolve to deterministic non-success
  statuses — an invalid cwd creates **no** artifacts at all.
- **Honest security claims.** `allowed_roots` validates cwd/config **intent** only — it is
  **not** an OS/filesystem sandbox.

Out of scope — caller/platform territory: public ingress, real IM delivery, Gateway lifecycle,
production config writes, live/default-on behavior, `@all` fan-out, and agent-to-agent routing.

## Install and use

> **No published package yet** (version `0.0.0`). Run from a source checkout. The runtime is
> **Python ≥ 3.11, standard-library only**; `pytest` is the only (optional) dev dependency.

```bash
# Clone and enter the repository
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor

# Validate an AgentRoleSpec (JSON) and print its stable role hash
PYTHONPATH=src python3 -m agent_run_supervisor validate-role <role-file>.json

# Replay an observed acpx stdout stream through the parser (deterministic, launches no AGENT)
PYTHONPATH=src python3 -m agent_run_supervisor replay \
  fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson

# Probe local readiness (read-only, never launches an AGENT)
PYTHONPATH=src python3 -m agent_run_supervisor doctor

# Dry-run: compile policy + argv and persist preview artifacts, launch nothing
PYTHONPATH=src python3 -m agent_run_supervisor run \
  --role <role-file>.json --prompt-file <prompt>.txt --no-real-run

# Real one-shot exec: supervise a local `acpx exec` under the role's policy
# (requires acpx/Node available locally; launches ONE explicit, local AGENT)
PYTHONPATH=src python3 -m agent_run_supervisor run \
  --role <role-file>.json --prompt-file <prompt>.txt
```

Once installed (`pip install -e .`), the same surface is available as the
`agent-run-supervisor <command> …` console script.

Run artifacts land under `.agent-run-supervisor/runs/<run_id>/` — redacted prompt/env/argv, the
generated policy, observed stdout (NDJSON), normalized events, stderr, `result.json`
(`business_verdict = null`), and `redaction-report.json`.

## Environment requirements

| Need | Requirement |
|---|---|
| Runtime | **Python ≥ 3.11**, standard-library only — zero third-party runtime dependencies. |
| Tests (optional) | `pytest >= 8, < 10` (the `dev` extra). |
| Real AGENT runs | **Node + acpx** available locally — required **only** for `run` without `--no-real-run`. |
| Everything else | `validate-role`, `replay`, `doctor`, and `--no-real-run` need **no** Node/acpx and launch **no** AGENT. |

## Quality and test indicators

Factual local gates that keep the supervisor honest (run from the repository root):

| Indicator | Evidence |
|---|---|
| Unit / integration tests | **132 pytest tests** — `python3 -m pytest -q`. |
| acpx contract | acpx `0.10.0` fixtures + validator — `python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0`. |
| Import / syntax smoke | `python3 -m compileall -q src scripts tests`. |
| Doctor (read-only) | `… doctor` never launches an AGENT (`launched_real_agent = false`). |
| Replay determinism | `… replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson`. |
| Safe artifacts | Redacted artifacts · `business_verdict = null` · EventStore `0700`/`0600` atomic NDJSON. |

```bash
python3 scripts/validate_contract_fixtures.py fixtures/acpx-0.10.0
python3 -m pytest -q
python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m agent_run_supervisor doctor
PYTHONPATH=src python3 -m agent_run_supervisor replay fixtures/acpx-0.10.0/success-codex-sentinel/stdout.ndjson
```

## Roadmap

High-level direction only — full phase status, acceptance, and non-approvals live in
[`docs/roadmap/current-status.md`](docs/roadmap/current-status.md) and
[`docs/roadmap/features.md`](docs/roadmap/features.md).

- **Now — foundations + one-shot exec.** Role/policy/parser/store foundation plus real local
  `acpx exec` supervision (role-bound, outer watchdog, kill metadata) are in place.
- **Next — persistent sessions.** Controlled multi-turn ACP/acpx session lifecycle
  (create/send/resume/close, locks, stale-lock recovery). Product-required, **not** a non-goal.
- **Later — hardening + thin integrations.** Fuller doctor probes, artifact retention/cleanup,
  and a documented result schema; thin caller integration only under separate approval.

## License

© the `agent-run-supervisor` authors. Released under the **[MIT](https://opensource.org/license/mit)**
license (`license = { text = "MIT" }` in [`pyproject.toml`](pyproject.toml)). Pre-release software
(`0.0.0`); surfaces and result schemas may still change.

<p align="center">
  <img src="docs/assets/branding/logo-mark.png" alt="agent-run-supervisor logo mark" width="72" height="72">
  <br>
  <sub><b>agent-run-supervisor</b> — a supervisor, not a business judge.</sub>
  <br>
  <sub><a href="README.zh-CN.md">简体中文 README</a></sub>
</p>
