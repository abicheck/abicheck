<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **A stored `BundleFacts` comparison carries the live release document, with explicit unavailable markers** — `compare OLD.bundle-facts.json NEW -o json` now adds a `release` block (release schema `2.3`): the same document a directory comparison writes, built from the stored comparison. Fields a stored comparison cannot compute are explicit `{"state": "unavailable", "reason": ...}` markers (per-member `type_attribution`, `unreached_type_disposition`, `bundle_evidence`; the release `public_surface`), never empty values or absent keys. Its receipt is marked `effective_config_source: "reconstructed_from_stored_facts"`. The stored document's existing keys are unchanged. `-o html` now renders a stored comparison too, through the same release renderer as a directory comparison. The release schema moves from `2.2` to `2.3`; the live document's content does not change.
