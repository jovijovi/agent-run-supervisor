---
title: "Remove the legacy acpx runtime, package, and CLI surface"
status: archived
created_at: 2026-08-06
last_validated_at: 2026-08-06
archived_at: 2026-08-06
implementation_authorized: true
production_authorized: false
---
# Plan — Remove the legacy acpx runtime, package, and CLI surface

## 1. Task Contract

**Objective.** Leave one production architecture — `arsd` + ars-core + Native ACP — with no acpx runtime, package path, CLI leaf, current product claim, or newly written `acpx_exit_code` field. The field removal ships as part of the unreleased API v3 contract.

**Done criteria (mechanical).**

| # | Criterion |
|---|---|
| D1 | No module under `src/agent_run_supervisor/` imports, spawns, parses, or configures acpx. |
| D2 | Installed console script exposes exactly `agents validate`, `agents doctor`, `run inspect` — the three commands design authority declares. `--help` contains no `acpx`. |
| D3 | Wheel **and** sdist manifests match a committed allowlist; assertion runs inside `make verify`; both artifacts are installed and smoked. |
| D4 | Current-authority docs name acpx only in past tense or as a refused surface. No current doc presents any acpx capability, command, probe, runtime, compatibility surface, or result field as available. |
| D5 | **Revised 2026-08-06 by operator decision — supersedes the compatibility reading below.** API v3 is the only contract: there is no historical persisted data to keep readable, so `acpx_exit_code` has no tolerant reader, passthrough, projection, alias, migration, dual format, or semantic branch. API v3 writers omit it, `_REQUIRED_NATIVE_RESULT_FIELDS` no longer requires it, and the persisted-terminal field set is **closed** — a record carrying it, or any undefined key, is untrusted evidence under the existing strictness model and never reaches a wire response. Nothing rewrites or migrates a stored record. `arsd` keeps `api_version = 3`. |
| D6 | `LEGACY_SESSION_IDENTITY_FIELDS` and fail-closed refusal of pre-reset Native identity records are preserved and still pinned by their existing tests. |
| D7 | One containment scanner implementation, with unit tests and a runtime-synthesized non-vacuity input. No deliberate violation is committed. |
| D8 | `make verify` green. |

**Hard constraints.** Deletion only — no shim, alias, flag, dual runtime, bridge, or renamed replacement for `acpx_exit_code`. No online migration or dual-format writer. Repository-relative paths only. Runtime stays stdlib-only. Cold archives (`docs/archive/**`, `docs/plans/archive/**`, `docs/roadmap/archive/**`) are not edited. `CHANGELOG.md` dated entries are not rewritten.

**`implementation_authorized = true`** — the operator authorized source, tests, and documentation for exactly
this plan on 2026-08-06; `docs/roadmap/non-approvals.md` records the decision and its boundaries.
**`production_authorized = false`** — see §6. No push, PR, merge, tag, release, publication, deployment,
service action, runtime-data change, or real-agent canary is authorized, and a green gate implies none.

---

## 2. Scope model

**Roots (supported entrypoints):** `agent_run_supervisor.arsd.__main__`, `agent_run_supervisor.arsd.client`, the three CLI leaves in D2, and `agent_run_supervisor.__version__`.

**Rule.** Compute the **transitive symbol closure** from those roots. A symbol is KEPT iff it is in the closure. A file is DELETED iff no symbol of it is; REWRITTEN if partially. Import-time reachability alone never keeps a symbol; conversely, a symbol reached only transitively (via a kept function body) **is** kept. This is the correction that preserves `LEGACY_SESSION_IDENTITY_FIELDS`: `validate_native_binding` → `session.py:1283` → the constant.

**Native identity guard is load-bearing, not legacy.** `validate_native_binding` must keep refusing pre-reset records fail-closed (PRD R11). Its pins at `tests/native_acp/test_session_epoch.py:116`, `tests/native_acp/test_session_switching.py:472`, and `tests/test_reset_boundary_scans.py:104-109` stay green unmodified. Any diff that touches them is out of scope.

