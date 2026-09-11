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

- **Preserve cross-key compiler-flag ordering in the Action config-overlay's
  `compile:` merge.** `action_config_overlay._merge_compile_block` used to
  merge `compile.std` and `compile.defines`/`compile.options` as
  independently-precedenced fields, but the real
  `cli_options.merge_compile_config` folds all three into ONE flattened
  compiler-argv token sequence per document and decides a same-flag
  conflict by relative position in that combined sequence — not by which
  YAML key either side used. A checkout `std: c++17` plus a sources-root
  `options: [-std=c++23]` therefore let the sources-root value win instead
  of the checkout value the real two-stage pipeline actually produces,
  silently changing the compiled language mode (and therefore the
  extracted API surface and findings) for any assurance run or Action
  overlay hitting this combination. The fix extracts the real per-document
  token synthesis into a new shared `cli_options.compile_config_argv_tokens`
  primitive (used by `merge_compile_config` itself, closing the risk of a
  second, independent reimplementation drifting from it again) and a new
  `cli_options.merge_compile_std_fields` helper that folds `std`/`defines`/
  `options` together whenever both documents contribute at least one of
  those three fields, preserving each document's compiler-token ordering.

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
