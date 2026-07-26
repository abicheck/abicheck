<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A manifest `project_owned` include resolving outside the manifest's own
  directory** (the primary documented `../src` sibling-of-checkout
  example) **fingerprinted with a raw, machine-specific absolute path**
  instead of a stable, mount-point-independent token — breaking
  `profile_fingerprint` stability across two checkouts of the identical
  relative layout at different filesystem locations, the exact scenario
  this escape hatch exists to make stable. `_manifest_declared_includes`
  now uses `os.path.relpath` (which can climb back up, e.g. `"../src"`)
  instead of `Path.relative_to` (which raises for a non-descendant path).
- `stack_report.py`'s Markdown renderer now backtick-wraps a dependency's
  `not_comparable_reason` (a free-text string that can embed a filesystem
  path) instead of interpolating it raw, matching the file's existing
  convention for other embedded free-text — an unwrapped path containing
  `*`/`_` could otherwise corrupt the rendered report's formatting.
- `compare --dump-manifest <malformed.yaml>` on a directory/package
  (release) comparison now fails with the clear "not supported for
  directory/package" message instead of a confusing "invalid YAML" one —
  the manifest is parsed only after the directory/package rejection runs,
  not before it (both were always a nonzero exit; only the message
  differed).
- Corrected `LabeledIncludePathParam`'s docstring, which claimed
  (present tense) to be "`dump`'s own `--include` type" — it is built and
  unit-tested but not yet wired into `dump_cmd`'s actual `--include`
  option (`comparability.py`'s module docstring already documented this
  gap accurately; this one class's own docstring contradicted it).
