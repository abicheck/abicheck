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

- **`deployment:` config now applies to every `compare` operand shape, and
  round-trips correctly.** Four gaps from the `--env-matrix` → `.abicheck.yml`
  `deployment:` demotion are closed: an explicitly-empty `deployment: {}`
  block no longer collapses to absent (`None`) on a `to_dict()`/`from_dict()`
  round-trip; an unrecognized `deployment:` key (top-level or nested
  `sycl`/`cuda`) is now a hard config error instead of a silently-ignored
  typo that could disable the whole runtime-floor check;
  `deployment.runtime_floors` now reaches both the stored-BundleFacts
  comparison drivers (stored/live and stored/stored) and `compare
  --no-baseline` audits, so a candidate exceeding a declared floor is
  reported `BREAKING` there too, matching the scalar `compare` path instead
  of silently passing as a mere `RISK`. `EnvironmentMatrix.from_dict` gained
  an opt-in `strict=True` parameter for the new config-loading check;
  existing typed-API/YAML-file callers keep the original lenient (warn, not
  raise) behavior.
- **`compare --no-baseline` now stamps `env_matrix_source_sha256` when a
  `deployment.runtime_floors` matrix is declared.** Previously this field
  stayed `None` on a `--no-baseline` audit even when the declared matrix
  changed the run's findings and verdict, since that path deliberately never
  passes `env_matrix=` into its own self-diff (to avoid re-running the
  candidate checks and tripping the ADR-068 D3 identity invariant). The
  digest is now computed separately, with the identical helper `compare()`
  uses, so a typed caller can tell an audit governed by a deployment contract
  apart from one with none.

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
