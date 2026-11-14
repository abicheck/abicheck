### Fixed

- **`func_became_inline` reads the export table on every platform and says
  when it had none** — the detector looked only at ELF `.dynsym`, so on a
  PE or Mach-O library (and on a headers-only comparison) it always said the
  symbol "may be removed". It now reads the new side's export table through
  `compare/export_names.default_export_names`: "still exported", "no longer
  exported" (the break itself stays `func_removed`/`func_visibility_changed`),
  or, with no export table read, that the export was not assessed. The
  verdict stays `COMPATIBLE_WITH_RISK` in every case (ADR-081 amendment).

### Changed

- Internal ownership moves (ADR-061), no behavior change: the binary
  export-name helper moved from the private
  `buildsource.cross_source_checks_base._exported_symbol_names` to the
  public `compare/export_names.default_export_names`; the asymmetric-header
  coverage warning and evidence-gap marking moved from
  `policy/common_evidence_depth.py` to
  `workflows/asymmetric_header_evidence.py`; `AbiSnapshot.invalidate_indexes()`
  replaces direct writes to its private index caches.
