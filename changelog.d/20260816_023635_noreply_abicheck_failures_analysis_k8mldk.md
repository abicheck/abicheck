### Fixed

- **`abicheck dump` now folds real L3 build context into its own L2 header
  parse, on both the ELF and PE/Mach-O paths.** A `dump --sources ...
  --build-info compile_commands.json` snapshot's `parsed_with_build_context`
  read `false` and `language_standard` read `""` even though real build
  evidence was supplied and embedded in the snapshot — the evidence was
  collected and stored, but never routed to the header-AST invocation,
  because `dump`'s CLI path never called the same L3→L2 fold `compare`'s
  implicit-dump operand already applies via `resolve_side_snapshot`.
  Concretely, a `dump`-produced baseline and a `compare` candidate of the
  *same* project, given the *same* build evidence, resolved to genuinely
  different `CompileContext`s (a `profile_fingerprint` mismatch on
  `include_sequence`/`language_standard`), so `compare` correctly refused
  the comparison as `NOT_COMPARABLE` — not because evidence was missing,
  but because the two commands extracted under non-comparable recipes for
  reasons neither command's own diagnostics named. Fixed with a new,
  shared `buildsource.l2_seed.seed_includes_and_fold_compile_context()`,
  called from both `perform_elf_dump` (ELF) and `handle_non_elf_dump` (PE/Mach-O, which
  shared the identical gap) — folding the real L3 `CompileUnit`-derived
  context (`-std=`, ABI-relevant `-D`/`-U`, target, sysroot) into the same
  explicit context CLI flags/`.abicheck.yml` already resolved, the exact
  fold `resolve_side_snapshot` already applies elsewhere. `dump`'s two
  independent second-pass clang re-parses (the header-graph attach and the
  clang-layout-tool attach) now receive this same fully-merged context too,
  closing a narrower sibling gap where their own re-derivation never looked
  at `gcc_option_tokens`/`sysroot`/`nostdinc`/deferred include roots at all.
  `AbiSnapshot.parsed_with_build_context` is now stamped from either this
  fold or the older `-p`/`--compile-db` mechanism.

- **`scan --against` had the identical L3→L2 fold gap on its own candidate
  side, found only by running the fix above through the real end-to-end
  repro.** `scan_engine._build_new_snapshot` calls `service.resolve_input`
  directly, not `resolve_side_snapshot`, so a `scan` candidate's own header
  parse never received real build context either — a same-project
  `scan --against` a `dump`-produced baseline still returned
  `NOT_COMPARABLE` even after the `dump`-path fix above. Fixed the same
  way: `_build_new_snapshot` now calls the combined seed+fold function
  before `resolve_input` and stamps `parsed_with_build_context` accordingly. A
  real `dump` baseline → `scan --against` repro (a `g++`-compiled library +
  `compile_commands.json`) now produces `NO_CHANGE` end to end.

