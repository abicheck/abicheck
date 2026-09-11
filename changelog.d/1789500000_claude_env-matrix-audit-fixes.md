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
- **`deployment.runtime_floors.WHEEL_ARCH` now rejects an unrecognized
  architecture token, not just a wrong-shaped value.** The round-8 fix above
  validated `WHEEL_ARCH`'s value is a `str`, but never that the string is
  one `diff_wheel_deployment.check_wheel_tag_architecture_mismatch` actually
  recognizes -- `WHEEL_ARCH: x86-64` (a hyphen typo for `x86_64`) loaded
  successfully into a valid `EnvironmentMatrix`, and that detector silently
  treats an unrecognized claim identically to "no claim declared", reporting
  nothing even against a binary of a visibly different architecture --
  silently disabling the hard wheel-architecture-mismatch gate a strict
  config believes it enabled. `abicheck/model/wheel_arch_claims.py` is a new
  leaf module holding the exact vocabulary the detector's own per-claim
  dicts (`_ARCH_CLAIM_TO_ELF_MACHINE`/`_ARCH_CLAIM_TO_MACHO_CPU_TYPE`)
  recognize, imported by both `environment_matrix.py`'s config-parse
  validation and `diff_wheel_deployment.py` itself (which now asserts at
  import time that its own dicts' keys union to exactly that vocabulary),
  so the two can no longer independently drift on what's supported. Raises
  `ValueError` naming the rejected token and listing every valid one, in
  both lenient and `strict=True` `from_dict` modes.
- **A direct `dataclasses.asdict()` call over an `EnvironmentMatrix` is now
  JSON-serializable without needing `to_dict()`.** The round-7
  `copyreg`-reducer fix above made `pickle`/`copy.deepcopy` safe for
  `types.MappingProxyType`, but `dataclasses.asdict()`'s own field recursion
  falls back to `copy.deepcopy` for a `MappingProxyType`-typed field too --
  which, even with that reducer registered, reconstructs *another*
  `MappingProxyType`, not a plain, JSON-serializable `dict`, so
  `json.dumps(dataclasses.asdict(matrix))` still raised `TypeError: Object
  of type mappingproxy is not JSON serializable`. `runtime_floors` now uses
  a dedicated `dict` subclass, `abicheck.model.frozen_str_dict.
  FrozenStrDict` (immutable -- every mutator raises -- and hashable, like
  the `MappingProxyType` it replaces), which `asdict()`'s own
  `isinstance(obj, dict)` branch recognizes and recurses into natively,
  producing a plain-dict-shaped, directly JSON-serializable result with no
  `to_dict()` unwrapping required.
- **`EnvironmentMatrix`'s plain scalar fields (`abi_version`,
  `libstdcxx_dual_abi`, `target_os`, `target_arch`) now reject a
  wrong-shaped value instead of silently accepting it.** Unlike every list-
  and mapping-typed field on this dataclass (all validated by prior rounds
  above), these four scalar `str | None` fields were assigned straight from
  the raw config dict with no type check at all -- `deployment: {abi_version:
  {bad: shape}}` loaded successfully into a frozen, hashable
  `EnvironmentMatrix`, and `hash(matrix)` (and hashing any `CompareRequest`
  containing it) then raised `TypeError: unhashable type: 'dict'` far from
  where the bad value was read. Rather than hand-writing a fifth
  field-specific check, `abicheck/model/dataclass_scalar_validation.py` is a
  new leaf module that derives which fields to validate directly from the
  dataclass's own `str`/`str | None` type hints (`typing.get_type_hints` +
  `dataclasses.fields`), so a scalar field added to `EnvironmentMatrix` in
  the future is validated automatically instead of needing its own
  dedicated fix next time. Raises `ValueError` in both lenient and
  `strict=True` `from_dict` modes; a well-typed matrix still hashes cleanly.
