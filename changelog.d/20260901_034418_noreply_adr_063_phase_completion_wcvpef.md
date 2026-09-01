### Fixed

- **`dump --dry-run`/`compare --dry-run`/`scan --dry-run` now reject a
  root-target scope declared only in `.abicheck.yml`, not just an explicit
  `--build-target`.** A pre-captured Bazel `aquery`/`cquery` `--build-info`
  jsonproto combined with a root-target scope silently ran unscoped
  (collecting every action/target in the captured graph); a prior fix
  raised a clear usage error (exit 64) for an explicit `--build-target`
  flag, but a scope declared only in a discovered `.abicheck.yml`'s
  `build.targets:` still previewed success under `--dry-run` for a request
  the real run then rejected. `dump`/`compare`/`scan`'s pre-flight check
  now auto-discovers (or, for `scan`, honors an explicit `--config`
  override for) that config the same way real execution does, closing the
  gap for every shape of the request — CLI and typed Python API alike.
