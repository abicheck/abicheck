### Changed

- **`action.yml`'s `dependency-source` now defaults to `conda-forge`**, not
  `system` — the pixi-managed conda-forge path (added earlier as an opt-in)
  is now what every caller who has never touched `dependency-source` or
  `install-deps` gets. An explicit `dependency-source: system` (or the
  deprecated `install-deps: false` → `none`) still works exactly as before;
  only the *unset* case changed. `install-deps: true` (its own schema
  default too, so indistinguishable from omission) now also resolves to
  `conda-forge` rather than `system`, since there is no way to tell an
  explicit `true` apart from the input's own default at the composite-action
  level.

### Added

- **Two pinned-compiler `dependency-source` variants**:
  `conda-forge-gcc14` (gcc/gxx pinned to `14.*`, Linux-only — conda-forge's
  gcc doesn't build for macOS) and `conda-forge-clang20` (clang/clangxx
  pinned to `20.*`, Linux/macOS) — for a caller who wants a specific
  compiler family/major version instead of whatever conda-forge's default
  toolchain generation currently resolves to. Backed by two new pixi
  environments in `pyproject.toml` (`gcc14`, `clang20`, alongside the
  existing `scanner`), each `pixi.lock`-frozen and verified end-to-end
  locally (castxml 0.7.0 + gcc 14.3.0 / clang 20.1.8 confirmed working).
  `action/install-deps-conda-forge.sh` now reads `$ABICHECK_PIXI_ENV` (set
  by `action.yml` from the resolved `dependency-source`) to pick both the
  pixi environment and the right tool list to shim onto `PATH` (gcc-family
  vs. clang-family binaries).
- Expanded `test-dependency-source-conda-forge` in
  `.github/workflows/test-action.yml` into a 5-cell matrix (`conda-forge` on
  Linux+macOS, `conda-forge-gcc14` on Linux, `conda-forge-clang20` on
  Linux+macOS), plus a new `test-dependency-source-none-and-legacy-alias`
  job covering `dependency-source: none` and the deprecated
  `install-deps: false` alias explicitly.
