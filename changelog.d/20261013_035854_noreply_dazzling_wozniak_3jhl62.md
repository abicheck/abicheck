<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **`Function.is_inline` is now a `Fact[bool]` (snapshot schema v60).** The last case-(a) field guarded by a `*_facts_reliable` flag gets a persisted `is_inline_fact` sibling, so a declaration whose inline linkage no producer captured reads as not collected instead of `False`. Snapshots written before v60 decode through the existing `inline` family threshold, so their trust is unchanged.
