---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - surface-growth
summarizes:
  - verdicts
depends_on:
  - abicheck/diff_surface_metrics.py
  - abicheck/surface_graph.py
  - abicheck/policy/severity.py
  - abicheck/semver.py
lifecycle: active
generated: false
---

# Report the Surface, Not Only the Breaks

## Growth is a change to the contract

Every added public symbol, type or field is compatible today and a promise
tomorrow: the next release has to keep it, and every consumer that starts
using it is a consumer you now cannot break. A report that lists only
breaks therefore answers half the question. "0 breaks" means nothing was
taken away; it does not mean nothing was added, and for a frozen API — a
plugin ABI, a stable SDK line, a library whose consumers are rebuilt on a
schedule you do not control — an unplanned addition *is* the break.

abicheck reports additions as `COMPATIBLE`, which [Verdicts](verdicts.md)
defines; this page is about making that category visible instead of
letting it drown under a green check.

## Four signals

### Per-symbol additions

Every added function, variable or type is its own finding in the
`COMPATIBLE` (addition) category, with the same identity and location as a
break: [case03](../reference/examples/case03_compat_addition.md) (a new
exported function), [case61](../reference/examples/case61_var_added.md) (a
new global), [case62](../reference/examples/case62_type_field_added_compatible.md)
(a field added to an opaque type). Read them in the report the way you read
removals — as a list to review, not a count to admire.

### Aggregate roll-ups

Three surface-metric findings describe the *shape* of the change rather
than its members: `public_surface_grew`, `public_surface_shrank`, and
`undocumented_export_ratio_increased` (the fraction of exported symbols
with no public-header declaration went up — the export table is growing
faster than the documented contract). They are informational and only
emitted on request:

```bash
abicheck compare old.json new.so -H include/ --surface-metrics --format json
```

### The release recommendation

Under the release profile the report carries `release_recommendation` — the
SONAME or SemVer action the change set implies, which turns "additions
present" into "this is a minor release, not a patch":

```bash
abicheck compare last-release.json new.so -H include/ --profile release-cut
```

The field's shape is owned by
[Output Formats § Release recommendation](../use/output-formats.md#release-recommendation).

### Growth you did not intend

The one-build audit (a `scan` with no `--against`) finds surface that grew
without anyone deciding it should: an accidental export
([case143](../reference/examples/case143_audit_accidental_export.md)) or an
export with no version node under a versioning scheme
([case145](../reference/examples/case145_audit_unversioned_export.md)). Both
are additions to your contract that no review approved.

## Report or gate?

Additions gate CI only when you say so. A `.abicheck.yml` `severity:` block
with `addition: error` (or an Action `severity-preset` that includes it)
turns them into exit 1 — there is no dedicated Action input for additions.
That is right for a frozen API, where any growth needs a decision, and
noise for a growing SDK, where it would fail every feature PR; for the
latter, report and review instead. The Action recipe is in
[GitHub Action Recipes § Detect unintentional API expansion](../use/github-action-recipes.md#detect-unintentional-api-expansion);
the severity categories and how each maps to an exit code are owned by
[Severity](../use/severity.md).

## Make it visible on the PR

With `annotate: true` and `annotate-additions: true` (the second has no
effect without the first) the Action posts each addition as a `::notice`
annotation on the diff, and the
[sticky PR comment](../use/github-action-recipes.md#sticky-pr-comment)
recipe keeps one always-current summary at the top of the conversation.
How findings map to annotation levels is owned by
[Annotations](../use/annotations.md).

## Trend it

The roll-ups and the addition count are plain JSON fields; keeping them
per release in whatever dashboard you already have shows the contract's
growth rate, which is the number a frozen-API team wants to see stay at
zero and a growing-SDK team wants to see match its roadmap. Nothing here
claims a tool for that beyond the fields themselves.

---

**Ladder:** ← [Where in the Pipeline](where-in-the-pipeline.md) · Tier 5 · Practice · [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) →
