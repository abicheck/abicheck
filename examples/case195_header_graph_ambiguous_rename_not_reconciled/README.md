# case195_header_graph_ambiguous_rename_not_reconciled — Ambiguous simultaneous rename, correctly NOT reconciled

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `public_api_internal_dependency_added` (×2) · **Evidence tier:** L5

> This case ships a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is
> validated compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

The deliberate counter-example to
[case194](../case194_header_graph_rename_reconciled/README.md): when G31
Phase B's graph reconciliation (ADR-048) genuinely *cannot* tell two
declarations apart, it must refuse to guess — never resolve on ambiguous
evidence.

A public struct `demo::Config2` has **two** sibling private field-type
dependencies, `demo::detail::RawA` and `demo::detail::RawB` (both declared
in the same private header). In the next version, both are renamed
simultaneously — `RawA` → `RawX`, `RawB` → `RawY`.

Neither of B2's non-canonical-id match tiers can safely resolve this:

- **Alias match** fails because every alias (qualified name, normalized
  signature, source-relative identity) changed on both renamed nodes.
- **Structural-context match** fails too, and for a more interesting
  reason: `TYPE_HAS_FIELD_TYPE` edges carry no per-field discriminator
  beyond `role: "field"`, so *both* old nodes (and *both* new nodes) occupy
  the **identical** structural position — "the sole field-type target of
  `demo::Config2`, role `field`" describes two different old nodes
  equally well. The reconciler correctly detects this collision and
  refuses to pick a pairing (see `graph_reconcile.py`'s
  `_structural_context` docstring and `tests/test_graph_reconcile.py`'s
  `test_ambiguous_rename_does_not_reconcile`).

The alternative — guessing a pairing anyway (e.g. "first old wins first
new") — would be exactly the false-positive-by-arbitrary-choice class of
bug ADR-045 fixed for flat old/new type matching, generalized here to graph
nodes. So **no** `declaration_renamed` finding is produced for either pair.

The raw structural diff still does its job independently: since neither
`RawX` nor `RawY` shares a node id with anything in the old graph, both are
reported as genuinely new — `public_api_internal_dependency_added` fires
twice (once per newly-reached internal type), the conservative, correct
outcome when identity cannot be safely established.

## How to reproduce

```bash
python3 -c "
import json
from abicheck.buildsource.source_graph import SourceGraphSummary, diff_source_graph_findings
old = SourceGraphSummary.from_dict(json.load(open('old.json')))
new = SourceGraphSummary.from_dict(json.load(open('new.json')))
for c in diff_source_graph_findings(old, new):
    print(c.kind.value, c.symbol, c.old_value, '->', c.new_value)
"
# public_api_internal_dependency_added demo::Config2 no internal dependency -> reaches 2 internal decl(s)/type(s)
```

## Sibling cases

- [case194_header_graph_rename_reconciled](../case194_header_graph_rename_reconciled/README.md) — the matching positive case: an unambiguous single rename that *does* reconcile.
- [case160_public_api_internal_dep_added](../case160_public_api_internal_dep_added/README.md) — the `public_api_internal_dependency_added` mechanism this case's finding shares.

## How to fix

No fix needed for the finding itself — the two internal types are
legitimately internal. If a downstream reviewer wants clearer provenance
across renames, avoid renaming multiple sibling internal dependencies of
the same public entry in the same release, or keep them structurally
distinguishable (e.g. different declaring files) so evidence-based
reconciliation has something unambiguous to key on.
