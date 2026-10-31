### Added

- **ProjectSnapshot packages carry the ADR-062 D14/D15 carriers.** A snapshot with a failed or blocked section, an unknown section, section or record extensions, or a document-level must-understand list is now written to a directory package instead of being refused: the carriers are stored as one object named by the artifact ref's new `carriers` field (that ref is written at `ref_schema_version` 2, so an older reader refuses it rather than reading the left-out sections as empty), and they are inside the stored `evidence` digest. single-file → package → load → save is exact. G38 bundle documents and G40 bundle archives still refuse such a snapshot.
- **`function_presence` detector.** When the `functions` detector is not comparable because a section it reads (`types`, `debug`, …) is blocked or failed, a removed function is still reported from the declarations and export table alone, classified as `functions` would classify it. It never runs when `functions` does, so a removal is never reported twice.
- **Package garbage collection** (`abicheck.workflows.package_gc.collect_package_garbage`): removes the refs and objects a package's live `manifest.json` no longer names, under the publish lock and only past a grace period (default one hour), so it never races a writer and a reader holding an older manifest fails closed rather than reading wrong data.
- **`transport` digest for package archives** (ADR-062 D16): each member of a packed ProjectSnapshot archive carries the SHA-256 of its stored bytes in its zip extra field, verified before anything is unpacked; archives packed before it are still read.

### Fixed

- **A PE dump whose PDB was found but failed to parse** now records the `debug` section as `failed` with the parse error instead of storing it as present and empty; the analyses that read debug information are reported not comparable, live and stored alike. A missing PDB stays not collected.
- **A section stored as `not_collected` no longer re-saves as present** (which made the re-saved snapshot unreadable).
- **A section-level must-understand name this build does not understand, inside a directory package,** now blocks only that section's analyses (as in a single-file snapshot) instead of refusing the whole package.
- **Unknown keys nested inside a record** (a parameter, record field, enum member, ELF symbol or import, PE/Mach-O export) and on the binary section's ELF/PE/Mach-O metadata object are preserved on re-save, not only a record's top-level fields.
- **Package publishing serializes writers on Windows** (`msvcrt.locking`), as it already did on POSIX.
