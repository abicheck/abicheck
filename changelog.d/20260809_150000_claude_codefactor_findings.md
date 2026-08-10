### Fixed

- **CodeFactor (pylint) findings across the package** — the redundant
  no-op arms in `pr_comment._release_row`'s severity tally are now plain
  `+=` accumulations; `package.py`'s six extraction-security and RPM-timeout
  raises chain their cause (`raise ... from exc`) instead of dropping it;
  `environment_matrix.EnvironmentMatrix.from_yaml` and
  `scripts/benchmark_comparison`'s shebang probe pass an explicit
  `encoding="utf-8"` rather than depending on the platform default (as do
  six more text reads/writes across `eval/` and `validation/scripts/`);
  thirteen unused tuple-unpack targets take the `_`-prefixed spelling the
  surrounding code already uses; and fifteen function-local imports of a
  module the file already imports at top level were removed. Also folded
  the two byte-identical copies of the enum-sentinel test
  (`*_LAST`/`*_MAX`/`*_COUNT`) in `diff_types` and `diff_platform` — each
  redefined once per loop iteration — into one shared
  `diff_helpers.is_sentinel_enum_member`. No behavior change.

### Security

- **Bandit's five high-severity `tarfile` findings (B202) resolved** — the
  conda-forge fetch helpers in `eval/condafetch.py` and
  `validation/scripts/conda_harness.py` apply the `data` extraction filter
  where the runtime supports it (on top of the existing per-member
  containment/link validation), and the harness's wrapper is renamed
  `extract_members_safely` because bandit matches the substring
  `extractall` in *any* callee name and so re-raised the finding at every
  call site of a wrapper spelled `safe_extractall`.
  `impact/use_cases.py`'s `yaml.load(..., Loader=_DuplicateKeyCheckingLoader)`
  gains the same explicit `# nosec B506` rationale the other strict-loader
  call sites already carry.
