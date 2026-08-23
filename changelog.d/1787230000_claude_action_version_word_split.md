### Fixed

- **The composite GitHub Action word-split `old-version`/`new-version`
  inputs on whitespace**, so a version label containing a space (e.g.
  `old-version: '1.0 (release build)'`) was silently corrupted into three
  repeated `--version old=...` flags, of which only the last survived — the
  report rendered `(release build)` and the real version, `1.0`, was
  entirely lost. `action/run.sh`'s `add_sided_flag` helper is designed for
  genuinely list-valued inputs (headers/includes/paths), where one flag per
  whitespace-separated word is correct; a version label is a single opaque
  string that must reach the CLI unsplit. Added `add_sided_scalar_flag`,
  used for `--version` — every other `add_sided_flag`/`add_single_flag` call
  site was audited and is unaffected (`add_single_flag` already passes its
  value through unsplit).
