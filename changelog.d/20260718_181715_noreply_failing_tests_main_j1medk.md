<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Changed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
### Fixed

- **Fixed a function-rooted `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` finding
  never joining its public-header graph node on macOS** — clang's
  `-ast-dump=json` reports a C++ decl's `mangledName` with the Mach-O
  ABI's extra linker-symbol-table underscore still attached
  (`__ZN4demo9configureE...`), but the `Function`/`Variable` objects the
  header-only graph (`--header-graph`) must join that identity against
  already have it stripped (the same normalization
  `macho_metadata.py` applies to the binary's own export table). A public
  type's field/base-type dependency was unaffected (type names carry no
  such decoration), but a public *function's* parameter-type/call
  dependency landed on an orphaned graph node with no public-visibility
  tag, so the risk finding was silently dropped whenever the "reaching"
  entity was a function rather than a type.

<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
