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
  recognize it — fixed to a containment check. The probe's language mode
  is now the header-AST parse's *actual, post-retry* mode rather than a
  static re-derivation: a C-mode dump whose parse self-heals into C++
  (either backend's own C→C++ retry) or a castxml C-mode dump (whose
  driver is internally remapped from the caller's `"c++"` default to
  `"cc"`) now threads that real, resolved language mode/compiler identity
  into `AbiSnapshot.ast_toolchain`, so the provenance probe queries the
  compiler that actually parsed the headers instead of a stale guess.
  And an unpinned C/gnu-dialect parse is no longer probed at all: both
  header-AST command builders unconditionally force `-std=gnu11` for that
  case (`dumper_ast_config.py`), a fact the probe previously ignored —
  querying the resolved compiler's own naked default instead (e.g. GCC's
  C17) and recording a dialect that never actually parsed the headers.
  `dumper_toolchain._resolve_standard_provenance` now reports that forced
  standard directly for a gnu-dialect C parse, and only probes the
  remaining genuinely-unpinned cases (C++ with no C++20 heuristic, or any
  MSVC-dialect parse, neither of which either command builder pins). That
  fix in turn broke the upgrade-corroboration carve-out itself: it only
  recognized an empty-string-to-`"probed:..."` transition, but the forced
  standard produces three shapes it had never seen — an unpinned, no-`--lang`
  baseline moving to the bare `"gnu11"` literal (no `"probed:"` marker at
  all), and an explicit-`--lang` baseline moving from a *bare* `"c"`/`"c++"`
  tag (not an empty string) to `"c:gnu11"`/`"c++:probed:..."`. Generalized
  the carve-out (`_newly_resolved_standard_remainder`) to recognize every
  pre-upgrade "nothing resolved yet" spelling (`""`, `"c"`, `"c++"`) against
  a same-lang-tagged, newly-populated successor (the forced literal or a
  probed value), in either comparison direction. The carve-out's own
  `compiler_family`/`compiler_version` corroboration also now checks
  `AbiSnapshot.ast_toolchain`'s `compiler_sha256` (the resolved compiler
  binary's own content hash) when both sides carry one: a compiler wrapper
  replaced at the same path can report an identical family/version string
  — text a wrapper's own `--version` output controls — while actually
  selecting a different default dialect. Falls back to the family/version-only
  check when either side lacks the hash (an older snapshot, or a side whose
  compiler resolution itself failed).
- Known, narrower residual, documented rather than fixed: a cache/memo
  *hit* for a header that previously self-healed C→C++ still reports the
  pre-retry language mode, since neither backend's AST cache persists that
  fact alongside the cached document — see `_clang_header_dump`'s own
  docstring for why a correct fix needs a cache-format change on both
  backends, not a follow-up to this PR.
