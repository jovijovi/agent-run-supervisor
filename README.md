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
  <code>status:&nbsp;0.1.0</code>
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

The product covers **two execution modes**, both implemented for local use: one-shot exec and a
local persistent-session lifecycle (create/send/status/close/abort/list — see
[Roadmap](#roadmap)). It is deliberately **not** Sachima, a Gateway plugin, an IM adapter, or a
daemon, and it never emits a business verdict (`business_verdict` is always `null`).

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

```bash
pip install agent-run-supervisor
```

Or install from a source checkout (see [Development](#development)).

```bash
# Validate an AgentRoleSpec (JSON) and print its stable role hash
agent-run-supervisor validate-role <role-file>.json

# Replay an observed acpx stdout stream through the parser (deterministic, launches no AGENT)
agent-run-supervisor replay \
  fixtures/acpx-0.12.0/success-codex-sentinel/stdout.ndjson

# Probe local readiness (read-only, never launches an AGENT)
agent-run-supervisor doctor

# Dry-run: compile policy + argv and persist preview artifacts, launch nothing
agent-run-supervisor run \
  --role <role-file>.json --prompt-file <prompt>.txt --no-real-run

# Real one-shot exec: supervise a local `acpx exec` under the role's policy
# (requires acpx/Node available locally; launches ONE explicit, local AGENT)
agent-run-supervisor run \
  --role <role-file>.json --prompt-file <prompt>.txt

# Local persistent-session lifecycle (role must use a persistent session strategy):
# create → send turn(s) → status → close/abort. create/send/status/close/abort drive a
# real local acpx session and need Node + acpx; `session list` is local read-only and
# launches no AGENT.
agent-run-supervisor session create \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session send \
  --role <role-file>.json --session-id <id> --prompt-file <prompt>.txt
agent-run-supervisor session status \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session close \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session abort \
  --role <role-file>.json --session-id <id>
agent-run-supervisor session list

# Plan or apply local artifact retention/cleanup (dry-run by default; --apply deletes)
agent-run-supervisor cleanup
```

From a source checkout without installing, prefix commands with `PYTHONPATH=src python3 -m agent_run_supervisor` instead of `agent-run-supervisor`.

```bash
# Clone and enter the repository
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor

# Example: validate-role from checkout (no install)
PYTHONPATH=src python3 -m agent_run_supervisor validate-role <role-file>.json
```

### Codex/acpx smoke helper

For an explicit local connectivity check that exercises both supervised Codex surfaces —
one-shot exec first, then a two-turn persistent session — use the maintained helper:

```bash
python3 scripts/smoke_codex_acpx.py --model 'gpt-5.5[xhigh]'
```

The helper creates temporary no-tool roles, asks Codex for exact sentinel replies, verifies
`business_verdict = null`, closes the persistent session, and cleans artifacts by default
(`--keep-artifacts` keeps the temp scratch/runs/sessions directories). It intentionally uses
`runner.acpx_binary = null`, so the existing compiler invokes the pinned
`npx -y acpx@0.12.0` path.

Use the exact Codex ACP model IDs advertised by the ACP session, such as
`gpt-5.5[xhigh]`, `gpt-5.5[high]`, or `gpt-5.4-mini[medium]`. A bare id like
`gpt-5.5` can be rejected with `the ACP agent did not advertise that model`, and the
helper refuses it before launching anything.

Once installed (`pip install -e .`), the same surface is available as the
`agent-run-supervisor <command> …` console script.

Run artifacts land under `.agent-run-supervisor/runs/<run_id>/` — redacted prompt/env/argv, the
generated policy, observed stdout (NDJSON), normalized events, stderr, `result.json`
(`business_verdict = null`), and `redaction-report.json`. Persistent-session artifacts land under
`.agent-run-supervisor/sessions/<session_id>/` (local record, redacted `management/` summaries,
and one redacted `turns/<turn_id>/` directory per send). The `cleanup` command plans and (only
with `--apply`) deletes aged run/session artifacts, confined to the resolved
`.agent-run-supervisor` root and never touching open/live-locked sessions.

## Environment requirements

| Need | Requirement |
|---|---|
| Runtime | **Python ≥ 3.11**, standard-library only — zero third-party runtime dependencies. |
| Tests (optional) | `pytest >= 8, < 10` (the `dev` extra). |
| Real AGENT runs / session turns | **Node + acpx + the target AGENT CLI** available locally — required for `run` (without `--no-real-run`) and for the real `session create/send/status/close/abort` turn & management commands. The Codex smoke helper specifically needs `npx` plus Codex CLI via `CODEX_PATH` or `PATH`. |
| No-AGENT commands | `validate-role`, `replay`, `doctor`, `run --no-real-run`, `session list`, and `cleanup` (dry-run) need **no** Node/acpx and launch **no** AGENT. |

## Development

Primary path uses [uv](https://docs.astral.sh/uv/) for a reproducible dev environment.
Short commands are available via the root [`Makefile`](Makefile):

```bash
git clone https://github.com/jovijovi/agent-run-supervisor.git
cd agent-run-supervisor
make sync      # uv sync --extra dev --extra release
make verify    # full local gates (same as CI)
make build     # sdist/wheel + twine check
make smoke     # build + installed-wheel smoke
make clean     # remove build artifacts, caches, local scratch data
make help      # list all targets
```

Equivalent without Make:

```bash
uv sync --extra dev --extra release
./scripts/verify_local.sh
```

`make verify` / `./scripts/verify_local.sh` is the single local gate entry — it mirrors CI and
[`docs/roadmap/current-status.md`](docs/roadmap/current-status.md) §6 (tests, doctor/replay smoke,
docs index/drift, static safety scan, build/twine check, and installed-wheel smoke).

**pip fallback** (without uv):

```bash
pip install -e '.[dev,release]'
python3 -m pytest -q
```

## Publishing

**Production PyPI** — tag-triggered via GitHub Actions Trusted Publishing (no API tokens in the
repo):

```bash
make verify              # or ./scripts/verify_local.sh
# bump version in pyproject.toml + CHANGELOG.md, merge to main
make release-tag         # prints git tag vX.Y.Z && git push commands
agent-run-supervisor doctor   # after pip install from PyPI
```

**TestPyPI dry-run** (local upload with API token in env — never commit tokens):

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-...    # TestPyPI token
make release-test                 # verify + upload to TestPyPI

pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            agent-run-supervisor==0.1.0
agent-run-supervisor doctor
```

Maintainers must configure PyPI Trusted Publishing for workflow `release.yml` and environment
`pypi` before the first production tag push. See `docs/plans/2026-07-06-p3-engineering-basics.md`
for the operator checklist.

## Quality and test indicators

Factual local gates that keep the supervisor honest (run from the repository root with
`./scripts/verify_local.sh`, or step-by-step):

| Indicator | Evidence |
|---|---|
| Full local gate | `make verify` or `./scripts/verify_local.sh` — mirrors CI verify workflow. |
| Unit / integration tests | **Full pytest suite** — `uv run pytest -q` (current local acceptance: full suite passing). |
| acpx contract | acpx `0.12.0` fixtures + validator — `scripts/validate_contract_fixtures.py fixtures/acpx-0.12.0`. |
| Import / syntax smoke | `python -m compileall -q src scripts tests`. |
| Doctor (read-only) | `… doctor` never launches an AGENT (`launched_real_agent = false`). |
| Package checks | `python -m build` + `twine check dist/*`, plus an installed-wheel `agent-run-supervisor doctor` smoke. |
| Safe artifacts | Redacted artifacts · `business_verdict = null` · EventStore `0700`/`0600` atomic NDJSON. |

```bash
uv sync --extra dev --extra release
./scripts/verify_local.sh
```

## Roadmap

High-level direction only — full phase status, acceptance, and non-approvals live in
[`docs/roadmap/current-status.md`](docs/roadmap/current-status.md) and
[`docs/roadmap/features.md`](docs/roadmap/features.md).

- **Done — foundations + both execution modes.** Role/policy/parser/store foundation, real local
  `acpx exec` supervision (role-bound, outer watchdog, kill metadata), and the local
  persistent-session lifecycle (create/send/multi-turn-resume/status/close/abort/list, locks,
  stale-lock recovery) are implemented and closed for local use.
- **Done — hardening + local caller integration.** Full read-only doctor probe set, confined
  artifact retention/cleanup, a documented result/event schema, process-liveness crash recovery,
  the generic local caller boundary, and a local/offline Hermes caller + offline Feishu
  view-model adapter are merged.
- **Backlog — deeper hardening (not started).** `npx` strict-offline enforcement, stronger
  redaction/DLP plus a caller allowlist, and a lock-release audit trail are tracked as backlog
  only. Any live/platform integration (real Feishu/IM delivery, Sachima, Gateway lifecycle,
  public ingress) stays out of scope and requires separate approval.

## License

© the `agent-run-supervisor` authors. Released under the **[MIT](https://opensource.org/license/mit)**
license (`license = "MIT"` and [`LICENSE`](LICENSE)). Pre-release software
(`0.1.0`); surfaces and result schemas may still change before a stable `1.0.0`.

<p align="center">
  <img src="docs/assets/branding/logo-mark.png" alt="agent-run-supervisor logo mark" width="72" height="72">
  <br>
  <sub><b>agent-run-supervisor</b> — a supervisor, not a business judge.</sub>
  <br>
  <sub><a href="README.zh-CN.md">简体中文 README</a></sub>
</p>
