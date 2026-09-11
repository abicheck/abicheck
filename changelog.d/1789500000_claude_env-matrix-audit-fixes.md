### Fixed

- **A `--no-baseline` audit of a candidate exceeding a declared
  `deployment.runtime_floors` value now correctly reports BREAKING, not
  `COMPATIBLE_WITH_RISK`.** Two of the six standalone declared-runtime-
  floor / wheel-packaging checks (`PLATFORM_BASELINE_FLOOR_RAISED`,
  `MACOS_DEPLOYMENT_TARGET_RAISED`) default to a RISK verdict in the
  catalog, but each only ever fires when the candidate's own requirement
  already exceeds the declared floor -- there was previously no promotion
  step for these two, so both `compare` (two-sided, e.g. a candidate
  compared against itself) and `compare --no-baseline` silently under-called
  a genuine deployment-floor violation. A new shared
  `diff_versioning.promote_baseline_violation_findings` unconditionally
  promotes any occurrence of these two kinds to BREAKING, called from both
  `checker._env_matrix_contract_changes` and
  `workflows.env_matrix_audit.env_matrix_candidate_findings` so the two
  paths cannot disagree, and (on the `compare` two-sided path) called
  *before* suppression filtering rather than after, so a suppressed
  occurrence still records the promoted BREAKING verdict rather than the
  catalog's unpromoted RISK default (`vision.md`'s "record before
  disposing" -- a suppressed finding's own record must show what it
  actually was). `diff_versioning._SONAME_BUMP_CANNOT_FIX_KINDS` now also
  lists these two kinds, since a SONAME bump does not fix "the binary
  requires a newer GLIBC/macOS SDK than the declared floor" any more than it
  fixes the sibling checks already listed there.
  `WHEEL_RPATH_NOT_PORTABLE` is deliberately **not** promoted: unlike the
  two kinds above, `check_wheel_rpath_not_portable`'s own docstring says a
  non-`$ORIGIN`-relative RPATH entry is only "almost always" a build
  artifact -- a portability heuristic, not proof the named dependency is
  actually unresolvable (a separate closure/reachability check would be
  needed for that) -- so it keeps its catalog-default RISK verdict on both
  paths rather than manufacturing a hard break from heuristic evidence.
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
  `--no-baseline` audit schema `1.4`. The packaged JSON
  Schema files (`abicheck/schemas/compare_report.schema.json`,
  `abicheck/schemas/audit_report.schema.json`, and their published mirrors
  under `docs/reference/schemas/v1/`) now declare `env_matrix_source_sha256`
  and the `4.2`/`1.4` version bumps too -- they previously still described
  the pre-bump shape, so a schema-driven consumer (one that discovers fields
  from the schema rather than only validating against it) could not see the
  field exists.
- **`env_matrix_source_sha256` now reaches every `--no-baseline` audit
  format, not only JSON.** A within-floor audit (no finding, so no other
  evidence of the declared `deployment.runtime_floors`/`EnvironmentMatrix`
  contract) was indistinguishable from a run with no deployment contract at
  all in Markdown, the one-line summary, SARIF, and JUnit -- the digest
  above previously reached only `_document_json`. Each format now projects
  it through its own existing digest/provenance projection point: a new
  "Deployment floor digest" bullet in Markdown, a `; deployment floor
  <digest>` clause in the oneline summary, `runs[].properties.
  envMatrixSourceSha256` in SARIF, and an `env_matrix_source_sha256` suite
  property in JUnit -- all omitted, never a placeholder, when no matrix was
  declared, matching the JSON field's own additive convention.
- **`EnvironmentMatrix` (and its `sycl`/`cuda` sub-objects) are now
  hashable.** `CompareRequest` is a frozen dataclass whose generated
  `__hash__` needs every field to be hashable; `EnvironmentMatrix`'s own
  plain, non-frozen `@dataclass` had `__hash__` implicitly set to `None`,
  making any `CompareRequest` supplying real deployment configuration
  unhashable (`TypeError: unhashable type: 'EnvironmentMatrix'`) and
  breaking a typed-API caller using requests as set members or cache keys.
  Fixed with an explicit `__hash__` on all three dataclasses, projecting
  their `list`/`dict` fields into a hashable, order-independent tuple —
  the fields themselves stay `list`/`dict` (several call sites read
  `runtime_floors` via `.get(...)`), and nothing in the codebase mutates
  either in place after construction.
- **A directory/package `compare-release` with a declared `deployment:`
  contract now publishes its `env_matrix_source_sha256` digest in the
  release JSON**, both per library (surviving
  `_strip_diff_results_and_adjust_verdict` discarding each library's
  `DiffResult`) and once at the release envelope, since one release run
  threads the identical `EnvironmentMatrix` to every library. Release JSON
  schema `1.2`.
