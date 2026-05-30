# Case 118: Internal struct gains a field (non-public, scoped)

**Category:** Public-surface scoping (ADR-024) | **Verdict:** ✅ NO_CHANGE (exit 0, with `--scope-public-headers`)

## What changes

`struct InternalStats` gains a new field (`int errors;`). That is a real
layout change for `InternalStats` — but `InternalStats` is **not part of the
public ABI surface**: no public, header-declared, exported function takes,
returns, or otherwise reaches it. The public API (`Point`, `translate`) is
unchanged.

## Why this is not a false positive

A consumer can only depend on what the public API exposes. Since nothing public
reaches `InternalStats`, growing it cannot break any caller that uses only
`translate()`/`Point`. Reporting it would be a **false positive**.

With ADR-024 public-surface scoping enabled, abicheck resolves the public
surface (symbols with `PUBLIC` visibility + their reachable type closure) and
moves the `InternalStats` layout change to the *filtered* audit ledger rather
than reporting it. Internal-type *leaks* (a private type reachable from a public
API) are never hidden — only genuinely non-public changes are filtered.

```bash
abicheck compare libfoo_v1.so libfoo_v2.so -H v1.h \
    --scope-public-headers --show-filtered
# verdict: NO_CHANGE (exit 0)
# filtered ledger lists: type_size_changed: InternalStats
```

Without `--scope-public-headers`, the same comparison reports the
`InternalStats` layout change — useful when you intend to audit the entire
exported surface, not just the public-header API.

## How to fix

N/A — this is the intended, compatible outcome for changes confined to
internal types. If `InternalStats` were actually used by a public API, the
reachability closure would keep it on the surface and the change would be
reported.
