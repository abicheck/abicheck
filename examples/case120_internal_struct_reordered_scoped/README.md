# Case 120: Internal struct fields reordered (non-public, scoped)

**Category:** Public-surface scoping (ADR-024) | **Verdict:** ✅ NO_CHANGE (exit 0, with `--scope-public-headers`)

## What changes

The fields of `struct InternalStats` are reordered (`calls`/`total` swapped),
changing field offsets. Reordering members is normally an ABI break — but
`InternalStats` is **not on the public ABI surface**: no exported,
header-declared public function reaches it. The public API (`Point`,
`translate`) is unchanged.

## Why this is not a false positive

A consumer linking against the public API cannot observe the internal layout of
`InternalStats`, so reordering its fields cannot break it. With ADR-024
public-surface scoping the offset change is recorded in the filtered audit
ledger rather than reported, and the verdict is `NO_CHANGE`.

```bash
abicheck compare libfoo_v1.so libfoo_v2.so -H v1.h \
    --scope-public-headers --show-filtered
# verdict: NO_CHANGE (exit 0); filtered finding(s) on InternalStats
```

This relies on reachability, not on a name heuristic: the moment a public API
references `InternalStats`, the same reordering is reported as breaking — so
scoping reduces noise without hiding real breaks.

## How to fix

N/A — confined to an internal type, this is the intended compatible outcome.
