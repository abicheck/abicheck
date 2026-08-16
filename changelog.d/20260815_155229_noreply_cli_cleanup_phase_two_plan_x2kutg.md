<!-- Follow-ups to PR 1 (CLI cleanup phase two), found by Codex review after that commit pushed. -->

### Fixed

- **`compare --profile quick` now reports the scoped gate, not the full
  library, under `--used-by`/`--required-symbol`.** The internal one-line
  summary format previously fell through the scoped-compat fold untouched,
  so it could print the full-library verdict/counts even though the process
  actually exits on the scoped result -- e.g. printing `BREAKING` while
  exiting `0`, or the reverse. `--profile quick`'s one-liner now reuses the
  same already-reviewed scoped-fold logic `--format json` uses, so the two
  can never disagree about the verdict for the same invocation.

- **`validation/scripts/run_matrix.py` no longer passes the removed
  `--recommend` flag.** `--format json` already carries
  `release_recommendation` unconditionally.
