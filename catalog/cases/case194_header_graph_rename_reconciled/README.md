# Case 194: Internal Dependency Target Renamed, Safely Reconciled

**Category:** Risk (Source Graph / Reconciliation) | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

A public struct `demo::Config` has a private field-type dependency on
`demo::detail::RawConfig`, declared in a private header. In v2, `RawConfig`
is renamed to `RawConfigV2` — same declaring file, same field, only the
spelling changes. Nothing here is a hard break: an internal type rename
behind a public struct's field is not itself an ABI/API problem. What
matters for a reviewer is *legibility* — without reconciliation, the raw
graph diff would show an unrelated node removal plus node addition, leaving
a reader to infer by hand that they're "probably" the same declaration
renamed. abicheck's graph reconciliation collapses that into one explicit
`declaration_renamed` finding instead.

## Old/new diff

| v1 (conceptual) | v2 (conceptual) |
|------|------|
| `struct Config { detail::RawConfig raw; };` | `struct Config { detail::RawConfigV2 raw; };` |

This case ships a hand-built pair of evidence-model fixtures (`old.json` /
`new.json`, `SourceGraphSummary` objects) instead of compiled `v1`/`v2`
sources. See
[`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## abicheck command

There is no compiled binary or header pair to point `abicheck compare` at —
the fixture is a raw `SourceGraphSummary`, the same evidence object
`dump --sources`/`--build-info` would embed inside a real snapshot. The
reproducible command runs abicheck's source-graph diff function directly:

```bash
python3 -c "
import json
from abicheck.buildsource.source_graph import SourceGraphSummary, diff_source_graph_findings
old = SourceGraphSummary.from_dict(json.load(open('old.json')))
new = SourceGraphSummary.from_dict(json.load(open('new.json')))
for c in diff_source_graph_findings(old, new):
    print(c.kind.value, c.symbol, c.old_value, '->', c.new_value)
"
```

## Expected abicheck finding

```text
public_api_internal_dependency_added demo::Config no internal dependency -> reaches 1 internal decl(s)/type(s)
declaration_renamed demo::detail::RawConfigV2 demo::detail::RawConfig -> demo::detail::RawConfigV2
```

Verdict: COMPATIBLE_WITH_RISK — the risk finding fires independently of the
rename explanation (reconciliation only explains/localizes; it never
suppresses an existing finding, ADR-028 D3).

## Minimum evidence

`min_evidence: L5` — reconciling the two nodes as "the same declaration,
renamed" instead of an unrelated remove+add pair requires the derived source
graph's structural-context matching (G31 Phase B, ADR-048): both nodes
occupy the identical, unique `TYPE_HAS_FIELD_TYPE:field` position from
`demo::Config`. No lower evidence tier carries graph node identity at all.

## Why abicheck catches it

`abicheck.buildsource.graph_reconcile` runs canonical-identity and
graph-reconciliation matching between the old and new source graphs. Here,
the qualified name changed on both sides so neither the alias tier nor a
shared canonical id resolves the match, but the two nodes are each the sole
`TYPE_HAS_FIELD_TYPE:field` target of `demo::Config` in their respective
graphs — a unique structural position — so the weakest ("structural-context")
tier safely pairs them and reports one `declaration_renamed` finding instead
of two disconnected facts.

## Runtime failure demonstration

There's no `app.c` here and no crash to demonstrate — this is a legibility
improvement on a non-breaking risk finding, not a hard break. The real-world
scenario is a CI job that runs abicheck with `--sources`/`--build-info`
evidence across two releases and posts the findings to a PR: without
reconciliation, a reviewer sees an unexplained "internal type X removed,
internal type Y added" pair and has to manually confirm they're the same
renamed declaration; with it, the PR comment says outright "RawConfig was
renamed to RawConfigV2," cutting review time on every routine internal
rename.

## Safe redesign

No fix needed — renaming an internal implementation detail is not itself a
break. Promote `RawConfig`/`RawConfigV2` to a documented part of the API if
consumers need a stable name for it, or keep `demo::Config`'s field types
independent of internals whose evolution consumers cannot track.

**Real-world example:** internal type renames during header reorganization
are routine in large C++ codebases; without a reconciliation step a
generated changelog or ABI report would otherwise list them as spurious
unrelated add/remove pairs.

## Cross-tool comparison

`abidiff`/`abi-compliance-checker` operate on compiled binaries and debug
info; neither has an equivalent to abicheck's source-graph canonical-identity
and reconciliation machinery, so neither could report this as a rename at
all — at most they'd see nothing (the type is fully internal and outside
either tool's ABI-surface scope). This finding and its reconciliation are
unique to abicheck's L5 build-source evidence layer (ADR-048). Contrast with
[case195](../case195_header_graph_ambiguous_rename_not_reconciled/README.md),
the deliberate counter-example where two simultaneous renames correctly stay
unreconciled.