**Status enum narrowing is closure-driven, not asserted.** `result.py:228` defines the Native terminal vocabulary as exactly `COMPLETED | FAILED | CANCELLED | TIMED_OUT | UNKNOWN`. Remove `AgentRunStatus` members and `_ERROR_CODE_FOR_STATUS` rows that fall out of the closure once `runner`/`session_runtime` are gone. Members still referenced by `arsd/` or `native_acp/` (e.g. admission and protocol failure codes) stay — the closure decides, member by member, and each removal is justified by a named absent reference.

---

## 3. Inventory

**Delete whole (≈5,000 LOC source):**
`src/agent_run_supervisor/{runner,parser,preflight,session_runtime,live_stream,policy,role,mcp_config,workspace,goal,caller,session_inspect,retention}.py` · `src/agent_run_supervisor/hermes_caller/` (8 files) · `src/agent_run_supervisor/fixtures/` (packaged acpx NDJSON).

**Rewrite per-symbol:**

| File | Treatment |
|---|---|
| `cli.py` | Delete `validate-role`, `replay`, `doctor`, `run` (exec), `session *`, `cleanup` parsers and all acpx help text. `run` survives only as parent of `inspect`. |
| `commands.py` | Delete `cmd_validate_role`, `cmd_replay`, `cmd_doctor`, `_default_fixture_dir`, `cmd_run`, `cmd_session`, `_cmd_session_list`, `cmd_cleanup`, `_candidate_payload`, `_plan_payload`. Keep `cmd_run_inspect`, `cmd_agents*`. Remove module-scope legacy imports that today execute on every native subcommand. |
| `session.py` | Delete `create_session`, `validate_binding`, `_mcp_binding_state`, `SessionRecord.acpx_version`/`.acpx_session_id`, and the four module-scope legacy imports. **Keep** `LEGACY_SESSION_IDENTITY_FIELDS`, `validate_native_binding`, `validate_native_session_record`, `read_native_session_record`. |
| `result.py` | Delete the dead `raw_event_path="acpx-stdout.ndjson"` default (native passes `events.jsonl`); narrow `_ERROR_CODE_FOR_STATUS` with the enum; stop emitting `acpx_exit_code` and remove it from `_REQUIRED_NATIVE_RESULT_FIELDS` and field-specific validation. Do not replace it with `exit_code` or another process-exit field. Close the persisted-terminal field set so an undefined key makes the record untrusted (revised D5); add no tolerant reader and no projection. |
| `exit_classifier.py` | Delete `classify_exit`, `ClassifierInput`, the `acpxCode` branch; narrow the enum per §2. |
| `native_acp/spec.py` | Absorb `PERMISSION_KINDS` (sole native import from `role.py`). |
| `__init__.py`, `pyproject.toml` | Docstring; `description`; `keywords`; drop the `[tool.setuptools.package-data]` acpx fixture entry. |

**Tests — delete:** `tests/test_{parser,parser_incremental,policy,preflight,runner_exec,runner_dry_run,session_runtime,session_store,session_inspect,caller,live_event_stream,role,workspace_gate,session_strategy_guard,mcp_config,goal,retention,smoke_codex_acpx,smoke_persistent_session,validate_contract_fixtures}.py` · `tests/hermes_caller/`.

**Tests — rewrite:** `tests/conftest.py` (drop `FIXTURES_ROOT`, acpx `valid_role_dict`; native tests share this file) · **`tests/test_cli_smoke.py`** (`:44-47` asserts `validate-role`, `replay`, `doctor`, `run` — retarget to the D2 three) · `tests/native_acp/test_status_vocabulary.py` (keep enum and `build_result_payload` pins; drop pins on deleted consumers) · `tests/test_cli_commands.py` · `tests/test_result_event_schema.py` · `tests/test_exit_classifier.py` · `tests/test_reset_boundary_scans.py` (acpx bans move to the single scanner; reset-identity assertions at `:104-109` stay) · `tests/native_acp/test_no_acpx_coupling.py` (delete the two acpx-import tests, now subsumed; **keep** the two SDK-gate tests — retitle the module accordingly).

**Scripts/gates:** delete `scripts/{smoke_codex_acpx,smoke_persistent_session,capture_acpx_contract,validate_contract_fixtures}.py` · rewrite `scripts/verify_local.sh` (drop the fixture-validate, `doctor`, and `replay` steps; add the manifest assertion and dual-artifact smoke) · rewrite `scripts/smoke_installed_wheel.sh` (drop `doctor`/`CODEX_ACPX_OK`, which read the packaged fixture) · **`scripts/release.sh:49`** (advertises `agent-run-supervisor doctor`) · `Makefile` `release-tag` (same string).

