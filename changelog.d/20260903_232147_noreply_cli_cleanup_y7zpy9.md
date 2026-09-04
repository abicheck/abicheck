<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Removed

- **`compare --old-bundle-facts` is gone; OLD_INPUT/NEW_INPUT are now
  classified automatically instead** (CLI cleanup phase two, PR I). A
  stored `BundleFacts` document (from a prior `--bundle-facts-out`) is now
  detected by its own self-describing `artifact_type` marker
  (`workflows/bundle_compare_operand.py`), the same way `compare` already
  classifies a directory vs. a package vs. a single binary — so
  `abicheck compare old.bundlefacts.json new-release/` works without any
  flag. `--bundle-facts-library-manifest`/`--max-json-object-nodes` are
  unaffected in spelling, only in when they apply (whenever OLD_INPUT
  classifies as stored bundle facts, rather than gated on the removed
  flag). Comparing against a *stored* NEW_INPUT (live/stored or
  stored/stored) is explicitly rejected with a clear error rather than
  silently mishandled — no execution engine exists for either yet. No
  deprecation alias, per this plan's standing "hard cleanup" stance; a
  genuinely marker-less legacy v1 `BundleFacts` document (predating
  `BUNDLE_FACTS_SCHEMA_VERSION` 2) can no longer be auto-detected — pass it
  through the Python API (`bundle_side_input.compare_release_against_bundle_facts`)
  instead, or re-persist it with a current-schema `--bundle-facts-out`.

