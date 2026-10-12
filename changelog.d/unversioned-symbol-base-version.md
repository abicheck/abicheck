### Fixed

- **`unversioned_exported_symbol` no longer flags a versioned library's base
  API** — a symbol bound to the library's base version definition
  (`VER_FLG_BASE`, what `objdump -T` prints as `Base`; zlib's
  `deflate`/`inflate`) looked unversioned and was flagged, so a self-compare
  of zlib reported 41 advisory findings. Each ELF export now records its
  binding (`ElfSymbol.version_binding`: `base`, `unversioned`, `named`,
  `unknown`; snapshot schema v58), and the check flags only an export with
  no version at all. A pre-v58 snapshot cannot tell the two apart: its
  candidates are reported as not evaluated (the check's coverage row reads
  `partial`), never flagged. `ElfSymbol.version` is unchanged. The
  version-correlation helpers moved from `elf_metadata.py` to
  `extract/elf_symbol_versions.py`.
