### Removed

- **Four unreferenced internal helpers are deleted** — `DispositionLedger.extend_records` (its docstring pointed at a `policy.disposition_merge` module that does not exist), `_compiler_options.join_gcc_options` (the round-trip partner of `split_gcc_options`, with no caller), `diff_symbols._type_unknown` (the `"?"` sentinel test had no caller; `_UNKNOWN_TYPE` stays), and the `DwarfSnapshotBuilder._elf_meta` attribute, which was assigned and never read. None is public API or documented in `docs/`, and no CLI flag, exit code, report field or verdict changes.
