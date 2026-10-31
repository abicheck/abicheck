### Fixed

- **Reports, help and docs no longer name option spellings that ADR-091
  removed.** The scoped-gate text heading and the PR-comment and HTML
  scoped-gate notes now say `consumers.required_symbols` instead of the
  removed `--required-symbol(s)` flag. `compare --dry-run` does the same. The
  markdown note for findings filtered out of the public surface no longer
  says "Pass `--view filtered`" (a removed flag and a retired view token); it
  says where the findings are listed: stderr and `out_of_surface_changes` in
  `-o json=...`. `--acknowledgments` help, the Action's `acknowledgments`
  input description and the generated references now name
  `acknowledgments.file`/`acknowledgments.breaks` instead of the old singular
  key. The `compare_report` JSON-schema descriptions say
  `consumers.required_symbols`, `consumers.manifests` and `output.view`. The
  Action's evidence-contract summary says `--abi3-floor`, and its audit-only
  `old-label` error names `--label`.
- `architecture/option-vocabulary.yaml` gains a `removed:` list: every CLI
  option, config key and typed-API keyword ADR-091 deleted, with its
  replacement. A new guard (`tests/test_no_removed_option_spellings.py`)
  reads that list and the retired Action inputs, and fails when one of them
  appears in a user-visible string, `--help`, the Action layer or a current
  doc page.
