<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`explain_use_case_impact()` dropped entrypoints from a repeated
  `use_case` manifest entry** (G29 Phase 4, Codex review): a manifest may
  legitimately repeat the same `use_case` name across separate list
  entries — `parse_use_case_manifest()` never rejects it, and
  `build_use_case_graph()` already merges every entry sharing a name onto
  one `use_case` graph node with a `USE_CASE_USES_ENTRY` edge per entry.
  Attribution disagreed with the graph it claims to mirror: a plain
  `use_case_entries[name] = ids` assignment let a later entry's
  entrypoint set silently replace an earlier one's, so a change reachable
  only through an earlier entry's entrypoints was never attributed. Fixed
  by merging (`setdefault(...).update(...)`) instead of overwriting.
