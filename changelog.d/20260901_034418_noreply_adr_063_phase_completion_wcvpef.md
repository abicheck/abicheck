### Fixed

- **`dump --dry-run`/`compare --dry-run`/`scan --dry-run` now reject a
  root-target scope declared only in `.abicheck.yml`, not just an explicit
  `--build-target`.** A pre-captured Bazel `aquery`/`cquery` `--build-info`
  jsonproto combined with a root-target scope silently ran unscoped
  (collecting every action/target in the captured graph); a prior fix
  raised a clear usage error (exit 64) for an explicit `--build-target`
  flag, but a scope declared only in a discovered `.abicheck.yml`'s
  `build.targets:` still previewed success under `--dry-run` for a request
  the real run then rejected. `dump`/`compare`'s pre-flight check, and
  `scan`'s CLI-reachable pre-flight checks (single-binary and
  `--artifact-set` alike, both real-run and `--dry-run`), now auto-discover
  (or, for `scan`, honor an explicit `--config` override for) that config
  the same way real execution does. A direct `service_scan.run_scan_set(
  ScanRequest(...))` typed-API call with no CLI in front of it is not yet
  covered — it still rejects a configuration-only scope later, during real
  embedding, rather than at this pre-flight (see `docs/contribute/
  known-gaps.md`).
- **`scan --artifact-set` with an unset `--depth` no longer silently
  accepts a root-target scope the real scan would then reject.** Its own
  pre-flight had approximated an unresolved `--depth` rather than
  consulting the same risk-based resolution the real scan uses, so a
  seeded, high-risk change (e.g. a public-header edit) could pass the
  pre-flight and only fail -- with the identical exit 64 -- once
  `run_scan_core`'s own per-member check ran later. The pre-flight now
  resolves the real effective depth/collect mode first (the same
  primitive `--dry-run` cost estimation already uses), so a request that
  would ultimately be rejected is rejected up front, consistently, whether
  or not `--depth` is given.
- **`scan --artifact-set` with a malformed `--risk-rules` file no longer
  crashes with an unhandled traceback.** The new pre-flight resolution
  above ran ahead of this command's existing `try`/`except` translation,
  so a malformed profile's `ValueError` leaked past it (exit 1) instead of
  the established clean usage error (exit 64) every other malformed-input
  path in this command already gives.
