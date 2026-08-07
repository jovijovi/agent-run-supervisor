---
title: Reference
description: The caller-stable contracts — socket API, agents file, events, results, and error codes.
---

# Reference

The contracts a caller and an operator can rely on.

| Page | Contract |
|---|---|
| [Socket API](socket-api.md) | the `arsd` API v3 wire protocol: envelope, operations, request fields, bounds |
| [Agents file](agents-file.md) | the operator registry: grammar, complete field set, bounds, refusal rules |
| [Events](events.md) | the normalized, `seq`-ordered event stream |
| [Results](results.md) | the persisted terminal `result.json`, status vocabulary, and Session projection |
| [Error codes](error-codes.md) | every code, what it means, and what to do about it |

For the Python client surface, see the [API Reference](api/index.md).

## Stability rules that apply everywhere

- **`business_verdict` is always `null`.** The supervisor never sets a business
  verdict, and supervisor status is not a pass/fail judgement about the work.
- **Field sets are closed.** A persisted terminal carrying a key this version
  does not define is untrusted evidence, not a tolerated extension. There is no
  reader for an unknown key, no projection that strips one, and no alias.
- **Existing keys are never renamed, removed, or repurposed.** Future changes may
  *add* keys, documented here in the same change as the emitter.
- **Nothing migrates a stored record.** An untrusted terminal simply never
  becomes trusted evidence; ARS does not rewrite, repair, or re-hash it.
- **API v3 is the only contract.** Every request envelope carries `api_version`,
  and anything other than `3` is refused rather than guessed — for every
  operation, including `server_info`. There is no drain window, dual protocol, or
  alias.
