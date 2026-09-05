# Case 196: Declaration Reconciled as Moved Across a Compound Edit

**Category:** Risk (Source Graph / Reconciliation) | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

A private, never-exported internal helper `demo::detail::helper` keeps its
exact qualified name, but two things change about it in the same release:
its parameter type changes from `int` to `long` (which moves its Itanium
mangled name, `_ZN4demo6detail6helperEi` → `_ZN4demo6detail6helperEl`), and
its declaring header is reorganized (`include/demo/detail_v1.h` →
`include/demo/detail_v2.h`). Because the mangled name — and therefore the L5
graph node's own id — changed, the raw graph diff would show an unrelated
node removal plus node addition that happen to share the name
`demo::detail::helper`. abicheck's graph reconciliation (ADR-048) recognizes
the two as the same declaration via the qualified-name alias tier, and —
since the declaring file also changed while the qualified name did not —
classifies the outcome as `declaration_moved`.

This is deliberately **not** a "pure" move: an unchanged function signature
can never change its own mangled name, so a pure declaring-file move cannot
by itself perturb a node's identity in the current graph model (see
`abicheck/buildsource/graph_reconcile.py`'s own "Known gap" note — this is
the compound, *reachable* shape a real move-plus-signature-change release
takes, not the unreachable pure-move shape an earlier attempt at this case
mistakenly modeled).

A public **inline** function, `demo::process`, genuinely starts calling the
helper — only on the **new** side of this fixture. It's inline (rather than
an ordinary out-of-line exported function) for a separate reason a further
review round caught: `demo::process`'s own body must actually be compiled
into consumer code for its internal call to genuinely count as
public-facing — an ordinary out-of-line public function's internal calls
are compiled into the *library's* binary only, never into any consumer's
(see `source_graph.is_consumer_compiled_public_entry`'s docstring and its
real callers, e.g. `post_processing_reachability.MarkReachability`). An
inline caller's body is emitted into every consumer TU that includes the
header, so its call is consumer-visible under any reasonable reachability
notion — sidestepping that question rather than resting this case's ground
truth on which reachability predicate a given detector happens to use.

`demo::detail::helper` is deliberately **inline too** — a further review
round caught a real linkability gap in an earlier version of this fixture:
since `demo::process`'s body (including its call to `detail::helper`) is
emitted into *every consumer's own* translation unit, an ordinary
out-of-line, non-exported `detail::helper` would leave that
consumer-emitted call as an unresolved external symbol reference — a real
link failure for any actual consumer, not a purely internal, risk-only
change. Making `detail::helper` inline too (an entirely ordinary
header-only-library pattern: a private "detail" header providing an inline
implementation, transitively included by the public header) means its own
body is *also* emitted into that same consumer TU, so the call resolves
locally with no external symbol needed at all — a real consumer program
built against this exact scenario links and runs cleanly, which is what
makes `COMPATIBLE_WITH_RISK` genuinely correct rather than a modeling
artifact.

Both fixture surfaces also carry a `coverage.fact_set`/`fact_family_states`
rollup naming the one `source_edges` producer whose coverage genuinely
matches a full, unfiltered call/type-graph replay
(`source_graph._FULL_WALK_SOURCE_EDGES_PRODUCER`), and the fixture calls
`source_graph.mark_source_edges_extractor_coverage()` — the *real*
production certification helper — rather than hand-forcing
`extractor_passes["call_graph"] = True` directly. A review round caught
that the hand-forced version bypassed the real certification gate
entirely: run against this exact surface data, the real helper would
instead *degrade* the pass (an unconfirmed rollup, not a full-walk
producer), so a regression in production coverage propagation could never
have failed this fixture. With the real helper genuinely certifying both
sides from that coverage rollup, the old side's zero calls is a
**confirmed** zero, and the new side's one call is a **genuinely new**
dependency — not an artifact of the same target under two different raw
ids (the shape an earlier review round correctly rejected for the
*reverse* case, where the identical call edge existed on both sides
pointing at the helper's two different per-version mangled-name ids).
`source_graph_findings._internal_dependency_findings` (the
`public_api_internal_dependency_added` producer, demonstrated in
[case160](../case160_public_api_internal_dep_added/README.md)) therefore
correctly fires here, alongside `graph_reconcile`'s own
`declaration_moved` on the helper — the same `demo::process` →
`demo::detail::helper` edge does both jobs at once: it satisfies
`graph_reconcile._public_reachable_ids`'s gate (a purely-internal,
never-publicly-reached rename/move would otherwise be suppressed rather
than reported as RISK), and it is itself the genuinely-new public-to-internal
dependency the second finding names.

The helper is deliberately **private** — a review round on an earlier
version of this case caught that a *public* function's mangled-name-moving
signature change is itself a real, independent BREAKING change (the old
exported symbol disappears), which contradicts `ground_truth.json`'s
invariant that one canonical verdict applies to the scenario a case
describes: cataloging a scenario that genuinely breaks the ABI as
`COMPATIBLE_WITH_RISK` would be wrong. With the identity-perturbing edit
confined to a `private_header`-visibility declaration never present in the
exported symbol table, a real end-to-end `compare()` of this exact scenario
has nothing BREAKING to contradict — `COMPATIBLE_WITH_RISK` is the genuinely
correct canonical answer, carried entirely by the two RISK-tier L5 findings
this fixture reproduces.

## Old/new diff