- **An explicit `-I`/`-isystem` could lose to a derived one in the L3→L2
  fold's include search order (Codex review).** `_merge_l3_compile_context`
  (shared by `dump`/`scan`/`compare`'s fold) put every derived token ahead
  of every explicit one, correct for a macro/std/sysroot switch
  (last-flag-wins) but wrong for an include search path (first-match-wins):
  a derived `-I`/`-isystem` from the build's own `CompileUnit` could
  silently shadow an explicit `--compiler-option -I`/`--gcc-options`
  override for a colliding header basename. Fixed with a new
  `_split_include_tokens()` helper that carves derived's own include-search
  entries out of the leading last-flag-wins group and appends them after
  explicit instead, so an explicit include path always searches first.

- **Two more Codex-review findings on the same fold, both fixed alongside
  it.** A derived `-I`/`-isystem` directory reaches the header parse only
  as an opaque `gcc_option_tokens` string, which the AST cache key's own
  directory-mtime hashing (`extra_includes`/`extra_hash_dirs`) never
  inspected — so editing a header under a derived include dir could reuse
  a stale cached AST on the ELF `dump` path. The combined seed+fold function
  now also returns the derived include directories, threaded into
  `perform_elf_dump`'s existing `extra_hash_dirs`. Separately, `scan`'s own
  fold call hard-coded `lang_explicit=False`, so an explicit `scan --lang
  c` against a matched C++ compile unit's own `-std=c++20` could let that
  derived standard reach a parse being forced into C mode; `lang == "c"`
  (never `scan`'s own default) is now treated as explicit, mirroring
  `perform_elf_dump`'s identical squash-guard rule.

- **A self-deadlock risk in the same L3→L2 fold (Codex review): the
  include-dir seed and the fold each independently collected L3 evidence,
  so a caller needing the zero-config inferred build query (cmake/make/
  bazel, no existing compile database) could contend on its own
  still-held lock.** The inferred query's temp build dir is held under an
  exclusive `flock` until its cleanup runs — deliberately deferred until
  after the header parse consumes the seeded dirs — so a second,
  independent collection immediately after the first would block on that
  same lock for up to 600s before falling back to a throwaway dir. Fixed
  with `buildsource.l2_seed.seed_includes_and_fold_compile_context()`,
  which collects the L3 evidence exactly once and derives both the
  include-dir seed and the compile-context fold from it; all three call
  sites (`perform_elf_dump`, `handle_non_elf_dump`,
  `scan_engine._build_new_snapshot`) now call this combined function
  instead of the two separate ones.

- **Two more Codex-review findings on the combined function above, both
  real and both fixed.** `scan --against` a native library could still
  reach `NOT_COMPARABLE`/produce false ABI differences: the candidate's own
  header parse folded real L3 build context, but `run_scan_core` forwarded
  its original, un-folded `compile_context` to `_run_baseline_compare`, so
  the *baseline*'s native-library header parse never received the same
  fold — `_build_new_snapshot` now also returns the effective
  (fold-applied) compile context, and `run_scan_core` forwards that instead.
  Separately, `perform_elf_dump`'s ADR-039 build-context collector has its
  own explicit rule against unioning the auto-derived, per-header build
  context snapshot-wide — the L3 fold's reassignment of the identically-
  named `gcc_option_tokens` local silently defeated that rule; fixed by
  capturing the user's own tokens before the fold and passing those to the
  collector instead. A third finding, from writing direct unit tests for
  the combined function itself: its own pack-resolution call sat outside
  the try/except that both sibling functions explicitly keep it inside
  (per their own comments) so a corrupt/unreadable build-source pack
  degrades to a no-op instead of raising — fixed to match. The now-unused
  standalone `fold_l3_compile_context()` wrapper (superseded once all three
  call sites moved to the combined function) was removed as dead code.

- **A stale-AST-cache-key gap in `service._attach_header_graph`'s own
  independent second header parse (Codex review).** Its `extra_hash_dirs`
  computation only covered inferred-root deferred directories, never any
  include-search directory riding in the compile context's own
  `gcc_option_tokens` (an explicit `-I`, or — since the L3 fold above — a
  compile-DB-derived one), so editing a header under such a directory
  could silently reuse a stale cached graph even though the primary
  snapshot pass re-parsed correctly. Fixed by extracting the include-dir
  extraction logic into a new shared `header_utils.include_operand_dirs()`
  and folding it into this pass's own cache key too.

- **Documented (not fixed, Codex review): the L2 include-dir seed scans
  every compile unit in the build evidence, not only the one(s) actually
  matched to the headers being parsed, so an unrelated TU's own colliding
  header directory could in principle shadow the matched TU's header in a
  multi-TU build.** Confirmed pre-existing and not introduced by this
  PR's fold — `compare`'s implicit-dump path already combines the
  identical broad-seed-plus-matched-fold shape via
  `service_input_resolution._seeded_includes`/`_seeded_compile_context`,
  unchanged here. A correct fix needs `resolve_header_compile_context`'s
  result to expose which compile units actually matched (today only a
  count), then both this module's seed and `_seeded_includes` to restrict
  to that set — a cross-cutting change to a well-tested module's return
  shape and two independent call sites, left as a known gap (see
  `_existing_include_dirs`'s own docstring and `AGENTS.md`).

- **A `scan --against` regression risk on the side-aware `-H old=PATH`
  baseline path, in the fix that made `run_scan_core` forward the
  candidate's folded compile context to the baseline parse (Codex
  review).** That earlier fix forwarded the fold unconditionally, but the
  fold describes the *new* side's build specifically (its `-D`/`-U`/
  `-std`/include flags come from matching the candidate's own headers
  against the new build) — a side-aware `-H old=PATH` baseline is parsed
  through its own, different old headers, where the new side's derived
  flags can produce a bad parse or a false ABI diff instead of a more
  accurate one. Fixed by forwarding the folded context only when the
  baseline reuses the candidate's own headers (`baseline_headers` not
  given); a side-aware baseline now gets the caller's plain, unfolded
  context instead, since no old-side build evidence exists to derive a
  matching fold for.

