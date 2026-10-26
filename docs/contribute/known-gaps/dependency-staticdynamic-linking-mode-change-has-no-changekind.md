---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Dependency static/dynamic linking-mode change has no ChangeKind

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-25): OPEN.** docs/contribute/abi-taxonomy-coverage.md:177 still lists dependency-abi.linking-mode-change as NOT_IMPLEMENTED, and no ChangeKind containing linking_mode exists in abicheck/model/change_catalog.


Found by Phase 2/3 of
[ABI/API knowledge and corpus](../plans/abi-api-knowledge-and-corpus.md) —
the one taxonomy leaf (`dependency-abi.linking-mode-change`,
`docs/contribute/abi-api-failure-taxonomy.md`) that no `ChangeKind` claims
at any evidence tier, and therefore the only `NOT_IMPLEMENTED` row in
[the coverage matrix](../abi-taxonomy-coverage.md).

When a dependency switches between static and dynamic linking, the
dependency's own symbols change status wholesale: previously resolved at
load time through `DT_NEEDED` (or the PE import table / Mach-O load
commands), they are now duplicated into the artifact itself, with their own
visibility, ODR exposure, and interposition behaviour changing with them —
and the reverse when a vendored dependency is unbundled. Nothing in the
registry names that transition. Its consequences are *partly* observable
through unrelated kinds — `needed_removed` when the dependency leaves the
needed list, `symbol_leaked_from_dependency_changed` /
`visibility_leak` when its symbols surface in the artifact's own export
table — so a real occurrence produces two or three findings that each
describe a symptom and none of which says what happened.

Not fixed here: Phase 2/3 of that plan is a mapping and classification
pass, and the plan's own "Out of scope" section rules out any detector,
evidence, or `ChangeKind` change (a `NOT_IMPLEMENTED` finding is recorded,
not fixed, by it). Tractable when it is picked up: both sides' needed/import
lists and export sets are already collected at L0, so the evidence a
detector would join is present today — what is missing is the join, a kind,
and the "vendored vs. unbundled" direction in its `impact` text. Note the
adjacent, deliberately *unclaimed* half: whether the newly-static
dependency's code is ABI-compatible with what consumers already linked is a
question about a third artifact abicheck was not given, which is
`dependency-abi.transitive-break`'s territory, not this leaf's.
