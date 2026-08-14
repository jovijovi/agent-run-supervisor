# Direct-controller evidence contract

## Fresh local root

`--output-dir` must name a path that does not exist. Its **parent directory must already exist and be writable**; controllers create the leaf output root exclusively and intentionally do not create missing ancestors. Preflight both facts before launch. A missing parent yields the stable pre-submission category `OUTPUT_CREATE`.

If `OUTPUT_CREATE` occurs, do not call it an AGENT failure or Case retry. First prove there was no successful submit acknowledgement, no controller-created output root, and no corresponding new durable Run evidence. Only after that proof may the operator create the missing parent and begin the intended first Case attempt. The controller then creates the leaf directory and writes only beneath it:

```text
<output-dir>/
├── raw/
│   ├── live-policy.json
│   └── per-case categorical receipts
├── workspaces/
│   └── per-case disposable workspaces
├── outside/
│   └── per-case out-of-workspace fixtures (permissions controller only)
└── summary.json
```

`live-policy.json` is a closed projection of the live policy numbers — served API version, diagnostic
package version, concurrency, page and prompt limits, event-budget ceiling. The `server_info` document itself
is never persisted: it is daemon-authored and may carry operator, host, or deployment detail this skill has
no business retaining.

The separate `--supervisor-root` is read-only controller input used to inspect `native-runs/<run_id>/effective.json` and `spec.json`. It is not copied into shared output.

## Local-only evidence

Raw receipts may retain request, Run, and Session IDs; exact operator route literals; event-family counts; durable check booleans; and categorical failures. They do not retain prompts, response-only `final_message` values, or Session continuity message values.

The Session controller keeps the fresh token only in memory and never copies its plaintext, either prompt, or either final message into controller-owned evidence. S2 necessarily returns the token through ARS, so the separately managed ARS Run record can contain that AGENT-authored final message; keep the supervisor state root local under its own retention policy.

## Permission evidence

A permission Case additionally retains, locally: the Case's frozen grant capabilities; a recursive workspace
manifest taken before and after the Run that records each entry under a **digest of its relative path**, with type, size, and content hash
and **never follows a symlink**; a count and the path digests that changed beyond the Case's expected effect;
the sanitized
observed mediation decision; and whether a tool attempt of the expected kind was seen.

A filename is chosen by whoever created the file, and inside a supervised workspace that is the AGENT, so no
observed name or relative path is persisted anywhere — not as a manifest key, not in the changed-entry list,
not in an error. The digest keeps every comparison the controller makes: presence, absence, replacement, and
change. Only the Case's own fixed target name, a literal from the source-owned case table, is named — and it
is addressed through the same digest in the manifest.

Untrusted text never lands verbatim. A mediation `requested_op`, a tool `kind`, an event `type`, and a
terminal `status`/`stop_reason`/`detail_code` are each recorded against a closed source-owned vocabulary —
anything outside it becomes `other`, an absent field becomes `absent` — and no reason string is copied. A
tool-call identifier is agent-authored, so only its digest is kept: that still correlates a decision to an
attempt and still separates two calls. A symlink is recorded as a target digest plus whether the target is
absolute and whether it stays inside the manifest root, so a repointed or replaced link is still detected
without publishing host layout. The target is read once and compared as text, never resolved: resolving would
walk outside the Case boundary, and a link an AGENT points at a cycle would abort the batch instead of
producing evidence. "Inside the root" is therefore a lexical statement about the recorded target.

Effect evidence is existence, an exact-token match boolean, and a body hash. The reply text is never copied; it
remains in the separately retained ARS durable artifacts.

Workspaces are never cleaned up. When a refusal produced a side effect it should not have, the workspace and
both manifests are preserved exactly as found — the evidence is the point.

## Shareable projection

`summary.json` and stdout are identical sanitized JSON projections. They may contain:

- controller/schema identity and the live package/API version;
- stable operator-supplied agent IDs and exact model/effort routes;
- per-round, per-route, or per-Case `PASS`, `FAIL`, `INDETERMINATE`, or — for a permission Case —
  `UNSUPPORTED`;
- one stable categorical `first_failure`.

They exclude absolute paths, output/evidence location, owner/namespace, registry path, request/Run/Session IDs, prompts, final messages, the continuity token, workspace manifests and file names, process IDs, event bodies, raw exceptions, and provider-authored free-form text. Use stable categories such as `SUBMIT`, `CONFIG_FIDELITY`, `SESSION_CHANGED`, `TOKEN_MISMATCH`, or `PROCESS_REAP_UNPROVEN`; never copy exception text into shared output. The permission controller adds `PERMISSION_VIOLATION`, `REFUSAL_INEFFECTIVE`, `UNEXPECTED_ALLOW`, `UNEXPECTED_DENY`, `MEDIATION_ABSENT`, `CONDITIONAL_ALLOW_UNAVAILABLE`, `TOOL_ATTEMPT_UNPROVEN`, `EFFECT_UNPROVEN`, `SESSION_BINDING`,
`TERMINAL_UNTRUSTWORTHY`, and `CONTROLLER_DEADLINE`; its projection also carries the mode and the case ids it ran. Live package and API versions stay diagnostic: no verdict reads one, an absent or unusable package version
is projected as the sentinel `unreported` and never stops a batch, and no projection ranks AGENTs or
reports a pass rate.

## Independent post-run verification

Treat controller outputs as immutable evidence. Derive the expected receipt set from the source-owned fixed case table and route list: response-only expects one receipt for every `R1/R2/R3 × route`, while Session reuse expects one paired S1/S2 receipt per route. Verify counts, single-submission fields, unique Runs/Sessions, exact configuration checks, continuity checks, process reap, summaries, daemon state, and repository delta.

A read-only helper or aggregation failure is an evidence-inspection problem, not an AGENT Case failure and not permission to replay. Keep the original outputs, switch to direct fixed-path receipt inspection, and report the helper issue separately if it matters. Never repair, normalize, or rewrite receipts to make aggregation succeed.

## Interpretation

- Response-only PASS proves task-delivery chain health for the exact tested route. Content correctness and quality are out of scope.
- Session-reuse PASS proves create/load continuity only for the exact tested route. The exact token comparison is continuity evidence.
- Permission PASS proves that the currently installed chain mediated exactly that fixed Case as expected. It is
  not a version, provenance, or isolation claim, and it is never derived from an AGENT's self-report.
- `UNSUPPORTED` records that a chain did not mediate a family at all. It is never reported as PASS, and never
  inferred from a declared capability.
- Process reap is checked from each Run's recorded identity. Missing or unverifiable identity cannot silently pass.
- ARS supervises registered external AGENT commands; this skill does not install, manage, or attest them.
