### Fixed

- **A present-but-unparseable CFI section (`.eh_frame`/`.debug_frame`) is
  no longer reported as complete evidence** — `_get_cfi_source()` caught
  the same broad `ELFError`/`AssertionError` for two different situations:
  a section that never existed (legitimately nothing to be incomplete
  about) and a section that genuinely exists but whose entries raised on
  decode (a malformed/truncated section, a real pyelftools
  `ELFParseError`). Both previously returned a plain `None`, which the
  caller unconditionally treated as "no CFI section at all" and reported
  `complete=True`. A new opt-in `source_failed` out-parameter now
  distinguishes the two, so a genuine decode failure correctly downgrades
  the advanced DWARF channel's completeness.
- **`DwarfMetadata`'s debug-evidence provenance fields (`evidence_source`,
  `evidence_state`, `cu_total`, `cu_failed`) are now keyword-only** —
  mirrors the identical treatment `AdvancedDwarfMetadata`'s own provenance
  fields already received, so an external caller still constructing this
  dataclass positionally cannot silently bind a value to the wrong field.
- **`from abicheck.dwarf_advanced import diff_advanced_dwarf` (and its
  diff-only siblings) keeps working** after their move to
  `compare/dwarf_advanced_diff.py` — a lazy module-level `__getattr__`
  shim resolves the old import path (mirrors `cli_buildsource.py`'s own
  shim for the identical pattern), so a downstream caller that has not
  yet migrated to the new canonical import path does not see an
  `ImportError`.
