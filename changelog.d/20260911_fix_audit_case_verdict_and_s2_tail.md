### Fixed

- `run_special_cli_examples.py`'s audit lane now checks each `findings[]` row's
  own retained `verdict` against the catalog's `abi_break`/`api_break`/
  `bad_practice` flags. The top-level verdict is `null` for every
  `compare --no-baseline` audit, so asserting only on finding *kind* let a
  reclassification pass silently in either direction. It also records
  `unvalidated_assertions` for ground-truth assertions the public report
  carries no equivalent of (`provider_assertions`), and
  `collect_full_example_matrix.py` surfaces that on the row, so `COVERED` is
  never read as "every assertion for this case was checked".
- `test_s2_fields_keep_positional_tails.py` asserted a hardcoded `[-3:]` tail
  for `BundleDiffResult`, so a correctly-appended `env_matrix_source_sha256`
  broke it while its two siblings, written as `[-len(...):]`, only needed
  their tuple extended.
- The published compatibility skill still described `--env-matrix` as a live
  per-run flag after ADR-068 D5 demoted it to `.abicheck.yml`'s `deployment:`.
