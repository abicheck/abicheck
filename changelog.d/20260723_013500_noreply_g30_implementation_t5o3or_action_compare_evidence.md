### Fixed

- **GitHub Action `compare` mode now forwards build/source evidence, and
  fails loud instead of silently dropping an unservable request** — `sources`,
  `build-info`, `compile-db`, `build-config`, and `depth` were already
  documented root-Action inputs but were previously only wired to `dump`/
  `scan` mode in `action/run.sh`, so a `compare`-mode Action run requesting
  `--depth build`/`source` evidence had no way to actually reach the CLI's
  evidence flags. Now forwarded (scoped to the new/candidate side for
  `sources`/`build-info`, matching `compare`'s own `new=`-prefixed syntax).
  For a directory or package operand (the CLI's per-library release
  fan-out), `--sources`/`--build-info`/`--depth` are not supported at all —
  the Action now fails with an explicit error instead of silently running a
  shallower comparison and reporting a clean result while a source-only
  break could have been missed; `--config` is unaffected and always
  forwarded, since the release fan-out does consume it.
- **GitHub Action `compare`/`scan` modes now forward cross-compiler flags**
  — `gcc-path`, `gcc-prefix`, `gcc-options`, and `sysroot` were already
  documented root-Action inputs but were previously only wired to `dump`
  mode in `action/run.sh`; a `compare`/`scan` run against a cross-target
  library silently fell back to the host toolchain/includes for header
  parsing, which can produce false ABI results. Now forwarded in both
  modes.
