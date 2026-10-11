---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `bundle_facts_store.read_bundle_facts_package` holds a bundle's raw JSON and its decoded `AbiSnapshot`s concurrently at peak

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/import_bundle_facts.py:597 export_bundle_facts still returns one whole dict[str, Any] document (no streaming form). It is bounded by DEFAULT_MAX_BUNDLE_DECODED_BYTES, as the entry notes.


A Codex review round on PR #1054 (the Track B/C duplicate-multi-artifact-
writer reconciliation) found that delegating `read_bundle_facts_package` to
`storage.import_bundle_facts.export_bundle_facts` regressed peak memory
behavior relative to the reader it replaced. The removed reader converted
each artifact's document to an `AbiSnapshot` immediately inside its
per-artifact loop (`per_library_snapshots[library_name] =
snapshot_from_dict(document)`), discarding the raw JSON document each
iteration; `export_bundle_facts` instead accumulates every artifact's raw
JSON document into one `per_library_snapshots: dict[str, Any]` and returns
it as a whole document, and `bundle_facts_from_dict()` then decodes every
entry in one pass — so at peak this can hold both the full set of raw JSON
document trees and (once decoding starts) their `AbiSnapshot` counterparts
concurrently.

Not fixed: closing this fully means changing `export_bundle_facts`'s own
return contract from "one complete `bundle_facts_from_dict()`-shaped
document" (its module docstring's stated contract, shared with
`storage/import_baseline_set.py`'s sibling adapter) to a streaming/
generator form the caller converts and discards from as it goes — a change
to the shared `storage.import_bundle_facts` module's public shape, not a
caller-local fix, and outside what that PR (reconciling the Track B/C
duplicate writers onto that one shape) was scoped to redesign. It is
bounded in the meantime, not unbounded: `read_bundle_facts_package`'s own
`DEFAULT_MAX_BUNDLE_DECODED_BYTES` check runs *before* `bundle_facts_from_dict`
is ever called (raising during the incremental per-artifact charge if the
raw documents alone already exceed the budget), so peak memory here is
capped at roughly the charged-byte budget's raw-JSON cost plus its decoded-
object cost — a bounded constant, not a growth path uncorrelated with any
limit this module already enforces. If this becomes a real reported
problem rather than a review-found edge case, the honest fix is reworking
`export_bundle_facts`/`import_baseline_set`'s shared adapter shape to
stream per-artifact results to the caller instead of returning one
document, which both known callers would need to be updated for together.
