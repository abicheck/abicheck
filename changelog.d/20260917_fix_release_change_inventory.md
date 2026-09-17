### Fixed

- **A release report now states standing hygiene separately from what the
  comparison observed, the way a single-pair report already did.** The
  directory/package fan-out already computed each member's
  `build_summary(result)` but copied only two of its fields into the member
  entry, dropping `change_inventory` — so a byte-identical rebuild of a
  release whose members carry pre-existing `exported_not_public` exports
  announced them as observed risk in the JSON document, the one-line
  summary and the Markdown table alike. Each member entry now carries the
  identical block a scalar `compare` report carries, from the identical
  computation, and the release summary carries a members-only aggregate
  (`report/release_change_inventory.py`, release schema **1.7**) that states
  its own scope: a member with no completed comparison is counted rather
  than summed as zero findings, release-global bundle/probe-matrix findings
  stay a separate unit, the Markdown table's existing columns keep their
  inclusive meaning, and no aggregate is emitted at all when no member
  carries a block.
