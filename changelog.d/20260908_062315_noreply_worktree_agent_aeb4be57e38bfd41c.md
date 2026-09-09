<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`--surface-metrics` is now unconditional on every path, not just
  scalar `compare`** — the directory/package release fan-out and both
  stored-`BundleFacts` drivers (`compare_release_against_bundle_facts`,
  `compare_stored_bundle_facts_pair`) were still silently omitting
  `public_surface_grew`/`public_surface_shrank` findings that an identical
  library pair produces under a scalar `compare`, since none of the three
  forwarded `surface_metrics=True` to the shared `compare_snapshots()`
  chokepoint (violating "one model, any cardinality"). `--surface-metrics`
  is also no longer rejected as an unsupported flag on a stored-bundle-facts
  `compare` — it is a harmless no-op there too now.
- **`--audit-suppressions` with no `--suppress` is now a no-op on
  directory/package and stored-bundle-facts `compare` too** — matching the
  scalar path's own "nothing to audit without a suppression file" no-op.
  Only `--audit-suppressions` *together with* a real `--suppress` is still
  rejected on these operand shapes (a genuine capability gap: no single
  audit result to attach across a per-library fan-out).
- **`--view show=...` given multiple times now genuinely ORs the groups
  together** — two occurrences naming different dimensions (e.g.
  `--view show=breaking --view show=functions`) used to be joined with a
  comma into one `ShowOnlyFilter` group, which `ShowOnlyFilter`'s own
  AND-across-dimensions semantics silently turned into "breaking AND
  functions" instead of the documented "breaking OR functions". Each
  `--view show=...` occurrence is now its own group, and a finding is shown
  if it matches *any* group (`reporter_markdown.parse_show_only_groups`/
  `show_only_matches`/`show_only_matches_severity_label`).
