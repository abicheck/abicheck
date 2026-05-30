# Case 119: Internal struct loses a field (non-public, scoped)

**Category:** Public-surface scoping (ADR-024) | **Verdict:** ✅ NO_CHANGE (exit 0, with `--scope-public-headers`)

## What changes

`struct InternalStats` drops a field (`int errors;`). Removing a field is
normally an ABI break for that struct — but `InternalStats` is **not on the
public ABI surface**: no exported, header-declared public function reaches it.
The public API (`Point`, `translate`) is unchanged.

## Why this is not a false positive

Downstream consumers only observe the public API. Since nothing public reaches
`InternalStats`, removing one of its fields cannot break a caller that uses only
`translate()`/`Point`. With ADR-024 public-surface scoping the finding is moved
to the filtered audit ledger (visible via `--show-filtered`) instead of being
reported, and the verdict is `NO_CHANGE`.

```bash
abicheck compare libfoo_v1.so libfoo_v2.so -H v1.h \
    --scope-public-headers --show-filtered
# verdict: NO_CHANGE (exit 0); filtered: type_field_removed: InternalStats
```

Scoping never hides a *leak*: if `InternalStats` were reachable from a public
API, the reachability closure keeps it on the surface and the removal is
reported as breaking.

## How to fix

N/A — confined to an internal type, this is the intended compatible outcome.
