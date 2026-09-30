### Fixed

- **Linker-reserved ELF symbols no longer read as undocumented exports.** A
  gold- or Bazel-linked library exports `__bss_start`/`_edata`/`_end` whatever
  its headers say, and `exported_not_public` reported each one as a RISK
  finding, so a purely additive change read `COMPATIBLE_WITH_RISK`. Every
  export-table-walking cross-source check now reads the table through one
  reader that drops the shared `elf_symbol_filter.is_linker_reserved_symbol`
  class (ELF only), rather than each check filtering on its own.
- **`compare` now replays the same source TUs as `dump` for the same
  evidence.** A pack-shaped `--build-info new=<pack>` next to a raw
  `--sources new=<tree>` was routed out-of-band instead of into the side's
  inline dump, so L4 replay discovered the whole tree's compile DB (19 TUs in
  the reporting case) where `dump --sources <tree> --build-info <pack>`
  replayed only the pack's compile units (1). The inline dump now receives
  exactly the inputs a standalone `dump` would.
- **Failed L4 translation units are named in the coverage row**, e.g.
  `2 extractor failures (src/a.cc, src/b.cc)`, not only counted
  (`source_abi.coverage.failed_compile_units`, capped at 20 names).
