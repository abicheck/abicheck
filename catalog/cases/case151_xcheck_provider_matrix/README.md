# Case 151: Provider-Agreement Matrix (Corroboration Grows With Evidence)

**Category:** Quality (Audit) | **Verdict:** 🟢 COMPATIBLE (bad practice)

## Verdict and consumer impact

Single-release audit: one build's evidence checked against itself, no
baseline. An audit reports no compatibility verdict at all (ADR-068 D2) —
there is no baseline to compare against — but the audit's
`private_header_leak` finding (public
function `make_widget()` returns a private-header type, same shape as
case144) is the fixed point of this case; what varies is **how much
evidence corroborates it**. The cross-check machinery records
which providers (evidence sources) contributed to each finding, and this
case demonstrates that the list grows — without the finding itself
changing — as more evidence becomes available.

## What this snapshot contains

Two fixtures for the same underlying leak, differing only in how much
evidence is attached:

| Fixture | Evidence present | Providers recorded for `private_header_leak` |
|---------|-------------------|-----------------------------------------------|
| `thin.abi.json` | public-header AST (L2) only | `public_header_ast` (1 provider) |
| `snapshot.abi.json` | header AST (L2) **+** L5 source graph | `public_header_ast`, `source_index` (2 providers) |

## abicheck command

```bash
abicheck compare --no-baseline thin.abi.json      # 1 provider
abicheck compare --no-baseline snapshot.abi.json  # + source_index corroboration
```


## Expected abicheck finding

Both fixtures report the identical finding (this is `snapshot.abi.json`;
`thin.abi.json` differs only in the header line's evidence tiers):

```text
# ABI audit: libdemo.so (no baseline)

OLD side: **declared absent** (`--no-baseline`) -- this is an audit of the candidate build alone, not a compatibility comparison. No additions, removals, or compatibility verdict are reported.

- Candidate version: `1.0`
- Acquisition state (OLD): `declared_absent`
- Evidence tiers: elf, header

## Candidate-side findings

| Finding | Symbol | Severity | State | Detail |
| --- | --- | --- | --- | --- |
| `private_header_leak` | `_Z11make_widgetv` | potential_breaking | present in this build | Public API 'make_widget' exposes type 'detail::WidgetImpl', which is declared only in a private (non-installed) header. Consumers including the public header pull in an unshipped declaration. Make the header self-contained or install the leaked header. |
```

The provider list is where the two fixtures diverge. It is **not** carried
by the audit report in any format today — the one-sided report states each
finding and its ADR-068 D3 evolution state, not the per-check coverage rows
(status/detail/providers) legacy `scan`'s own `crosscheck` block carried;
that difference is recorded in
[`docs/contribute/known-gaps.md`](../../../docs/contribute/known-gaps.md).
Read it directly off `run_crosschecks()`, the same call the audit's own
cross-source pass drives:

```bash
python3 - <<'EOF'
from abicheck.serialization import load_snapshot
from abicheck.buildsource.cross_source_checks import run_crosschecks

for path in ("thin.abi.json", "snapshot.abi.json"):
    res = run_crosschecks(load_snapshot(path))
    print(path, "private_header_leak providers:", res.providers["private_header_leak"])
EOF
```

```text
thin.abi.json     private_header_leak providers: ['public_header_ast']
snapshot.abi.json private_header_leak providers: ['public_header_ast', 'source_index']
```

## Minimum evidence

`min_evidence: L2` — the public-header AST alone (the `thin.abi.json`
floor) is already enough to flag the leak with one provider. The L5 source
graph, when present, adds a second, independent corroborating provider —
it strengthens confidence in the same finding but is not required to reach
the floor.

## Why abicheck catches it

`private_header_leak` is a cross-source check: abicheck resolves every
type referenced in a public signature and checks that type's own
provenance. The `public_header_ast` provider alone is enough to know
`make_widget()` returns a type recorded as `origin: private_header`. When
an L5 source graph is also attached, `source_index` independently confirms
the same declaration-to-private-type relationship by walking the graph's
edges — a second source reaching the same conclusion, recorded as a second
provider rather than a stronger verdict.

> **Scope.** This case asserts the provider *list* differs. Deriving a
> per-finding confidence *tag* from the provider count (so 1-provider
> corroboration renders differently from 2) is a separate reporting
> enhancement, not part of this corpus.

## Why this matters for a real release

A finding backed by one provider and a finding backed by two independent
providers are not equally trustworthy, even though both fire the same
`ChangeKind`. A CI pipeline that only ran a header scan (thin evidence) and
one that also replayed the source tree (rich evidence) should both catch
this leak — and do — but only the richer pipeline can tell a reviewer "two
independent sources agree," which matters when deciding whether a finding
is worth blocking a release over.

## Safe redesign

Same as any private-header leak (see case144): opaque-handle the internal
type, or install its header so it's a real, documented part of the public
API.

## Cross-tool comparison

`private_header_leak` is a cross-source check unique to abicheck's audit
mode — it reconciles a public function's signature against the provenance
of the type it references within the *same* build, which isn't something
`abidiff`/`abi-compliance-checker` do (they diff two ABI dumps against each
other, not a binary's public surface against its own header provenance).
Provider-agreement tracking (this case's subject) has no equivalent in
either tool.
