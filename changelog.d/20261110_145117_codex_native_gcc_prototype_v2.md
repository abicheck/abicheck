### Added

- Add opt-in `compile.frontend: gcc` for Linux ELF and header-only C/C++ using a version-matched native GCC plugin. Includes an explicit plugin build helper, discovery, compiler/plugin-aware snapshot cache identity, typed facts, persistent partial assurance and GCC 13/14 workflow tests. `auto` keeps its existing behavior; unsupported manifests/device/source replay fail explicitly.

### Changed

- Header backend registry now lives in `extract.headers.backend_registry`; tests and dispatch reach the owner directly.
- Dump depth provenance preserves the extractor's existing metadata and coverage limitations.

### Fixed

- Native GCC projection no longer reads an absent plugin key as `False` (virtual, deleted, const, linkage, ...): every schema-v2 boolean is required, only C++-only function/record properties may be absent from the C plugin, and a missing, non-boolean or malformed row fails the dump with no Clang fallback instead of recording confirmed facts or raising a traceback.
- `python -m abicheck.extract.headers.gcc.build` is a bounded Click command (deadline-supervised compiler run, a clear error on failure); native GCC probes use the shared hang guard and compiler options are tokenized by the canonical splitter.
- A configured "GCC" that prints no `--version` banner is refused with a clear error instead of an `IndexError` traceback.