| v1 (conceptual) | v2 (conceptual) |
|------|------|
| `// include/demo/api.h`<br>`#include "demo/detail_v1.h"`<br>`inline void process() { /* does not call helper yet */ }`<br><br>`// include/demo/detail_v1.h`<br>`namespace detail { inline void helper(int) { /* ... */ } }` | `// include/demo/api.h`<br>`#include "demo/detail_v2.h"`<br>`inline void process() { detail::helper(1L); }`<br><br>`// include/demo/detail_v2.h`<br>`namespace detail { inline void helper(long) { /* ... */ } }` |

This case ships a hand-built pair of evidence-model fixtures (`old.json` /
`new.json`, `SourceGraphSummary` objects) instead of compiled `v1`/`v2`
sources. Unlike case194/195, which construct `GraphNode`/`GraphEdge` objects
directly, these fixtures are produced by running real `SourceEntity`/
`BuildEvidence` facts through the actual production fold
(`source_graph.build_source_graph`) — the same function `dump --sources`/
`--build-info` calls — so the two node ids are genuinely distinct for the
reason a real extractor would make them distinct, not because the generator
invented an artificial id. See
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
public_api_internal_dependency_added demo::process no internal dependency -> reaches 1 internal decl(s)/type(s)
declaration_moved demo::detail::helper demo::detail::helper -> demo::detail::helper
```

Verdict: COMPATIBLE_WITH_RISK — both are pure L5-evidence risk annotations.
Because the identity-perturbing edit lands on a `private_header`-visibility
declaration, a real binary comparison of this exact scenario has no
BREAKING/API_BREAK finding to sit alongside them; reconciliation only
explains/localizes, it never suppresses or replaces another finding
(ADR-028 D3).

## Minimum evidence

`min_evidence: L5` — recognizing the two nodes as "the same declaration,
relocated" instead of an unrelated remove+add pair requires the derived
source graph's canonical-identity/alias matching (G31 Phase B, ADR-048):
both nodes share the qualified name `demo::detail::helper`. No lower
evidence tier carries graph node identity at all.

## Why abicheck catches it

`abicheck.buildsource.graph_reconcile` runs canonical-identity and
graph-reconciliation matching between the old and new source graphs. Here,
the qualified name is unchanged on both sides, so the *alias* tier resolves
the match directly (the same strength of evidence as case196's sibling
case194 needed the weaker structural-context tier for, since case194's
qualified name changed too). `graph_reconcile._classify_outcome` then
compares each side's declaring file, recovered from the real
`SOURCE_DECLARES` edge `build_source_graph` creates from each function's
`SourceLocation`: the name held still but the file differs, so the outcome
is classified `declaration_moved` rather than `declaration_renamed`. The
`demo::process` → `demo::detail::helper` `DECL_CALLS_DECL` edge exists only
in the new graph, and both graphs mark their call-graph pass as confirmed
(so the old side's absence is a proven zero, not missing evidence): this
satisfies `graph_reconcile._public_reachable_ids`'s gate (a
purely-internal, never-publicly-reached rename/move would otherwise be
suppressed rather than reported as RISK) *and* is itself the genuinely-new
dependency `source_graph_findings._internal_dependency_findings` (the
`public_api_internal_dependency_added` producer, demonstrated in
[case160](../case160_public_api_internal_dep_added/README.md)) correctly
credits as newly reached.

## Runtime failure demonstration

There's no `app.c` here and no crash to demonstrate — `demo::detail::helper`
is never exported as a symbol, and (being inline itself) resolves entirely
within each consumer's own translation unit, so no consumer binary ever
references it as an external symbol and no real binary-level break exists
to demonstrate. The real-world scenario is a
CI job that runs abicheck with `--sources`/`--build-info` evidence across
two releases and posts the findings to a PR: without reconciliation, a
reviewer sees an unexplained "`demo::detail::helper` removed,
`demo::detail::helper` added" pair (same name, different mangled symbol,
easy to misread as two unrelated internal declarations) and has to manually
confirm whether it matters; with it, the PR comment notes the pair is the
same internal declaration, relocated and resignatured, alongside a
separate note that `demo::process` picked up a new internal dependency in
the same release — legibility for an entirely internal refactor, not a
break to chase down.

## Safe redesign

No fix needed — `demo::detail::helper` is a private implementation detail;
neither its header move nor its signature change is visible to any
consumer of `libdemo.so`. If this helper needs to become part of the public
API, promote it deliberately (move its declaration to a public header,
export it) rather than relying on it staying accidentally reachable only
through `demo::process`'s own translation unit.

**Real-world example:** touching both a private helper's signature and its
declaring header in the same refactor is routine when consolidating
internal implementation files; without a reconciliation step, a generated
changelog or ABI report would otherwise list the pair as two unrelated,
unexplained internal symbol changes.

## Cross-tool comparison

`abidiff`/`abi-compliance-checker` operate on compiled binaries and debug
info; neither has an equivalent to abicheck's source-graph canonical-identity
and reconciliation machinery, and neither would see anything here at all —
`demo::detail::helper` is entirely internal and outside either tool's
ABI-surface scope regardless. This finding and its reconciliation are unique
to abicheck's L5 build-source evidence layer (ADR-048). Contrast with
[case194](../case194_header_graph_rename_reconciled/README.md) (a pure
rename, no signature change, resolved via the weaker structural-context
tier) and
[case195](../case195_header_graph_ambiguous_rename_not_reconciled/README.md)
(the deliberate counter-example where reconciliation correctly refuses to
guess).
