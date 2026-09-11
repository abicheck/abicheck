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
