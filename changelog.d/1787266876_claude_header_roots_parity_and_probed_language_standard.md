### Fixed

- **`dump`'s own dependency-scoping `header_roots` diverged from
  `compare`'s live-binary dumping, silently breaking `dump`-baseline-vs-live
  comparisons** (abicheck-internal-bugs finding 1): the ELF (`perform_elf_dump`)
  and PE/Mach-O (`handle_non_elf_dump`) `dump` paths computed the
  `header_roots` fed to `dumper_scoping.resolve_dependency_scope` right
  before serialization from `headers` alone (plus, for ELF,
  `--dump-manifest` roots) — omitting `public_headers`/`public_header_dirs`,
  which `dumper_scoping.apply_dependency_scope_to_run_dump_result` (the
  choke point `compare`'s own live-binary dumping, via `service.run_dump`,
  already uses for the identical decision) always folds in. A standalone
  `dump` of a library whose public surface is declared via
  `public_headers`/`public_header_dirs` therefore scoped its dependency
  filtering differently than `compare`'s live dump of the identical input —
  two `dependency_scope: filtered` snapshots whose actual filtered content
  silently disagreed, breaking every later `compare` between a
  `dump`-produced baseline and a live candidate. Both dump paths now compute
  `header_roots` identically to `apply_dependency_scope_to_run_dump_result`.
- **The `profile_fingerprint` comparability guard never fired between two
  header-AST dumps under genuinely different, *unpinned* toolchain defaults**
  (abicheck-internal-bugs finding 2): without an explicit `-std=`/`--lang`
  standard and no `--sources`/`--build-info` (L3) evidence,
  `ast_resolved_standard`/`language_standard` stayed `None`/empty on both
  sides regardless of which compiler or version actually parsed the
  headers — so `compare`ing two dumps of the same unchanged library taken
  under, say, an unpinned GCC 9 default and an unpinned Clang 18 default
  silently produced a real verdict (`COMPATIBLE_WITH_RISK`) instead of
  refusing as `NOT_COMPARABLE`. `dumper_toolchain._probe_default_language_standard`
  now probes the resolved compiler's own predefined-macro table
  (`-E -dM`, mirroring the existing `_clang_compiler_family` probe) for its
  genuinely unpinned default `__cplusplus`/`__STDC_VERSION__` whenever no
  explicit standard was given, so two dumps under differing toolchain
  defaults now correctly disagree on `language_standard` and the
  comparability gate refuses the comparison. Best-effort and additive: the
  common same-toolchain, no-flags workflow is unaffected, and a probe
  failure (unsupported driver, e.g. MSVC `cl.exe`) degrades to the prior
  behavior.
