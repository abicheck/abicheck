### Fixed

- **`inline_body_references_renamed_member` without DWARF** — the detector recognised a renamed member of an internal (`detail::`/`impl::`) record only when debug info supplied the record's namespace, so the finding vanished for release builds, stripped libraries and macOS dylibs (whose DWARF stays in the object files). A header-derived rename now resolves the record's qualified name from the snapshots' own records, when that name is unique.
