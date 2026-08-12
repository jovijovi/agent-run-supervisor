# Evidence and adjudication contract

## Raw local controller evidence

The runner creates a new output directory and refuses any existing path, including a symlink. It writes controller evidence exclusively and never replaces a prior artifact:

```text
<evidence-directory>/
├── controller-manifest.json
├── completion.json
├── cases/<case-ref>.json
├── events/<case-ref>.jsonl
└── workspaces/<case-ref>/
```

`controller-manifest.json` records the server preflight response and the planned ordinal case references. A case JSON contains the raw request, prompt, task-checker argv, request/Run/Session correlation values, terminal and Session observations, checker output, and stable controller errors. Event files contain the raw normalized Socket API events. Workspaces contain task effects and checker effects.

Event collection refuses non-advancing or empty open pages, pages above the configured page size, and totals above the case's declared `max_events`; such a case remains non-exhausted and cannot receive a settled `PASS`.

Treat the entire directory as sensitive local material. ARS deliberately does not scan agent-authored free-form evidence against projected environment values, and neither does this controller. The directory may therefore contain credentials or identity values echoed by an AGENT or checker. Filesystem permissions are not encryption or a disclosure-control guarantee.

## Independent sanitized receipt

`adjudicate.py` reads only the completed controller bundle. It never contacts arsd, submits a Run, replays a prompt, invokes a checker, or reads the operator registry.

For each ordinal case reference it recomputes:

- `transport_ars_terminal`: whether submission and terminal observation produced a trustworthy completed, failed, or indeterminate ARS outcome;
- `configuration_fidelity`: exact owner, namespace, agent, requested model, and requested effort versus the Session's observable API projection, or a categorical configuration failure; the receipt exposes only the equality verdict;
- `task_checker`: checker exit, non-zero result, timeout, or unavailable result;
- `execution_constraints`: required/forbidden normalized event types and mediation decisions;
- `settled_state`: terminal present, events fully paged, Session observed and not quarantined, checker observed; and
- `business_verdict`: `PASS` only when every preceding axis passes. A completed Run alone is insufficient.

The receipt excludes absolute paths, user/account and caller identity, agent identifiers, request/Run/Session IDs, socket/state/registry locations, environment material, credentials, prompts, final messages, event bodies, checker argv, and free-form checker output. Where the excluded category must be represented it uses `[REDACTED]`. It carries no raw error or child text.

The adjudicator also refuses to overwrite an existing receipt.

## Privacy and proof limits

- The receipt is sanitized by construction from a small allowlist; it is not a general-purpose redactor for arbitrary files.
- The checker argv is trusted operator code. `cwd` selects the case workspace but is not a filesystem boundary; the checker retains every authority of its launching account and must treat AGENT-created workspace content as untrusted.
- The operator-supplied agents file is a preflight view, not proof that an already-running daemon loaded identical bytes. The daemon's submission result remains authoritative for its immutable snapshot.
- A terminal, exhausted event page, and non-quarantined Session are the portable settled facts available through the Socket API. They do not prove provider-side cleanup, descendant containment, or absence of work outside ARS's process group.
- Mediation and event constraints prove only what the API/controller artifacts show. They do not turn cooperative mediation into a sandbox or hostile-code boundary.
- A business `PASS` is the controller's checker-based decision. ARS continues to emit `business_verdict: null` and owns no business result.

## Optional operator checks outside the portable skill

If a deployment's acceptance policy also requires service-manager health, cgroup membership, process-table inspection, daemon state-root inspection, daemon/provider logs, network isolation, or resource accounting, perform those as separately authorized operator checks. Keep their evidence outside this generic bundle and do not claim the portable receipt proves them.
