# Direct-controller evidence contract

## Fresh local root

`--output-dir` must name a path that does not exist. The controller creates it exclusively and writes only beneath it:

```text
<output-dir>/
├── raw/
│   ├── server-info.json
│   └── per-case categorical receipts
├── workspaces/
│   └── per-case disposable workspaces
└── summary.json
```

The separate `--supervisor-root` is read-only controller input used to inspect `native-runs/<run_id>/effective.json` and `spec.json`. It is not copied into shared output.

## Local-only evidence

Raw receipts may retain request, Run, and Session IDs; exact operator route literals; event-family counts; durable check booleans; and categorical failures. They do not retain prompts, response-only `final_message` values, or Session continuity message values.

The Session controller keeps the fresh token only in memory and never copies its plaintext, either prompt, or either final message into controller-owned evidence. S2 necessarily returns the token through ARS, so the separately managed ARS Run record can contain that AGENT-authored final message; keep the supervisor state root local under its own retention policy.

## Shareable projection

`summary.json` and stdout are identical sanitized JSON projections. They may contain:

- controller/schema identity and the live package/API version;
- stable operator-supplied agent IDs and exact model/effort routes;
- per-round or per-route `PASS`, `FAIL`, or `INDETERMINATE`;
- one stable categorical `first_failure`.

They exclude absolute paths, output/evidence location, owner/namespace, registry path, request/Run/Session IDs, prompts, final messages, the continuity token, process IDs, event bodies, raw exceptions, and provider-authored free-form text. Use stable categories such as `SUBMIT`, `CONFIG_FIDELITY`, `SESSION_CHANGED`, `TOKEN_MISMATCH`, or `PROCESS_REAP_UNPROVEN`; never copy exception text into shared output.

## Interpretation

- Response-only PASS proves task-delivery chain health for the exact tested route. Content correctness and quality are out of scope.
- Session-reuse PASS proves create/load continuity only for the exact tested route. The exact token comparison is continuity evidence.
- Permission PASS is outside these controllers and requires its separate real canary.
- Process reap is checked from each Run's recorded identity. Missing or unverifiable identity cannot silently pass.
- ARS supervises registered external AGENT commands; this skill does not install, manage, or attest them.
