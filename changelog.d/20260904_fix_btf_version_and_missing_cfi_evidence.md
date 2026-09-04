### Fixed

- **BTF parsing now flags an unsupported header version instead of
  silently trusting it** — `_parse_header()` only logged a warning when a
  BTF blob's version differed from the sole one this parser's record
  layout actually understands (`BTF_VERSION == 1`) and kept parsing
  anyway, so a future/different-version blob could be misdecoded (wrong
  field widths, misread type kinds) while `extraction_partial` stayed
  `False`. `parse_btf_from_bytes` now marks the receipt partial whenever
  the parsed version doesn't match.
- **DWARF advanced-channel CFI extraction now treats a total absence of
  unwind sections as incomplete evidence** — a binary carrying real DWARF
  debug info but with neither `.eh_frame` nor `.debug_frame` present (both
  call sites of `_parse_frame_registers` only run when real DWARF DIEs
  exist) previously reported `evidence_state="parsed"` with
  `frame_registers`/`callee_saved_regs` empty for every function, treating
  "no unwind data at all" the same as "nothing to be incomplete about" —
  a self-comparison of such a binary could report
  `analysis_assurance.status="complete"` and exit `0` under
  `--require-complete-analysis`. `_parse_frame_registers` now returns
  incomplete for this shape too, on both the standalone
  `parse_advanced_dwarf` entry point and `dwarf_unified.
  parse_dwarf_from_session` (the unified path real ELF dumps use).
