### Changed

- **`abicheck.service` thinned further (ADR-061 Phase 4)**: `resolve_input`
  and its zero-`PolicyFile`-dependency helpers (`detect_binary_format`,
  `sniff_text_format`, `collect_metadata`, `load_env_matrix`, and their
  private helpers) moved verbatim to the new `abicheck.workflows.
  input_resolution`, dropping `service.py` from 886 to 439 lines.
  `abicheck.serialization` joined `architecture/modules.yaml`'s
  `public_root_surfaces` so the new module — physically inside the
  `workflows` package — can reach `load_snapshot` without the deeper
  `storage` reclassification that module's own debt entry is still blocked
  on.

### Notes

- No behavior, schema, or public-signature change: every moved name keeps
  its documented import path (`from abicheck.service import
  resolve_input`, etc.) via a plain static re-export.
- **Test-authoring note**: a test that intercepts a call `resolve_input`
  makes *internally* (`run_dump`, `load_snapshot`, `detect_binary_format`,
  `sniff_text_format`) must now patch
  `abicheck.workflows.input_resolution.<name>` rather than
  `abicheck.service.<name>` — the same rule `service_dump_native.py`'s own
  re-export block already documents for its split. `resolve_input` itself
  (the whole function) is unaffected and still patchable at
  `abicheck.service.resolve_input`.
