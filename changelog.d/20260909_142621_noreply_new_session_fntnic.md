<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Directory/package `compare`'s release summary now stamps
  `reclassified_by` on its per-library findings** — the release fan-out's
  own capped `findings` projection (`_release_finding_dicts`) never computed
  this field at all, so a `--policy` `reclassify:` rule that demoted a
  finding's verdict left every emitted release finding silently missing the
  audit trail that single-pair `compare`'s `changes[]` and `scan --against`
  already carried (schema 2.31), even though the release's own aggregate
  disposition-audit ledger tallied the reclassification correctly. Fixed by
  routing all three finding-dict builders through one shared helper
  (`reporter.release_finding_entry`). The release's Markdown report gains
  the same disclosure (`reporter.release_finding_detail_lines`).

