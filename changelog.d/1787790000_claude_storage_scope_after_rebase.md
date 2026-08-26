### Fixed

- **`mypy abicheck/` is clean again.** ADR-061 Phase 4 moved `scan_config`
  out of the flat root into `abicheck/workflows/`, but its stubless-PyYAML
  override in `pyproject.toml` did not follow it — so `mypy abicheck/`
  reported one `import-untyped` error on a clean `main` in any environment
  where the `types-PyYAML` stubs are not visible. Added the new module path
  to the existing override list.
