---
title: "Roadmap and plans path migration"
status: active
created_at: 2026-07-07
last_validated_at: 2026-07-07T15:30:00+0800
---
# Roadmap and plans path migration

Map for bookmarks and historical references after the 2026-07 roadmap governance
restructure.

## `current-status.md` sections

| Old reference | New location |
|---|---|
| `current-status.md` §3 `<Phase>` | [`archive/phases/<slug>.md`](archive/phases/) |
| `current-status.md` §4 open tails | [`current-status.md#open-tails`](current-status.md#open-tails) |
| `current-status.md` §4 closed tails | [`archive/tails.md`](archive/tails.md) |
| `non-approvals.md` | [`non-approvals.md`](non-approvals.md) |
| `verification.md` | [`verification.md`](verification.md) |
| `current-mainline` YAML block | [`current-status.md#snapshot`](current-status.md#snapshot) |
| `#5-current-explicit-non-approvals` | [`non-approvals.md`](non-approvals.md) |

## Implementation plans

| Old path | New path |
|---|---|
| `docs/plans/YYYY-MM-DD-*.md` | `docs/plans/archive/YYYY-MM-DD-*.md` |
| New in-progress plan | `docs/plans/active/YYYY-MM-DD-*.md` |

## Review requirements

Former `current-status.md` §7 → [`docs/AI_FLOW.md`](../AI_FLOW.md) (Review requirements).
