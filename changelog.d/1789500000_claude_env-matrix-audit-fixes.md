### Fixed

- **A `--no-baseline` audit of a candidate exceeding a declared
  `deployment.runtime_floors` value now correctly reports BREAKING, not
  `COMPATIBLE_WITH_RISK`.** Three of the six standalone declared-runtime-
  floor / wheel-packaging checks (`PLATFORM_BASELINE_FLOOR_RAISED`,
  `MACOS_DEPLOYMENT_TARGET_RAISED`, `WHEEL_RPATH_NOT_PORTABLE`) default to a
  RISK verdict in the catalog, but each only ever fires when the candidate's
  own requirement already exceeds the declared floor -- there was previously
  no promotion step for these three, so both `compare` (two-sided, e.g. a
  candidate compared against itself) and `compare --no-baseline` silently
  under-called a genuine deployment-floor violation. A new shared
  `diff_versioning.promote_baseline_violation_findings` unconditionally
  promotes any occurrence of these three kinds to BREAKING, called from both
  `checker._env_matrix_contract_changes` and
  `workflows.env_matrix_audit.env_matrix_candidate_findings` so the two
  paths cannot disagree. `diff_versioning._SONAME_BUMP_CANNOT_FIX_KINDS` now
  also lists these three kinds, since a SONAME bump does not fix "the binary
  requires a newer GLIBC/macOS SDK than the declared floor" or "RPATH isn't
  `$ORIGIN`-relative" any more than it fixes the sibling checks already
  listed there.
- **The declared-deployment-floor contract's content digest
  (`env_matrix_source_sha256`) now reaches the rendered JSON report.** The
  digest was previously stamped only onto the in-process `DiffResult`
  (`compare` and `compare --no-baseline` alike); a persisted/rendered report
  had no way to tell a run governed by a declared
  `deployment.runtime_floors`/`EnvironmentMatrix` contract from one with no
  deployment contract at all whenever the candidate stayed within its
  declared floor (no finding, so no other evidence of the matrix in the
  output). The JSON report for both `compare` and `compare --no-baseline`
  now carries a top-level `env_matrix_source_sha256` key (omitted, not
  `null`, when no matrix was declared) — report schema `4.2`,
  `--no-baseline` audit schema `1.4`. Markdown/SARIF/JUnit/oneline are
  unchanged: none of those formats surfaces any comparison-level
  policy/contract metadata like this today (the same is true of the
  existing `policy` field), so this fix follows that established,
  JSON-only convention rather than inventing a new one.
