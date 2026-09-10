# Case 147: Depth Ladder — the Same Input Answered at Increasing Depth

**Category:** Quality (Audit) | **Verdict:** 🟢 COMPATIBLE (bad practice)

## Verdict and consumer impact

Single-release audit: one build's evidence checked against itself, no
baseline. abicheck's verdict is `COMPATIBLE`, but the audit flags an
advisory finding: `connect()` is a public function that takes
`detail::SessionState&`, a type declared only in a private header — the same
`private_header_leak` shape as case144, but this case exists to demonstrate
*how much evidence abicheck needed to prove it*. ADR-035's honest-coverage
promise is that a scan says exactly what each depth proved and what it
could not, rather than silently upgrading a hint into a confirmed finding.
This case is the legibility anchor for that promise: the same input, read at
increasing evidence depth.

## What this snapshot contains

`snapshot.abi.json` is a single, hand-built `AbiSnapshot` for one build of
`libdemo.so`. Unlike case144/146, it carries **both** the L2 header
provenance and a baked-in L5 source graph, so it represents what a live
`--depth source` scan would have already collected:

| Source in the snapshot | What it records |
|---|---|
| Binary export table (L0) | `_Z7connectv` (`connect`) exported |
| Public-header AST (L2) | `connect(detail::SessionState &s)`; the `detail::SessionState` struct carries `origin: private_header` |
| Source graph (L5) | corroborates the reference, adding `source_index` as a second provider alongside `public_header_ast` |

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
- Evidence tiers: elf, header

## Candidate-side findings

| Finding | Symbol | Severity | State | Detail |
| --- | --- | --- | --- | --- |
| `private_header_leak` | `_Z7connectv` | potential_breaking | present in this build | Public API 'connect' exposes type 'detail::SessionState', which is declared only in a private (non-installed) header. Consumers including the public header pull in an unshipped declaration. Make the header self-contained or install the leaked header. |
```

`private_header_leak`'s provider list on this snapshot is
`["public_header_ast", "source_index"]` (checked with the same
`crosscheck_surface()` helper `tests/test_g20_catalog.py` asserts against) —
the `source_index` entry is what marks this as the L5-corroborated case,
distinct from case144's `["public_header_ast"]`-only leak.

## Minimum evidence

`min_evidence: L2` — a public-header AST reference to a private-header type
is already enough to raise the finding; the L5 source graph baked into this
fixture *corroborates* it with a resolved call/reference edge, it isn't
required to produce the finding in the first place. That's the point of the
ladder: L2 alone already proves the leak here (unlike a case where only a
lexical pattern hints at it without AST confirmation).

## Why abicheck catches it — and what depth actually changes here

Because this is a **committed snapshot fixture**, not a live binary/source
tree, the `--depth` flag controls how much *new* evidence abicheck would
collect from `--sources`/`--build-info` — evidence this fixture doesn't need
collected because it's already baked in. Verified directly against this
file:

```bash
abicheck compare --no-baseline snapshot.abi.json --depth headers  # exit 0, same finding
abicheck compare --no-baseline snapshot.abi.json --depth binary   # exit 0, same finding
abicheck compare --no-baseline snapshot.abi.json                  # exit 0, same finding
abicheck compare --no-baseline snapshot.abi.json --depth source   # exit 0, same finding
```

All four report the identical `private_header_leak` row — because the L2
header AST alone already carries the `detail::SessionState` → private-header
fact, pinning a shallower `--depth` doesn't hide it here. `--depth source`
passes too, and that is deliberate rather than an unenforced pin: the
evidence-contract floor (`policy/depth_evidence_contract.py`) applies to
*live extraction only*, and this fixture is an already-serialized snapshot
this run never extracted, so there is no "reached a shallower depth than
requested" failure to report for it. Against a **live** binary the same pin
with no `--sources`/`--build-info` exits `7`. (Legacy `scan` errored on this
fixture instead, because it drew no live/stored distinction.)
Against a **live** binary + real source tree (not a committed fixture), the
ladder plays out as designed: `--depth headers` gives only the AST-level
hint, and `--depth source` is what adds the resolved source-graph
corroboration that upgrades it to a confirmed cross-check.

## Why this matters for a real release

A scan that silently reports "no leak found" because it only had shallow
evidence would be worse than one that says "not checked at this depth" —
the honest-coverage contract is what lets a CI policy decide how much
evidence to require before trusting a `COMPATIBLE` result. Here, L2 already
proves the leak; a project could still choose to require L5 corroboration
before gating on it, and the coverage report is what makes that choice
possible instead of guesswork.

## Safe redesign

Same fix as a private-header leak generally: give `detail::SessionState` an
opaque handle so the public signature no longer names a private-header type,
or install the header that defines it.

## Cross-tool comparison

`private_header_leak` is a cross-source check unique to abicheck's audit
mode — it reconciles a public declaration against the provenance of the
type it references, optionally corroborated by a source graph, within the
*same* build. This depth-ladder framing (evidence tiers, `--depth` dial,
honest per-layer coverage reporting) has no equivalent in
`abidiff`/`abi-compliance-checker`, which only diff two ABI dumps against
each other at a single, fixed evidence level.
