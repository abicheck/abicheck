# case151 — Provider-agreement matrix (corroboration grows with evidence)

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Cross-check:** `private_header_leak` ·
**Mode:** single-release audit · **Evidence tier:** L2

## What it demonstrates

"Better results from the *combination*" as a measurable output property. The same
`PRIVATE_HEADER_LEAK` finding is recorded with a **different provider list**
depending on how much evidence is available — the §6.8 provider-agreement matrix.

| Fixture | Evidence present | Providers recorded for the finding |
|---------|------------------|------------------------------------|
| `thin.abi.json` | public-header AST only | `public_header_ast` (1) |
| `snapshot.abi.json` | header AST **+ L5 source graph** | `public_header_ast`, `source_index` (2) |

Both fixtures flag the *same* leak — the finding does not change — but the rich
fixture's source graph **corroborates** it with a second, independent provider.
That provider list is the available corroboration signal ScanResult records.

> **Scope.** This case asserts the provider *list* differs. Deriving a per-finding
> confidence *tag* from the provider count (so 1-provider corroboration renders a
> weaker tag than 2) is a separate reporting enhancement, not part of this corpus.

## Reproduce

```bash
abicheck scan --audit libdemo.so -H include/                 # thin: 1 provider
abicheck scan --audit libdemo.so -H include/ --sources .     # rich: + source_index
```

## Fix

Same as any private-header leak (see case144): opaque-handle the internal type or
install its header.
