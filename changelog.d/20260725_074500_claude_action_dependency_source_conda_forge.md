### Added

- **`action.yml` gains a `dependency-source` input** (`system` | `conda-forge`
  | `none`) alongside the now-deprecated `install-deps` boolean (kept for one
  release cycle as a backward-compatible alias: `true`→`system`,
  `false`→`none`). `conda-forge` installs this repo's pixi-managed `scanner`
  environment (new `[tool.pixi.environments].scanner` in `pyproject.toml`,
  reusing the existing `native-toolchain` feature — castxml 0.7.x + a
  matching gcc/g++, `pixi.lock`-frozen) via a new
  `action/install-deps-conda-forge.sh`, instead of the
  apt/Homebrew-based `install-deps.sh` + checksum-pinned CastXML Superbuild
  path. Linux/macOS only for now (matches `native-toolchain`'s own platform
  list; Windows explicitly errors with a clear message rather than silently
  falling through). The default remains `system` — unchanged behavior for
  every existing caller. New `test-dependency-source-conda-forge` job in
  `.github/workflows/test-action.yml` exercises the new path end-to-end on
  Linux and macOS: builds a real `.so` pair, runs `abicheck compare` through
  the composite action with `dependency-source: conda-forge`, and asserts
  both the expected `BREAKING` verdict and `evidence_tier: header_aware` in
  the JSON report — the latter is what actually proves castxml parsed the
  headers via this path, not just that the pre-existing L0 binary-diff
  caught the change.

### Fixed

- The new conda-forge dependency-install script deliberately symlinks only
  the scanner tools (`castxml`/`gcc`/`g++`/`cc`/`c++`/`gcc-ar`/`gcc-nm`/
  `gcc-ranlib`) into a dedicated shim directory rather than prepending the
  whole pixi environment's `bin/` to `PATH` — the latter would also carry
  its own `python`/`pip` (a transitive dependency of the workspace-level
  `abicheck = {path=".", editable=true}` pypi-dependency) and silently
  shadow whatever `actions/setup-python` configured for the rest of the
  calling workflow's job.
