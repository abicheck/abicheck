### Fixed

- **P0.3 header→compile-unit context resolution now emits `-std=` for plain
  C standards, not only C++** — `_context_flags()` previously gated
  `-std=` on `"++" in cu.standard`, silently dropping `-std=c11`/
  `-std=gnu11`/etc. for a C compile unit even though the derived context is
  documented to apply generally. Also closes review gaps left open by PR
  #762 merging before this follow-up landed: a stale docstring on
  `derive_l2_compile_context` claiming the *caller* must drain cleanups on
  the `HeaderCompileContextAmbiguousError` path (the function drains them
  itself before re-raising), a duplicated pack-resolution block between
  `derive_l2_include_dirs`/`derive_l2_compile_context` now extracted into a
  shared `_resolve_l2_seed_pack_args()` helper, missing
  `@pytest.mark.integration` markers on the real-compiler P0.3 end-to-end
  tests (the default fast test lane now correctly excludes them), and
  redundant per-header file reads/regex compiles in header→compile-unit
  `#include` matching — `_cu_references_any_header()` replaces the previous
  `_compile_unit_references_header()`, reading each compile unit's source
  once and reusing a `functools.cache`d compiled pattern per header name
  instead of re-reading and re-compiling once per (unit, header) pair.
- **Two more review gaps closed on this same PR, both against the
  `_resolve_l2_seed_pack_args()` extraction and the ambiguity-signature
  masking above.** (1) The shared pack-resolution call (including
  `BuildSourcePack.load()`) had moved ahead of `derive_l2_include_dirs()`'s/
  `derive_l2_compile_context()`'s own `try` block during the extraction, so
  a corrupt/unreadable `--sources`/`--build-info` pack (bad `manifest.json`
  or `build/build_evidence.json`) crashed the whole best-effort L2 seeding
  path with a raw decoding exception instead of degrading to an empty seed.
  Both callers now resolve the pack args inside their own protected
  section again, restoring the pre-refactor best-effort contract. (2) The
  `-target`/`--sysroot`/`-isysroot` ambiguity-signature masking in
  `header_compile_context.py` matched by bare prefix, which also matched
  unrelated, genuinely-independent flags that merely start with the same
  characters — confirmed via a real `clang -cc1 --help`:
  `-target-sdk-version=<value>`, `-target-abi`, `-target-cpu`,
  `-target-feature`, `-target-linker-version`. Two compile units disagreeing
  only on one of those were silently treated as agreeing instead of raising
  `HeaderCompileContextAmbiguousError`. Masking is now restricted to the
  actual structured spellings: exact `-target`/`--target`/`--sysroot`/
  `-isysroot` (separate-operand switches) plus the single-token combined
  forms `--target=...`/`--sysroot=...`/`-std=...`/`/std:...`.
- **A further review finding on the same masking: MSVC `/std:` was
  unconditionally treated as redundant with `CompileUnit.standard`, which
  it never populates.** `-std=` (GCC/Clang) is always parsed into the
  structured `standard` field whenever present, so masking it out of the
  ambiguity signature can never hide a real disagreement — but nothing in
  this codebase parses MSVC's `/std:` into `CompileUnit.standard` at all
  (`adapters/base.py`'s own `_add_generic_flag_option` normalizes it into a
  separate `BuildOption` only when `cu.standard` is empty). Two MSVC
  compile units disagreeing only on `/std:c++17` vs. `/std:c++20`, with
  `standard` empty on both, were therefore silently collapsed to one
  signature — applying the first unit's standard — instead of raising
  `HeaderCompileContextAmbiguousError`. `/std:` is now masked only when
  `CompileUnit.standard` is genuinely populated for that specific compile
  unit (i.e. the structured field actually captured the value, making the
  raw flag truly redundant); when `standard` is empty, `/std:` stays in the
  ambiguity signature so two disagreeing units still raise.
- **A review finding on the *rendering* path, not the ambiguity-signature
  comparison: `_context_flags()` still appended a raw, structured-field-
  covered flag after the correct rendering it duplicates.** The masking
  above (`_is_structured_field_flag()`) was wired only into the
  ambiguity-*comparison* path (`_mask_pinned_abi_flags()`), so two compile
  units spelling an equivalent sysroot as `--sysroot=/…/sdk` and
  `--sysroot=sdk`/`-isysroot sdk` correctly compared as agreeing — but
  `_context_flags()` itself, which renders the literal argv passed to
  castxml/clang, still appended the selected unit's raw, unmodified
  survivor after the already-rendered, absolute structured `--sysroot=`
  token. A real compiler's last-flag-wins semantics then let the trailing,
  uncorrected *relative* raw flag silently override the correct one, so
  the header was parsed against a sysroot relative to abicheck's own
  current directory rather than the compile unit's — potentially failing
  or producing incorrect L2 evidence. `_context_flags()` now reuses the
  identical `_is_structured_field_flag()` predicate to exclude the same
  structured-field-covered raw flags from the rendered tail, so the two
  code paths (comparison vs. rendering) share one source of truth instead
  of two independently-evolving copies.
