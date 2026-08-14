# Known-issue verdict reclassification

Use this when a permission batch exposes a known adapter defect and the operator asks to report it as `WARNING` rather than `FAIL`.

## Core rule

A normalized ACP/ARS event stream can be a **lossy projection**. An outer `kind=execute` may represent a genuine command, a code-mode wrapper, or an MCP/Apps call. If the normalized event does not retain a source-owned discriminator for the inner operation, the controller cannot prove which one occurred.

Do not infer the inner operation from:

- successful read output or unchanged workspace;
- absence of shell-shaped text in the normalized receipt;
- tool-call ID prefixes;
- event counts or ordering;
- AGENT self-report;
- route name, CLI/adapter version, or a public issue alone.

Those facts can narrow a diagnosis but cannot turn an observationally identical real execute into a known wrapper defect.

## Safe decision sequence

1. **Preserve the base verdict and facts.** Keep the structured `permission_violation`, terminal detail, mediation history, effect/workspace evidence, and receipt. Never relabel the underlying operation as `read` or erase the violation.
2. **Test discriminability before coding a WARNING rule.** Construct a negative control containing the same expected read/effect facts plus a separate genuine unmediated execute. If the proposed predicate also warns on that control, automatic reclassification is unsafe.
3. **Require a source-owned discriminator.** Automatic WARNING is acceptable only when structured evidence distinguishes the inner operation, such as a typed nested operation/MCP identity emitted by the adapter or persisted by ARS and correlated to the violating tool call.
4. **If the event contract is insufficient, stop for a product choice:**
   - keep the controller FAIL and use a two-stage report backed by a Run-bound post-run attestation from raw adapter evidence;
   - explicitly accept a heuristic WARNING and document that it can mask observationally identical real executes; or
   - expand the adapter/ARS event contract to persist the discriminator.
5. **Honor an explicit broad-classification choice proportionally.** When the operator knowingly chooses the heuristic for a bounded test script, do not keep rebuilding product-grade discrimination or attestation machinery. Implement the smallest named predicate matching the approved scope (for example route + Case + structured violation family), state plainly that it may include genuine operations, preserve the raw violation evidence, and keep unrelated routes/Cases/violation families fail-closed.
6. **Bind any attestation to the exact Run evidence when attestation was the chosen path.** Include a source-owned warning code, Case/route, Run or receipt digest, violating tool-call correlation, evidence provenance, and the unchanged raw verdict. A free-form note or global version allowlist is not enough.

## Proportional regression matrix

Match tests to the selected product boundary:

- For a deliberately broad, test-only heuristic, prove the exact approved positive scope plus the nearest exclusions: another route, another Case, absent/unknown kind, and mixed violation families. Also prove aggregation, exit-code semantics, and raw-evidence retention. Do not reintroduce rejected discriminator requirements through tests.
- For a claim of precise known-defect identification, add the full fail-closed matrix below:

- another route and another Case;
- missing, unknown, and mixed violation kinds;
- a separate genuine execute tool call;
- execute mediation present (allow or deny);
- expected-family decisions in both directions, in either order;
- terminal detail absent, unrelated, or detail-only;
- `completed` rather than the exact known failed terminal;
- effect missing or workspace side effect present;
- config, Session, stop reason, and process-reap defects;
- ordinary verdict behavior, including conditional-allow Cases where “no supported allow” differs from “allow and deny both observed.”

Worst-verdict aggregation and exit-code semantics must be explicit. `WARNING` must remain distinct from `PASS`, and receipts must retain the categorical violation evidence even when `first_failure` is null.

## Reviewer stop condition

A green deterministic suite is not enough. Freeze the exact candidate and obtain a fresh read-only review focused on false-warning reachability. If the reviewer demonstrates an observationally identical genuine violation that matches the predicate, treat it as a blocker—not speculative hardening—and return to the product-choice step instead of adding more event-count heuristics.
