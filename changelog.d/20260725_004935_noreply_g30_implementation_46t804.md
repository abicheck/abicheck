### Added

- **`publish-baseline.yml` / `update-main-baseline.yml` reusable workflows**
  (ADR-047 §6/§10, G30 P1.6) — publish a `release-contract` baseline-set as
  an atomic GitHub Release asset per contract profile, and refresh the
  `accepted-main` channel in Actions cache on every default-branch push,
  closing the last unbuilt primitive `resolve-baseline`'s bundle-scoped
  resolution depended on. `actions/baseline` gains a `stage_binary` field on
  each `libraries[]` entry: when set, the library's real ELF binary is
  copied into `<output-dir>/binaries/<name>` and recorded in `manifest.json`
  (`binary`/`binary_sha256`), since `abicheck/bundle.py`'s
  `build_bundle_snapshot()` skips non-ELF (including JSON snapshot) inputs
  and would otherwise silently produce no old-side data for a release
  bundle. Both workflows derive `actions/baseline`'s `libraries` input
  straight from a contract profile's `build-output.json` via a new `abicheck
  build-output baseline-libraries DIRECTORY` command
  (`abicheck.buildsource.baseline_publish.derive_baseline_libraries`) — no
  separate `.abicheck.yml` read, and `stage_binary` is set automatically for
  any target that is a release-bundle member (`target.bundle`).
  `update-main-baseline.yml`'s cache key rotates on every run
  (`<key-prefix>-<profile-id>-<head-sha>`, restorable via a
  `<key-prefix>-<profile-id>-` prefix) so a refresh can never silently stop
  updating after the first write, the failure mode ADR-047 §10 calls out by
  name. See `docs/reference/publish-baseline.md`.

