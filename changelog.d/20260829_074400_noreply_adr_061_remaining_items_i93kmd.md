<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Internal: clang record-entity parsing moved into `extract/headers/clang/records.py`** —
  ADR-061 Phase 5's third entity-module split, now landed on both header-AST
  backends. `_ClangAstParser.parse_types`/`_build_record`/`_parse_fields`/
  `_collect_fields`/`_make_field` and five record-only helpers
  (`_clang_record_is_final`, `_bitfield_width`, `_anonymous_member_names`,
  `_parse_bases`, `_owned_tag_id`) moved as free functions taking the
  parser's categorized declaration lists explicitly, mirroring castxml's own
  `records.py` slice. `decl_is_public` (shared with constant parsing) moved
  into `extract/headers/clang/context.py`; six previously-private
  `dumper_clang_qualifiers.py` helpers were made public in place, each
  keeping a back-compat private alias. No behavior change — every existing
  caller keeps resolving through a one-line delegation, and a real clang
  header dump of a multi-inheritance/virtual-method/bitfield/anonymous-
  aggregate header produces byte-identical output before and after.

<!--
### Added

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
