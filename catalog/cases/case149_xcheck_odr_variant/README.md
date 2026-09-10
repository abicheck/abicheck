# Case 149: ODR Type Variant (Cross-Source, L4 Layout ↔ Layout)

**Category:** API Break (Audit) | **Verdict:** 🟠 API_BREAK

## Verdict and consumer impact

Single-release audit: one build's evidence checked against itself, no
baseline comparison. This finding's own kind is classified `API_BREAK` (the severity
ground-truth row above); the audit itself reports **no** compatibility
verdict and, by default, exits `0` — an audit has no baseline to break
against (ADR-068 D2), so it never emits `2`/`4`, the compatibility family's
own break codes. Gating on a hygiene finding like this one is opt-in via the
orthogonal audit-gate axis
([ADR-068's 2026-09-10 amendment](../../../docs/contribute/adr/068-one-comparison-product-and-scan-retirement.md#amendment-2026-09-10-the-audit-gate-exit-axis)):
adding `--severity-preset default` (or `strict`) reproduces legacy `scan`'s
gating decision on this exact finding, but through its own exit code `3`,
never `2`, so a hygiene gate can't be mistaken for a real compatibility
break — measured live: `abicheck compare --no-baseline snapshot.abi.json`
exits `0`, `abicheck compare --no-baseline snapshot.abi.json
--severity-preset default` exits `3`. The finding: two translation
units materialize **one** public type, `geometry::Vec3`, with **different
layouts** (one TU's definition carries an extra member behind a macro the
other TU does not see). This is an ODR violation: the linker picks one
definition arbitrarily, and call sites compiled against the *other* TU's
layout read the wrong bytes at runtime. The shipped binary contains exactly
one layout, so nothing about the binary itself looks wrong — the conflict
only exists in the relationship between the two TUs' own source-replayed
definitions.

## What this snapshot contains

`snapshot.abi.json` is a single, hand-built `AbiSnapshot` carrying the L4
per-TU source-ABI replay for two translation units that both define
`geometry::Vec3`:

| Source in the snapshot | What it records |
|---|---|
| Per-TU source-ABI surface (L4, `build_source.source_abi` per TU) | TU-A's replayed `geometry::Vec3` layout hash and TU-B's replayed `geometry::Vec3` layout hash, and they differ |

## abicheck command

```bash
abicheck compare --no-baseline snapshot.abi.json
```

!!! note "`compare --no-baseline`, not `scan`"
    [ADR-068](../../../docs/contribute/adr/068-one-comparison-product-and-scan-retirement.md)
    D2 makes this the declared spelling for a single-build audit, and
    retires `scan`. This case was blocked on that migration until
    2026-09-09; the audit now reports the finding below directly, and
    `tests/parity/test_no_baseline_audit_corpus_parity.py` pins that it
    reports at least every check `scan` does, counted per finding kind,
    while manufacturing no comparison of its own (no verdict, no
    `changes[]` entry).



## Expected abicheck finding

```text
# ABI audit: libdemo.so (no baseline)

OLD side: **declared absent** (`--no-baseline`) -- this is an audit of the candidate build alone, not a compatibility comparison. No additions, removals, or compatibility verdict are reported.

- Candidate version: `1.0`
- Acquisition state (OLD): `declared_absent`
- Evidence tiers: header

## Candidate-side findings

| Finding | Symbol | Severity | State | Detail |
| --- | --- | --- | --- | --- |
| `odr_type_variant` | `geometry::Vec3` | potential_breaking | present in this build | Type 'geometry::Vec3' has divergent per-translation-unit definitions in 'include/geometry/vec3.h': the source-replay surface recorded different layouts for the same type. Linking code that mixes them is undefined behavior — a consumer compiled against one layout silently reads the other. Reconcile the definitions (usually a macro/flag that changes the type per TU). |
```

## Minimum evidence

`min_evidence: L4` — the shipped binary (L0/L1) and the header AST (L2)
each see exactly one `geometry::Vec3` definition and look internally
consistent; only replaying each translation unit's own source (L4) and
comparing the *per-TU* layouts against each other exposes that two TUs
disagree about the same type's layout.

## Why abicheck catches it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | one `geometry::Vec3` layout — looks self-consistent |
| Header AST (L2) | one declaration — looks self-consistent |
| Per-TU source-ABI replay (L4) | TU-A's `Vec3` hash ≠ TU-B's `Vec3` hash |
| **Combination** | the L4 surface records the per-TU conflict → `odr_type_variant` (API_BREAK) |

`odr_type_variant` reads the L4 source-replay surface's recorded per-TU
type hashes, supplied by the `source_index` provider — no single artifact
layer can see it, because the binary contains only the one layout the
linker happened to pick.

## Why this matters for a real release

Whichever TU's definition the linker keeps, every call site compiled
against the *other* TU's assumed layout reads or writes the wrong offsets
for `geometry::Vec3` — silent corruption or misreads with no crash to flag
it. This is exactly the class of bug that is expensive to diagnose after
the fact (it reproduces only with specific link orders or optimization
levels) and cheap to catch here, before the build ships.

## Safe redesign

Make the type's definition identical in every TU: guard the divergent
member with the *same* macro everywhere and compile every TU with that
macro consistently, or move the type to a single header all TUs include
unconditionally so there is only ever one definition to replay.

## Cross-tool comparison

`odr_type_variant` is a cross-source check unique to abicheck's audit mode
— it compares two translation units' own replayed layouts for the *same*
type against each other, which isn't something `abidiff`/`abi-compliance-checker`
do (they diff two whole-binary ABI dumps against each other, not two TUs'
source-replayed definitions within one build).
