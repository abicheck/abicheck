<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Internal: `BundleFacts` now has one owner per responsibility (ADR-061
  gap E).** The value type moved to `abicheck.model.bundle_facts`; its
  JSON/G40-archive persistence moved to `abicheck.storage.bundle_facts_codec`/
  `abicheck.storage.bundle_facts_archive`/`abicheck.storage.bundle_facts_package`;
  capture, live-snapshot reconstruction, and comparison orchestration moved
  to `abicheck.workflows.bundle_facts_capture`/
  `abicheck.workflows.bundle_facts_compare`. `abicheck.bundle_facts`,
  `abicheck.bundle_facts_serialization`, and `abicheck.bundle_facts_store`
  remain as compatibility facades re-exporting the same public names — no
  behavior or schema change, and no public import path is affected.
