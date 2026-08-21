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
  behavior. The probe resolves the *actual* language mode the header-AST
  parse used (`dumper_toolchain._resolve_force_cpp`, the same auto-detection
  decision the real parse makes — not just a bare `--lang c`/`c++` check),
  so a header that auto-detects as C is probed in C mode even when `lang`
  wasn't explicitly given. A new `comparability.py` carve-out
  (`_language_standard_probe_upgrade_corroborated`) keeps an existing,
  pre-upgrade baseline (recorded with an empty `language_standard`, since
  this probe didn't exist yet) comparable against a freshly re-dumped
  snapshot of the identical input under the identical resolved compiler
  (confirmed via an unchanged `compiler_family`/`compiler_version`) — an
  abicheck upgrade adding evidence must not by itself make an
  otherwise-unchanged baseline `NOT_COMPARABLE`. Two follow-up review
  fixes, both real: the carve-out's transition check and
  `_cplusplus_macro_for_standard` both matched the `"probed:"` marker with
  `.startswith(...)`, but when `--lang` is given explicitly the recorded
  value is language-mode-prefixed (`"c++:probed:__cplusplus=201703L"`),
  so the marker no longer sits at position 0 and both silently failed to
  recognize it — fixed to a containment check. And the probe's language
  mode is now the header-AST parse's *actual, post-retry* mode rather than
  a static re-derivation: a C-mode dump whose parse self-heals into C++
  (`dumper.py`'s C→C++ retry) or a castxml C-mode dump (whose driver is
  internally remapped from the caller's `"c++"` default to `"cc"`) now
  threads that real, resolved language mode/compiler identity into
  `AbiSnapshot.ast_toolchain`, so the provenance probe queries the
  compiler that actually parsed the headers instead of a stale guess.
