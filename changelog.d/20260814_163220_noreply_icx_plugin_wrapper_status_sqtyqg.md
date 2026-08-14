<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The `abicheck-cc` wrapper's clang source extractor no longer collapses
  Intel oneAPI's `icx`/`icpx`/`dpcpp`/`dpcpp-cl` compiler to the generic
  `compiler_family: "clang"` label.** A `source_facts` pack collected under
  the `icpx` wrapper previously recorded the same `compiler_family` a
  vanilla Clang run would, silently losing the one signal (besides DWARF
  `DW_AT_producer`, when present) that the frontend was actually a
  downstream fork — and, since `check_fact_set_compatibility` compares
  `compiler_family` between two packs, silently hid a real
  `compiler_family_mismatch` warning when comparing a vanilla-Clang pack
  against an Intel-oneAPI one. Now reports `"intel-llvm"` for the Intel
  fork, `"clang"` unchanged for everything else.

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
