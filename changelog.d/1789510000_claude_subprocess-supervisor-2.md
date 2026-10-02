### Changed

- **Remaining child processes route through the subprocess supervisor.** The `c++filt` demangler fallback, the source-smoke compile/run checks, the probe harness compile, the clang layout tool, and `.deb` extraction's `ar x` now run through `abicheck.deadline.run_bounded`, so they honour an active `--timeout` budget and leave no grandchildren behind. The supervisor gate now covers all of `abicheck/`, with reviewed per-file exceptions (design-hardening plan, Phase 6).