**Fixtures:** delete `fixtures/acpx-0.10.0/` and `fixtures/acpx-0.12.0/` (187 tracked files) and `fixtures/README.md`.

---

## 4. Resolved decisions (no operator input required)

- **`acpx_exit_code`: remove in unreleased API v3.** Stop all new writers from emitting the key; remove it from `_REQUIRED_NATIVE_RESULT_FIELDS` and delete field-specific validation, documentation, and tests. Do not rename it. **Revised 2026-08-06 by operator decision:** the project exists only on the development machine and there is no historical persisted data that must remain readable, so no v1/v2 readability is preserved. The persisted-terminal field set is closed: a record carrying the retired key — or any key this version does not define — is untrusted evidence under the existing strictness model, exactly as a malformed field already is. A projection that stripped the key would itself be a reader, and is not taken. Add tests for both directions: a new v3 result without the key is valid, and a record carrying it is refused at the validator and at the reachable `run/status` and `run/cancel` boundaries. No record rewrite, reset, or online migration is part of this plan.
- **`cleanup`: removed from the CLI.** `technical-solution.md` §1.4 and `agent-registry.md:423-425` declare exactly three operator commands and omit it, and `cmd_cleanup` defaults to the legacy roots, not the Native storage seam. No Native redesign exists in source, and this plan does not invent one. `retention.py` and its test leave with the leaf because the closure has no other root. Re-introducing retention requires a design-authority change first — named as follow-up, not performed.
- **Caller layer: delete** `caller.py`, `hermes_caller/`, `session_inspect.py`. Reachable only through `runner`/`session_runtime`, and `docs/roadmap/features.md` carries no feature row for them.
- **Capture tooling: delete** `capture_acpx_contract.py`. Regeneration needs a live acpx being retired, no gate invokes it, and it embeds a host-specific absolute path.
- **Fixture retention: none.** The rule is "retain a fixture only if a named test consumes it to assert a current Native invariant." Inspection finds **no** test under `tests/native_acp/` or `tests/arsd/` referencing any acpx fixture; the only consumers are legacy tests being deleted. `validate_contract_fixtures.py` asserts exact acpx argv ordering, exit codes, and management schema — a compatibility lock, not a comparison. `tests/differential/` is created only if the WP-1 audit names a Native invariant currently asserted solely against an acpx input; the evidence says there is none, and no such input is to be invented.
- **Branch prefix: `feat/`** (per `docs/AI_FLOW.md`), one task branch, one PR.

---

## 5. Work packages

Linear: **WP-1 → WP-2 → WP-3 → WP-4.** WP-2 and WP-3 are co-committed so the gate stays green.

**WP-1 — Authority-backed inventory and RED gate.**
Produce the transitive closure from the §2 roots as a derivation (module + symbol level), including the per-member enum justification and an explicit statement that `LEGACY_SESSION_IDENTITY_FIELDS` and the Native identity refusal are in-closure. It is **review evidence by default** — attached to the PR, not added to the tree. Commit it only if an existing maintained consumer (a gate, a test, or a tool already in `make verify`) requires it as an input; a derivation kept solely for the record becomes a second inventory to maintain and drift against. Add **one** containment scanner as a new category in `tools/static_safety_scan.py`, reusing its existing file walker and path allowlist; bans are shape-based (imports of deleted modules, `npx`/`acpx` argv construction, packaged-fixture reads, present-tense acpx capability claims in current-authority docs), so prose naming acpx to refuse it stays legal. Add its key to the exact counts dict asserted in `tests/test_static_safety_scan.py`, plus focused unit tests and a non-vacuity test that **synthesizes a violating tree at runtime** — nothing violating is committed.
*Acceptance:* the scanner fails on the tree at the reviewed commit and is the only failure; the enum/closure derivation names every member removed and why.