- **A sixth review round found the matched compile unit's own derived
  `-std=` could be forwarded straight into a header parse whose language
  was explicitly forced to the *other* family, and a real Clang invocation
  rejects that combination outright.** Confirmed end to end: a matched C
  compile unit (`standard="c17"`) with the caller explicitly requesting
  `DumpRequest(lang="c++", lang_explicit=True)` forwarded `-std=c17` into
  the forced-C++ header invocation, and Clang aborted with `invalid
  argument '-std=c17' not allowed with 'C++'` — a supported, explicit
  language override could no longer parse the header.
  `resolve_header_compile_context()`/`derive_l2_compile_context()`/
  `_seeded_compile_context()`/`resolve_side_snapshot()` now thread the
  caller's own requested language (`lang`/`lang_explicit`) down to
  `_context_flags()`, the same additive, default-`None`/`False` pattern
  this codebase already uses for `DumpRequest.lang`/`lang_explicit`
  elsewhere. Two new pure helpers, `_derived_standard_language_family()`
  and `_forced_language_family()`, resolve each side's language family
  (`"c"`/`"c++"`), and `_context_flags()` omits the derived `-std=` token
  only when the two families genuinely disagree — never synthesizing a
  translated equivalent (no `c17` → `c++17` guessing), and never touching
  any other derived field (target triple, sysroot, defines/undefines,
  include paths, other ABI-relevant flags). A non-explicit language
  request (`lang_explicit=False`, including Click's own non-explicit
  `"c++"` default) remains a complete no-op, so every existing caller's
  behavior is unchanged.
- **An eighth review round found the `/std:` masking above still relied on
  `bool(cu.standard)`, which proves only that *some* token populated the
  structured field — not that `/std:` itself did, or that the two agree.**
  `clang-cl` accepts BOTH GCC/Clang's `-std=` and MSVC's `/std:` on one
  command line, and per real `clang-cl` semantics the LATER, MSVC-style
  `/std:` wins (confirmed empirically: `clang-cl -std=c++17 /std:c++20`
  compiles under C++20, `-std=` ignored) — but `build_context.py`'s
  `-std=` capture has no notion of that precedence, so a compile unit like
  `clang-cl -std=c++17 /std:c++20` gets `cu.standard == "c++17"` (from
  `-std=`, not from `/std:`) while the real, honored standard is `c++20`.
  `standard_captured=bool(cu.standard)` therefore masked away the
  disagreeing `/std:c++20` survivor as "redundant," silently parsing under
  the wrong standard. `_is_structured_field_flag()` now takes the actual
  `cu_standard` string and, via a new
  `_msvc_std_flag_matches_captured_standard()` helper, compares each
  `/std:` token's own value (case-normalized) against `cu_standard`
  directly — masking only when they genuinely agree, and retaining `/std:`
  in both the ambiguity signature and the rendered context whenever they
  disagree, matching what a real `clang-cl`/`cl.exe` invocation actually
  honors.
- **A ninth review round found the eighth round's own value-agreement fix
  still went too far for `clang-cl` specifically: agreeing values do not
  make `-std=` and `/std:` interchangeable there.** `clang-cl -std=c++20
  /std:c++20` has matching values on both spellings, but `clang-cl`
  ignores a bare `-std=` entirely (warns "unknown argument ignored") and
  relies on `/std:` alone to set the language dialect — confirmed via
  `clang-cl /?`, which documents `/std:<value>` as "Set language version."
  Dropping `/std:` because it happened to agree with the structurally
  rendered `-std=` therefore still silently changed the dialect L2 replays
  under. `_is_structured_field_flag()` now takes a `msvc: bool` computed
  once per compile unit from its own `argv` via
  `adapters.base._is_msvc_command()` (reusing the existing MSVC/clang-cl
  driver-detection heuristic rather than inventing a new one): for a
  compile unit detected as MSVC/clang-cl-dialect, `/std:` is never masked
  — in either the ambiguity signature or the rendered command — regardless
  of whether its value agrees with `cu.standard`. The eighth round's
  value-comparison fallback (`_msvc_std_flag_matches_captured_standard()`)
  is retained only for the conservative, unlikely case of a `/std:`-shaped
  token surviving on a compile unit `_is_msvc_command()` doesn't recognize
  as MSVC-dialect.
- **A tenth review round found the ambiguity-detection/signature-grouping
  step ran *before* an explicitly forced language was resolved, so an
  explicit `--lang c++` could still raise `HeaderCompileContextAmbiguousError`
  for a language disagreement the caller had already resolved.** Two
  otherwise-identical compile units differing only in `cu.language` (one C,
  one C++, neither carrying an explicit `-std=` for the standard-conflict
  check introduced in the sixth round to compare against) grouped into two
  distinct `_EffectiveContextSignature`s purely on their differing
  `cu.language` field — before `forced_language` was ever computed — and
  raised the ambiguity error even with `lang="c++"`/`lang_explicit=True`
  passed in. `resolve_header_compile_context()` now resolves
  `forced_language` first and narrows the matched-unit set (via a new
  `_cu_language_family()` helper reading `cu.language`, independent of
  whether `cu.standard` happens to be populated) to units whose own
  language family agrees with it, before signature grouping runs — falling
  back to the full, unfiltered matched set whenever no matched unit
  actually has the forced family, so a genuine "the build evidence doesn't
  cover this language" case degrades to the pre-existing behavior rather
  than silently discarding real L3 evidence. A genuine disagreement
  *within* the forced language (e.g. two C++ units still disagreeing on
  `target_triple`) still raises exactly as before, and the companion
  no-forced-language case (the same two-unit C/C++ setup with no explicit
  `lang`) still correctly raises `HeaderCompileContextAmbiguousError`.
