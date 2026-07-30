---
title: "Why the Binding era was retired"
status: archived
created_at: 2026-07-30
archived_at: 2026-07-30
deprecated_reason: "Historical rationale for a completed retirement; not a current requirement"
---
# Why the Binding era was retired

This is the record of *why* the authority in this directory was retired, written at the time of the
retirement. It argues about a design that no longer exists; it defines nothing.

## 1. The boundary was wrong, not the code

The Binding era asked ARS to own an **artifact identity** for software it did not own. To keep such an
identity meaningful, ARS also had to own the install location and the upgrade path — which is how
`/opt/agent-run-supervisor/artifacts/` came to hold other projects' interpreter and adapter trees, forked
from their own upgrade paths.

Retirement is a boundary correction. ARS owns the *process it starts*; the user and operator own the
*software it starts*, including installation, configuration, credentials, `HOME`, plugin and cache state,
and upgrades.

## 2. What the cost actually bought

On the retired line, a routine AGENT upgrade cost: install under a root-owned prefix, measure, author a
generation manifest, `runtime-binding validate`, `runtime-binding promote`. An *adapter* upgrade cost more
— a new adapter tree digest and entry digest, a revision bump, therefore a new `adapter_contract_hash`,
therefore every existing Binding generation **and** every existing Session failing closed, therefore
re-acceptance plus a fresh permission canary.

The retired board recorded that consequence honestly, as a list of operator gates that never emptied.

That price bought an artifact-integrity claim ARS could not sustain under its own ownership boundary, and
it converted an integrity gate into a **continuity** gate: `adapter_contract_hash` and
`session_compatibility_epoch` were Session-identity fields compared by symmetric equality, so a dependency
patch retired live conversations. The AGENT's own ability to `session/load` — the only real evidence of
continuity — was never consulted.

## 3. What is genuinely lost

Stating this plainly matters more than defending the reset.

ARS no longer detects a swapped or modified executable. That detection was real *as detection*; removing
it is a deliberate trade, not a discovery that it did nothing. The replacement is operator-owned OS and
deployment integrity — package signatures, immutable images, filesystem permissions, host integrity
tooling — plus per-Run recorded evidence for after-the-fact audit.

## 4. What was never gained

The detection ran as the same UID that then executed the artifact with that UID's full authority, so it
never bounded what a byte-identical agent could do. Retiring it reduces no containment, because it
provided none. Attestation and isolation were never the same claim, and the retired documents were careful
about that; the reset keeps that care and drops the apparatus.

## 5. Why each retired concept had to go, not just be simplified

| Retired concept | Reason |
|---|---|
| Artifact materialization under an ARS-owned prefix | ARS hosting other projects' executables is the ownership violation itself |
| Package closure, install root, tree digest, frozen interpreter identity, out-of-closure search prefix | freezes an adapter's dependency tree against its own upgrade path |
| Binding root, active pointer, generations, manifests, validate/promote/rollback, acceptance receipts | a promotion per upgrade is the cost §2 describes |
| Digest, ownership, mode, ancestor, and TOCTOU gates; descriptor-pinned exec | ownership overreach, and the image pin also broke ordinary PATH-shim semantics |
| Credential-root structural inspection; project-config refusal | credentials and project configuration are AGENT-owned |
| `adapter_contract_hash`, ARS-derived compatibility epoch, registration hash as Session identity | turns routine upgrades into continuity loss |
| Source-frozen model/effort value domains; version probe as a gate | live discovery plus exact readback is stronger and needs no release |
| Per-agent source profiles | one standard profile plus one evidenced compatibility profile is enough |
| Environment **values** in launch material, hash inputs, and inspect output | no ARS-owned sink may carry a projected environment value |

## 6. What retirement did not touch

- Production `v0.5.3`, its promoted generations, and the live `/opt` trees. They stop being referenced;
  they are not deleted, and their removal is a separate operator decision.
- Historical Run and Session bytes. They remain immutable. New readers project them value-blind; nothing
  is rewritten, migrated, re-hashed, or erased, and the reset makes no retroactive-erasure claim.
- Everything ARS actually differentiates on: caller authentication, owner scoping, immutable per-Run
  grants, one supervised local process per Run, exact configuration fidelity, default-deny permission
  mediation, bounded redacted evidence, irreversible terminal facts, no-replay recovery, and total
  fail-closed startup reconciliation. All of it survived the removal untouched, which is the whole
  argument for making it.

## 7. The decision that authorised this

Retiring three registered per-agent profiles was forbidden by the then-current non-approvals, which also
recorded that *adding* a retirement mechanism and *using* one are two separate decisions. The
policy-level decision was taken explicitly, in writing, on 2026-07-30, as **option (a): retire the
per-agent profiles as part of the reset**.

That decision is policy. It authorised this documentation retirement and the authority chain that replaced
it. It did **not** authorise deleting profiles from source: that execution step needs its own separate
confirmation, which the current non-approvals still record as untaken.
