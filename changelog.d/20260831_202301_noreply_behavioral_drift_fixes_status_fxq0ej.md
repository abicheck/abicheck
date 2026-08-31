<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A baseline persisted before `strip_anonymous_type_location` existed (or
  by a header-mode dumper build that never called it) had its closure/
  anonymous-type markers left completely unrenumbered on load.**
  `strip_anonymous_type_location` was only ever applied at header-extraction
  time; `serialization.snapshot_from_dict` called
  `qualified_name_segments.renumber_anonymous_closure_identities` directly,
  whose marker regex requires the already-stripped
  `(lambda:<basename>:<line>:<col>)` spelling and never matched the raw
  `(lambda at <path>:<line>:<col>)` form. Comparing such a baseline against
  a freshly dumped snapshot of the identical, unedited declaration
  manufactured a spurious `type_removed`/`func_removed` plus
  `type_added`/`func_added` pair purely from the encoding difference.
  `snapshot_from_dict` now calls the new
  `qualified_name_segments.rewrite_anonymous_type_spellings` with
  `name_classification.strip_anonymous_type_location` immediately before
  renumbering, closing the "raw pre-strip baseline vs. fresh dump" gap in
  the `scan --against`/`publish-baseline` workflow this tool exists to
  serve.
- **A namespace-move batch (`SYMBOL_RENAMED_BATCH`) could silently drop a
  well-supported member, or fall entirely below its 2+-pair reporting
  threshold, whenever an unrelated, isolated removed symbol happened to
  coincidentally collide with one of the batch's own added targets.**
  `diff_symbols_renames.find_namespace_move_groups` already resolved this
  class of cross-position ambiguity via global-support corroboration when
  ONE removed symbol had multiple candidate added targets
  (`removed_id_to_added_symbols`), but rejected the exact mirror
  unconditionally — ONE added symbol claimed by multiple distinct removed
  identities (`added_id_to_removed_symbols`) — with no attempt to
  distinguish a genuinely corroborated rival from an isolated coincidence.
  The added-side gate now runs the same corroboration test (scoped per
  competing removed identity, since each competing claim belongs to a
  different symbol rather than the same one's alternate positions): an
  unresolved competitor still always vetoes, but a competitor that resolved
  to its own key with no support beyond itself is dismissed instead of
  blocking a real, well-supported substitution.
