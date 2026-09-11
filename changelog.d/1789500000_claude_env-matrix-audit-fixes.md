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
- **`deployment.runtime_floors.WHEEL_ARCH` (and the sibling `MUSLLINUX`/
  `WHEEL_CONTEXT` keys) now reject a YAML list, mapping, or bare boolean
  instead of silently stringifying it.** These three keys are exempt from
  the dotted-numeric-version check every other `runtime_floors` key gets
  (they carry a non-version token), but that exemption previously let
  `EnvironmentMatrix.from_dict` accept *any* type for them — `WHEEL_ARCH:
  [x86_64]` became the literal string `"['x86_64']"`, which the wheel-
  architecture-mismatch detector treats as an unrecognized claim and
  reports nothing for, silently disabling a hard wheel-architecture check
  instead of raising the config error `strict=True` promises. Now raises
  `ValueError` for a non-string value on any of the three keys, in both
  strict and lenient `from_dict` modes.
- **`EnvironmentMatrix` is now genuinely immutable, not merely hashable.**
  The hashability fix above computed a hashable *projection* of the
  still-mutable `runtime_floors`/`compilers` fields (and the `sycl`/`cuda`
  sub-objects' own `backends`/`gpu_architectures`) inside `__hash__`, which
  satisfies Python's hash contract only as long as nothing mutates those
  containers after construction — a `CompareRequest` carrying a real
  `EnvironmentMatrix` inserted into a dict/set became silently unfindable
  after a caller mutated `matrix.runtime_floors["GLIBC"] = "2.34"`, since
  the object's hash changed out from under the container. `runtime_floors`
  is now a `types.MappingProxyType` wrapping a private copy (item
  assignment raises `TypeError`; `.get(...)`/`.items()`/`in`/`len()` still
  work identically for existing callers), and `compilers`/`sycl.backends`/
  `cuda.gpu_architectures` are now `tuple`s rather than `list`s.
- **A directory/package `compare-release` with zero matched or completed
  library pairs, but a declared `deployment:` contract, now still publishes
  the correct `env_matrix_source_sha256`** in both the release JSON
  envelope and the `--output-dir` `summary.json` sidecar's
  `effective_config_fields["policy.env_matrix"]` — previously derived only
  by reading the digest off a completed per-library entry, which made a
  genuinely-configured contract indistinguishable from none whenever no
  library comparison completed. Computed once, directly from the resolved
  `EnvironmentMatrix`, via the new shared `checker.env_matrix_content_digest`
  (also now used by `compare()`'s own stamping and
  `workflows.no_baseline_compare`'s post-hoc field replacement, replacing
  three independent inline computations with one).
- **`.abicheck.yml`'s `deployment:` configuration namespace is now
  registered in `docs/_meta/topics.yaml`.** The `compare --env-matrix`
  demotion introduced this public config key but never registered it with
  the doc-ownership registry, so the ownership/documentation-review
  tooling had no `fact_sources` entry tracing the key back to
  `abicheck/environment_matrix.py` the way `build:`/`compile:` already
  trace to `abicheck/buildsource/build_config.py`. Added
  `abicheck/environment_matrix.py` to the existing `config-keys` topic's
  `fact_sources` (the topic already owning `reference/config-file.md`'s
  `deployment:` section and `reference/config-keys-reference.md`).
- **`deployment.compilers`/`sycl.backends`/`cuda.gpu_architectures` now
  reject a non-string list element instead of silently accepting or
  mis-coercing it.** `.abicheck.yml`'s `deployment: {compilers: [{name:
  gcc}]}` (a list of dicts, not strings) previously passed `from_dict`'s
  outer `isinstance(compilers, list)` check with the dict element left
  untouched inside the resulting tuple; since `EnvironmentMatrix` is now a
  genuinely frozen, hashable dataclass (the round-7 fix above), hashing a
  `CompareRequest` carrying that matrix raised `TypeError: unhashable
  type: 'dict'` at hash time, defeating the structural-hash guarantee for
  a config `from_dict` had already accepted as valid. `sycl.backends`/
  `cuda.gpu_architectures` had the identical outer-list-checked,
  elements-unchecked shape, silently stringifying a wrong-shaped element
  (e.g. `sycl.backends: [{driver: x}]`) into a nonsense backend name
  instead of raising. All three now validate every element is a `str` and
  raise a clear `ValueError` otherwise, in both lenient and `strict=True`
  `from_dict` modes.
- **A directory/package `compare-release`'s `env_matrix_source_sha256`
  digest now reaches the Markdown and JUnit release reports too, not only
  JSON.** The release-scoped digest (round-6/round-7 fixes above) was
  forwarded only into the JSON release envelope: the Markdown report never
  exposed it at all, and the JUnit report only exposed it indirectly
  through completed per-library `<testsuite>`s, so a release with zero
  matched/completed pairs lost the digest entirely even though the
  identical invocation's JSON output recorded it correctly. Markdown now
  renders a "Deployment floor digest" bullet (mirroring the two-sided
  `compare` report's own Markdown projection); JUnit now renders a
  dedicated, zero-test/zero-error `<testsuite name="abicheck.deployment">`
  property (`abicheck.report.junit_scope.append_env_matrix_suite`) that
  does not depend on any per-library comparison having completed.
