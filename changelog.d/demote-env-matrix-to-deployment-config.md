### Removed

- `compare --env-matrix FILE` is removed (ADR-068 D5). Declared deployment
  constraints (target OS/arch, toolchain, SYCL/CUDA backends, symbol-version
  runtime floors) are a stable project property, not a per-run flag pointing
  at a side file to keep in sync — put the same YAML shape inline in
  `.abicheck.yml`'s new `deployment:` key instead:

  ```yaml
  deployment:
    runtime_floors:
      GLIBC: "2.28"
      GLIBCXX: "3.4.28"
  ```

  The old spelling is a hard usage error (`No such option`, exit 64) — there
  is no hidden alias or deprecation window. Unlike the old flag, which was
  rejected outright for a directory/package (release) `compare`,
  `deployment:` is a project-wide setting and now applies to every library
  in the release fan-out, the same way `gate.fail_on_removed_library`/
  `release.*` already do.

### Added

- `.abicheck.yml` gains a `deployment:` top-level key
  (`abicheck.buildsource.build_config.BuildConfig.deployment`), embedding
  `EnvironmentMatrix`'s existing YAML shape inline (parsed via
  `EnvironmentMatrix.from_dict`, the same parser `--env-matrix FILE` used to
  go through) rather than a side file. `compare`'s typed Python API
  (`CompareRequest.env_matrix`/`service.run_compare(env_matrix=...)`) now
  carries an already-resolved `EnvironmentMatrix` directly instead of a
  `env_matrix_path` to load.

### Fixed

- `EnvironmentMatrix`/`SyclConstraints`/`CudaConstraints` are now genuinely
  `@dataclass(frozen=True)` (ordinary attribute reassignment previously
  still worked despite the class being hashable, which could change an
  already-inserted dict/set member's hash out from under it), and
  `copy.deepcopy(matrix)`/`pickle.dumps(matrix)`/`dataclasses.asdict(matrix)`
  now round-trip correctly instead of raising `TypeError: cannot pickle
  'mappingproxy' object` — a Python stdlib gap (`types.MappingProxyType` has
  no registered pickle reducer at all) that surfaced through any
  `CompareRequest` embedding a real declared `deployment:` contract.
- The declared-deployment-floor digest (`env_matrix_source_sha256`) is now
  projected by every `compare` output format — Markdown, the `review`
  digest, SARIF, HTML, and JUnit — not only JSON, matching the digest's own
  documented "present whenever a `deployment:` contract was resolved"
  contract.
- A BundleFacts comparison (`compare_release_against_bundle_facts`/
  `compare_stored_bundle_facts_pair`) now carries the same
  `env_matrix_source_sha256` digest on `BundleDiffResult` and its JSON/
  Markdown renderers, computed once at bundle scope from the resolved
  matrix — previously absent whenever OLD and NEW shared zero matched
  library pairs, making a genuinely-configured `deployment:` contract
  indistinguishable from none.
