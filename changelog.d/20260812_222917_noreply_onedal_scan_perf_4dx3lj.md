<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Parallel `clang` AST extraction now records diagnostics in deterministic
  input order, not subprocess-completion order.** A PR review found that
  `preprocessor_scan.py`'s new parallel probe pool appended diagnostics
  straight from worker threads, making the reported "first failure" sample
  nondeterministic across identical, pinned runs. A follow-up audit found the
  same pattern copy-pasted across all six `Clang*GraphExtractor` classes
  (`call_graph.py`, `callback_graph.py`, `override_graph.py`,
  `macro_graph.py`, `type_graph.py`, `template_graph_extractor.py`), whose
  diagnostics also feed `inline_graph_fold.py`'s persisted build-source
  artifact and `cli_buildsource`'s `note:` CLI lines. Fixed generally via a
  new leaf module, `abicheck/parallel_probe.py` (`run_parallel_probes`,
  `OrderedDiagnostics`), and applied to all seven affected extractors: each
  compile unit's own diagnostics are now collected per-call and folded back
  in input order once its batch completes, so a "which unit failed first"
  report reads the same on every run of the same input.

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
