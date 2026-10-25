<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **BREAKING for the stored `BundleFacts` document: its JSON is the live release document** — `compare OLD.bundle-facts.json NEW -o json` no longer writes the flat `mode: "bundle_facts"` shape. The document is the live release document (release schema `2.5`): `release_schema_version` and every live key at the top level, the members as the live `libraries` list, `verdict`, `exit`, `run_outcome`. Fields a stored comparison cannot compute stay explicit `unavailable` markers with a reason, and its receipt stays marked `reconstructed_from_stored_facts`. The operand kind (`new_is_stored`), the unresolved members (`not_comparable_members`, `extraction_failures`, `resolution_not_applicable_members`) and the deployment digest are kept as top-level keys. Consumers that read the old keys (`mode`, `libraries` as a keyed object, `per_library_verdict`, `old_bundle_facts`) must read the live keys. The stored Markdown is the live release Markdown, so its old `Bundle-facts comparison` heading is gone. A stored run with a not-comparable member now reports `run_outcome.compatibility` as the verdict of the members that were compared (previously `null`); the run is still `operational: not_comparable` with exit 16, and the not-comparable member is listed with its state and reason in every format.
