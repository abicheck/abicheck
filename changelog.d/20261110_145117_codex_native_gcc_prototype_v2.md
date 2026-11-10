### Added

- Add opt-in `compile.frontend: gcc` for Linux ELF and header-only C/C++ using a version-matched native GCC plugin. Includes an explicit plugin build helper, discovery, compiler/plugin-aware snapshot cache identity, typed facts, persistent partial assurance and GCC 13/14 workflow tests. `auto` keeps its existing behavior; unsupported manifests/device/source replay fail explicitly.

### Changed

- Header backend registry now lives in `extract.headers.backend_registry`; tests and dispatch reach the owner directly.
- Dump depth provenance preserves the extractor's existing metadata and coverage limitations.
