<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`dump -H api.h` with no `SO_PATH` (and no `--sources`/`--build-info`)
  now runs a real header-AST parse instead of writing an empty snapshot.**
  Workstream F S1 ("Header-only comparison"): a binary-less `DumpRequest`
  whose only evidence is public headers routes through the same shared
  `execute_dump_request` pipeline a binary dump uses, producing an
  `AbiSnapshot` with real functions/types/enums/constants parsed from the
  headers (`AbiSnapshot.header_only`, schema v44). `compare old.json
  new.json` needs no changes to consume two such snapshots. The report's
  existing `not_evaluated` detector convention now also states, explicitly,
  which binary-level capabilities a header-only comparison structurally
  cannot provide (symbol presence/versioning, ELF/DWARF layout, vtable/RTTI
  linkage identity, mangled-name linkage-level churn) — never silently
  absent.

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
<!--
### Fixed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
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