**WP-2 — Runtime, package, CLI, and result-field removal.**
Everything in §3 source/test/script rows. Includes the `pyproject.toml` metadata and package-data edits, dropping the now-deleted `preflight.py` entry from the scanner's `SYNTHETIC_SECRET_PATHS`, removing `acpx_exit_code` from all new result builders and required-field validation, and adding the closed-contract tests defined in §4 (revised D5: a v3 result omits the key; a record carrying it is refused).
*Acceptance:* WP-1 scanner green; `compileall` clean; `tests/arsd` and `tests/native_acp` pass with no modification to the identity-guard pins.

**WP-3 — Distribution boundary.**
Commit an expected-manifest allowlist for the wheel and the sdist. Assert both **inside `verify_local.sh`** as an exact set comparison (not a `grep` observation, which also inverts its exit status when the desired count is zero): no path matching `acpx`, none of the deleted modules, no `fixtures/` package data. Install and smoke **both** artifacts in their own venvs, each asserting the D2 subcommand set, an acpx-free `--help`, `import agent_run_supervisor.arsd`, and the `--print-service-unit` refusal. Reuse the single build `verify_local.sh` already performs — no second `make build`.
*Acceptance:* `make verify` green including the two new assertions.

**WP-4 — Documentation, generated indexes, final review.**
Rewrite to past tense / refused-surface: `GOAL.md` § acpx boundary (keep the negative constraints); `docs/product/prd.md`; `docs/design/architecture.md` (rename §10 "Legacy coexistence and rollback" — "coexistence" implies a dual surface that no longer exists); `docs/design/technical-solution.md`; **`docs/design/result-event-schema.md`** (largest rewrite: delete the acpx field rows including `acpx_exit_code`, exit-code table, parser-normalization text, and doctor/preflight probe section; document that API v3 results no longer carry a process-exit field); `docs/roadmap/features.md` (`F-LEGACY-COMPAT-001` → Removed; evidence cell ≤120 chars); `docs/roadmap/current-status.md` (close the `:46` gate; board ≤180 lines, ≤5 PR refs, no SHA tokens); `docs/roadmap/non-approvals.md` (record the decision; **retain** the `:36` prohibition); `docs/roadmap/verification.md`; `docs/AI_FLOW.md`; `AGENTS.md`. Confirm both READMEs — they already document only the native surface, so no documented capability is lost; remove nothing else. Add a forward note to the unreleased `CHANGELOG.md` section without editing dated entries. `git mv` the completed no-close plan to `docs/plans/archive/`. Add this plan to `docs/plans/active/` **with required frontmatter and no host-specific path** at the start of implementation only, so it is active exactly while the work is in flight. Once WP-4's content edits are complete and before the final review, `git mv` this plan to `docs/plans/archive/` with `status: archived` and set `active_plan:` to the next board-linked plan, or clear it if none — no completed plan may remain active, and the final review sees an empty or forward-pointing `docs/plans/active/`. Regenerate `docs/INDEX.md` and the drift report with their tools; never hand-edit either. Then run `make verify` and hold one fresh-context review of the exact merge candidate: every closure KEEP symbol still called, no DELETE symbol shimmed or re-exported, identity-guard pins untouched, manifest assertion output pasted into review evidence, `acpx_exit_code` absent from all new-result paths and current-authority docs, a record carrying it refused at the validator and at `run/status`/`run/cancel`, and scanner counts exact. Mutation guard before/after; any mutation invalidates the verdict.

---

## 6. Rollback and non-approvals

**Rollback before deployment** is an ordinary `git revert` of the single merge commit. **Rollback after any deployment is not implicitly safe:** API v3 may already have written valid records without `acpx_exit_code`, while the reverted validator would require it and classify those records as corrupt. A deployed rollback therefore requires a separately authorized runtime-data decision (discard/reset the v3-written records, or ship a forward compatibility fix); this plan authorizes neither. There is no dual runtime, bridge, flag, or dual-format writer.

**Not authorized, and not implied by a green gate or a merge:** release, tag, GitHub Release, PyPI/TestPyPI publication · deployment, install, or rollout · `arsd` or Gateway restart, enablement, or any service action · runtime-data migration, reset, or archival beyond the specifically approved new-writer field deletion above · real external-agent canary or any live agent run · Sachima or any caller-platform integration · public ingress · push, pull request, merge, or tag · branch/worktree cleanup.

---