- **A regression in the fix directly above, caught by a further Codex
  review round before merge.** `not baseline_headers` was the wrong
  signal — a bare, shared `-H api.h` with no `old=` scoping at all (the
  ordinary, most common `scan --against` usage) already makes
  `baseline_headers` truthy and identical in content to the candidate's
  own `headers`, so the previous fix's guard treated every scan with any
  headers as "old-side-scoped" and silently reintroduced the same
  `NOT_COMPARABLE`/false-ABI-diff bug for the common case. Fixed by
  checking content equality instead of mere truthiness: the fold is used
  whenever the old side's resolved headers are the same as the
  candidate's, and only the plain, unfolded context is used when they
  genuinely diverge (a real `old=` override).

- **Documented (not fixed, Codex review): `ABI_RELEVANT_FLAG_PREFIXES`
  omits GNU/clang `-include`/`-imacros` and MSVC `/FI`/`/FU` (forced
  pre-include), so a matched compile unit's own forced-include header
  never reaches the P0.3 L3->L2 fold's derived compile context.**
  Confirmed genuinely pre-existing (present at this PR's own base
  commit, unrelated to the L3->L2 fold work this PR adds) and shared by
  `compare`'s own already-existing implicit-dump fold, not something
  newly introduced. Not fixed here: unlike every other entry in that
  list, these flags carry a required value that must travel with them, so
  a correct fix needs a new spaced-value-flag extraction branch, not a
  bare addition to the prefix list — left as a known gap (see
  `ABI_RELEVANT_FLAG_PREFIXES`'s own docstring and `AGENTS.md`).

- **A further regression in the header-list-equality fix above, caught by a
  further Codex review round (fresh evidence).** Comparing only the
  resolved *header* lists was not sufficient: `cli_scan.py` builds
  `baseline_include` independently of `baseline_header`, so a shared, bare
  `-H api.h` combined with side-specific `-I old=.../-I new=...` shares one
  header list across both sides while still routing each through a
  genuinely different include tree. The header-only check let the new
  side's folded `-D`/`-std`/sysroot/include context reach a baseline parsed
  through a different include scope, risking a bad parse or a false ABI
  diff on the old binary. Fixed by also requiring the old side's resolved
  include scope to match the candidate's own effective includes before
  reusing the fold.

- **Documented (not fixed, CodeRabbit review): `header_utils.
  _INCLUDE_FLAG_PREFIXES` matching is case-sensitive, so clang-cl's
  documented case-insensitive `/IMsvc`/`/EXTERNAL:I` spellings aren't
  recognized as include-search flags.** Confirmed pre-existing for four of
  its six consumers (present at this PR's own base commit, before any of
  its changes); only this PR's own new `include_operand_dirs` (AST
  cache-key hashing) and `_split_include_tokens` (explicit-vs-derived
  include ordering) inherit the gap for the first time. Not fixed here: a
  correct fix needs case-insensitive matching applied consistently across
  all six consumers plus a new longest-prefix-first tie-break (`/imsvc`
  case-insensitively also matches the shorter `/I`) — fixing only this
  PR's two new consumers would make the seed-suppression logic and the
  cache-hashing/ordering logic silently disagree with each other — left as
  a known gap (see `_INCLUDE_FLAG_PREFIXES`'s own docstring and
  `AGENTS.md`).

- **A stale-AST-cache-key gap in `service._dump_elf`'s own PRIMARY header
  parse (Codex review), the counterpart to the `_attach_header_graph` cache
  fix above.** `_dump_elf`'s own `deferred_dirs` computation hashed only
  `resolve_inferred_header_roots`'s deferred roots, never any include-search
  directory riding in the compile context's own `gcc_option_tokens` (an
  explicit `-I`, or — since the L3 fold — a compile-DB-derived one) —
  exactly the gap the `_attach_header_graph` fix above closed, but left open
  on the *primary* parse whose cache key that fix was supposed to stay
  aligned with. Editing a header under such a directory could let the
  primary parse reuse a stale cached AST while `_attach_header_graph`'s own
  independent second parse correctly reparsed, producing a snapshot whose
  declarations and embedded graph describe different source states. Fixed
  by folding `include_operand_dirs(cc.gcc_option_tokens)` into `_dump_elf`'s
  `deferred_dirs` too, mirroring `_attach_header_graph`'s identical fold.
