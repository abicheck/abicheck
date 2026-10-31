### Added

- **The typed Python API applies `compile.*` overrides.** `config_overrides`
  on `CompareRequest`, `DumpRequest` and `run_dump` now applies
  `compile.compiler`, `compile.frontend`, `compile.frontend_context`,
  `compile.lang`, `compile.nostdinc`, `compile.options`, `compile.std` and
  `compile.sysroot`, which it used to refuse. They resolve through the same
  resolver `compare --set` and `dump --set` use, so a typed override and
  `--set` give the same compiler, options, sysroot, frontend and language. A
  value you set on the request itself still wins over the override.
