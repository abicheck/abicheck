### Changed

- **`abicheck.suppression` is now classified `policy`** (ADR-061 Phase 4).
  The two CLI helpers that loaded a `--suppress` file (`cli_params.py`,
  `cli_scan_baseline.py`) now reach `SuppressionList`/`Suppression` through a
  new `abicheck.workflows.suppression` re-export, the same pattern
  `abicheck.workflows.storage` already established for `snapshot_io.py` —
  no behavior change, only which module a frontend imports through.
- **`BundleFacts` (de)serialization moved out of `abicheck.serialization`**
  into a new sibling, `abicheck.bundle_facts_serialization` (ADR-061 Phase
  4/5): `bundle_facts_to_dict`/`bundle_facts_from_dict`/`load_bundle_facts`/
  `save_bundle_facts` serialize a `workflows`-owned type (`BundleFacts`), so
  they now live beside it rather than inside the module ADR-061 is moving
  towards `storage`. `abicheck.serialization` keeps resolving all four names
  unchanged, via four typed wrapper functions that delegate to the new
  module lazily (avoiding a real import cycle between the two modules) — no
  call site changes and no loss of type checking for existing callers. This
  also retires
  a historical duplicate: `abicheck.storage.bundle_facts_validation`'s own
  copy of the `filesystem_aliases`/`library_filenames` validators is now
  called directly instead of being kept in sync by hand.

### Notes

- Neither change alters any JSON schema, exit code, or public function
  signature — see `architecture/debt.yaml`'s updated `serialization.py`
  entry for the precise, re-measured remainder of that module's own
  `storage` classification (down to 62 findings from 72; two distinct kinds
  remain, one of them a genuine behavioral edge rather than a routine
  reclassification).
