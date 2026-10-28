<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`load_snapshot` -> `save_snapshot` keeps a dump's `dump_provenance` block** — the depth and frontend stamp that `abicheck dump` writes into a snapshot document was silently dropped on every public-API round trip, because the typed `AbiSnapshot` had no slot for it. `AbiSnapshot` now carries it as `dump_provenance` (defaults to `None`, excluded from equality), so a re-save keeps the original block verbatim. A snapshot no `dump` produced encodes exactly as before, with no new key. A document whose `dump_provenance` is present but not a JSON object is now refused with `SnapshotError` instead of being dropped.
