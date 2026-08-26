### Changed

- **ADR-061 Phase 3**: `embed_build_source` — embedding L3-L5 build/source
  evidence into a snapshot — moved from the CLI layer to
  `abicheck/buildsource/embed.py`, together with the pack loaders
  (`buildsource/pack_load.py`) and the snapshot export set
  (`buildsource/snapshot_exports.py`). The engine no longer raises or catches
  Click exceptions: it raises `ValidationError` for a usage error and
  `SnapshotError` for an operational one, and the CLI translates those to
  `click.UsageError` (exit 64) and `click.ClickException` (exit 1)
  respectively. Exit codes, messages and the typed API's `SnapshotError`
  contract are all unchanged. `service_input_resolution.py` is now free of
  CLI imports and classified `workflows`.