- **`FrozenStrDict` (`runtime_floors`'s concrete type) now also blocks the
  `|=` in-place-union operator.** `dict.__ior__` mutates the receiver at the
  C level and returns it, bypassing every mutator override the earlier
  rounds above added (`__setitem__`, `update`, `pop`, `popitem`, `clear`,
  `setdefault`) entirely -- `matrix.runtime_floors |= {"GLIBC": "2.34"}`
  previously mutated the dict in place undetected, changing the hash of any
  `EnvironmentMatrix`/`CompareRequest` already holding it out from under a
  set/dict container, the exact same hash-invariant violation the earlier
  rounds fixed for every other angle. `__ior__` is now overridden to raise
  `TypeError` like its siblings. Auditing the rest of CPython's `dict`
  mutation surface found every other mutator already correctly blocked
  (`__setitem__`, `__delitem__`, `update`, `pop`, `popitem`, `clear`,
  `setdefault`); `copy()` is intentionally left alone, since it returns a
  fresh plain `dict` rather than mutating `self`.
  `TestFrozenStrDictContract` gained a `dir(dict)`-driven completeness
  sweep (`test_dict_mutator_probes_cover_every_mutating_dict_method`) that
  probes every callable `dict` exposes and fails if a future Python release
  adds a new in-place mutator this module hasn't audited, plus a
  belt-and-suspenders check that every probed name is an override
  `FrozenStrDict` actually defines, not one it merely inherits.
- **`FrozenStrDict` now also blocks re-running `__init__` on an
  already-constructed instance.** Being a `dict` subclass rather than a
  fresh wrapper object, calling `.__init__(some_mapping)` a *second* time
  directly on an existing instance re-ran `dict.__init__`, repopulating the
  receiver's storage in place at the C level -- bypassing every mutator
  override the earlier rounds above added, none of which intercept
  `__init__` being invoked again. Left unguarded, this changes the hash of
  an instance already embedded in a hashed container (e.g. a
  `CompareRequest`), the identical hash-invariant violation the `__ior__`
  fix above closed from a different angle. `__init__` now raises `TypeError`
  on any call after the first, guarded by a one-shot `_initialized` sentinel
  instance attribute set once real construction completes; ordinary
  construction and the `__reduce__`-driven reconstruction `pickle`/
  `copy.deepcopy` use are unaffected, since the sentinel is not yet set on a
  brand-new instance. The `dir(dict)` completeness sweep above previously
  missed this gap because its zero-argument probe call is exactly the shape
  under which `dict.__init__()` on an already-populated dict is a documented
  no-op -- it does not mutate, even though `dict.__init__(some_mapping)` (the
  shape that actually matters) does. The sweep now also tries a one-argument
  probe call for every candidate, so a future gap of this same shape is
  caught mechanically instead of needing a human to notice the zero-arg
  blind spot again.
- **The compatibility HTML report (`compat_html=True`) now also renders the
  declared-deployment-floor digest.** A prior round's fix projected
  `env_matrix_source_sha256` into every `compare` output format including
  "HTML" -- but there are two HTML code paths, and that fix only reached the
  native `abicheck.report.render_html_document.render_html_document` layout;
  the separate ABICC-compatible clone layout
  (`_render_compat_html_document`, `generate_html_report(...,
  compat_html=True)`) never read the field at all, so a `compat_html=True`
  report silently omitted an active deployment contract a `compat_html=False`
  report on the identical result showed. The digest now renders as a
  "Deployment Floor Digest" row in that layout's existing "Test Info" table
  (the same key/value convention its `Library Name`/`Version #1`/`Version
  #2` rows already use), omitted entirely -- never a placeholder -- when no
  `deployment:` contract governed the run.
- **`CompareRequest.env_matrix_path` is restored as a backward-compatible
  constructor parameter.** The `compare --env-matrix` demotion (ADR-068 D5)
  replaced this documented, released 0.4.0 field outright with
  `env_matrix: EnvironmentMatrix`, so a Tier-2 caller built exactly per the
  previously-published `CompareRequest(..., env_matrix_path=Path(...))`
  shape (credited in `CHANGELOG.md`'s own 0.4.0 entry) failed at
  construction with `TypeError: unexpected keyword argument
  'env_matrix_path'` — not a deprecation warning — contradicting that same
  amendment's claim that the typed Python API was unaffected.
  `env_matrix_path: Path | None` is kept as a genuine, still-accepted field;
  `__post_init__` resolves it into `env_matrix` via the existing
  `workflows.input_resolution.load_env_matrix` loader (the same one the
  retired CLI flag used) whenever `env_matrix` itself wasn't also given,
  and is then consumed (reset to `None`) so a later `.replace()` on an
  already-resolved request can't see both fields populated and spuriously
  raise. Passing both `env_matrix` and `env_matrix_path` together is a
  clear `ValidationError` (ambiguous which one should apply) rather than a
  silent, unstated precedence rule.
- **`compare --format oneline` now also carries the declared-deployment-
  floor digest.** The earlier fix in this same fragment projected
  `env_matrix_source_sha256` into JSON/Markdown/SARIF/HTML/JUnit, but the
  oneline/`--stat` path short-circuits in `service_render.render_output`
  straight to `reporter_markdown.to_stat`, before the shared
  `ReportEnvelope` projection point that fix was added at — so a clean
  `deployment:`-governed comparison still read identically to one with no
  deployment contract at all in the one format most likely to be a CI log's
  only line. `to_stat` now carries the same field through its
  `ReportDocument` mapping, and `report.render_text.render_stat_document`
  renders it as the identical `; deployment floor <digest>` clause the
  `--no-baseline` audit report's own oneline renderer already established
  — omitted, never a placeholder, when no matrix was declared.
- **`deployment.runtime_floors.WHEEL_ARCH`'s architecture-mismatch gate no
  longer silently disables itself for a claim that's real but belongs to
  the *other* binary format.** The round-9 fix above (the "unrecognized
  architecture token" `ValueError`) validated `WHEEL_ARCH` against the
  cross-format *union* of every token either detector dict recognizes
  (`WHEEL_ARCH_CLAIMS`) — but a token from that union can still be
  meaningless for the artifact actually under comparison: `WHEEL_ARCH:
  arm64` (a real, Mach-O-only token) passed config validation even when
  comparing an ELF binary, and `_elf_arch_mismatch` has no
  `_ARCH_CLAIM_TO_ELF_MACHINE` entry for `arm64` at all, so it returned `[]`
  unconditionally — even against a genuinely x86_64 ELF binary. The
  symmetric gap held for an ELF-only token (e.g. `aarch64`) declared
  against a Mach-O artifact. Fixed at diff time, where the binary's actual
  format is known: `_elf_arch_mismatch`/`_macho_arch_mismatch` now treat a
  claim that's in `WHEEL_ARCH_CLAIMS` but absent from their own per-format
  dict as an unconditional mismatch (naming the claim and the artifact's
  actual recorded machine/cpu_type) rather than silently returning `[]`. A
  claim outside `WHEEL_ARCH_CLAIMS` entirely (never reachable through
  config validation, but still possible via a hand-built `runtime_floors`
  mapping through the typed API) keeps the prior "nothing to check"
  behavior, unchanged.
- **`CompareRequest.env_matrix_path` now stays inspectable after
  construction, and `.replace(env_matrix_path=...)` on an already-resolved
  request re-resolves instead of raising.** The `env_matrix_path` restoration
  fix above *consumed* the field (reset it to `None`) right after resolving
  it into `env_matrix`, so `request.env_matrix_path` always read `None` even
  when a caller explicitly supplied a path, and
  `request.replace(env_matrix_path=new_path)` on that already-resolved
  request combined the *inherited* resolved `env_matrix` with the *new*
  path and spuriously raised the "not both" usage error — even though
  replacing the path is an unambiguous "re-resolve from here" request, not
  an attempt to supply both a path and a value at once.
  `__post_init__` now tracks, via a private (non-equality, non-repr)
  field, whether the current `env_matrix` was *derived* from a path here
  (and from which one) rather than given explicitly: a derived value may
  always be silently re-resolved against a new `env_matrix_path`, while a
  genuinely explicit `env_matrix` still conflicts with any
  `env_matrix_path`, exactly as before. `dataclasses.replace()`'s
  well-known re-run of `__post_init__` on the new instance is what this
  tracking is designed around.
