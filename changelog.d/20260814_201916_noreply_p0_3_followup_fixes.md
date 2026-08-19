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
- **An eleventh review round found three more gaps in the same
  ambiguity-signature/rendering machinery.** (1) The raw-survivor
  comprehension in `_EffectiveContextSignature.of()` masked only the
  structured target/sysroot/standard fields, never consulting the
  caller's own explicit `pin` — so an explicitly-pinned macro spelled as a
  raw `-D<macro>[=value]`/`/D<macro>` survivor in
  `cu.abi_relevant_flags` (captured a second time alongside the
  structured `cu.defines` dict entry the pin already excused) still made
  two otherwise-agreeing compile units read as ambiguous.
  `_mask_pinned_abi_flags()` now also drops a raw define survivor whose
  macro name is in `pin.defines`, via a new `_pinned_define_macro()`
  helper shared with `_ExplicitPin.of`'s own `-D`/`/D` recognition. (2)
  `extract_abi_relevant_flags()` normalized a genuinely split two-token
  flag like `-target-abi aapcs` by capturing only the bare switch (it
  shares the `-target` prefix already matched by
  `ABI_RELEVANT_FLAG_PREFIXES`), silently dropping the operand — so two
  units disagreeing purely on the ABI value read as identical, and any
  caller replaying the bare survivor got a syntactically incomplete
  command. Confirmed via a real `clang -cc1 --help` that
  `-target-abi`/`-target-cpu`/`-target-feature`/`-target-linker-version`
  are all genuine two-token forms; each is now normalized into one
  internal `<flag>=<value>` token (`_SPLIT_OPERAND_ABI_FLAGS`), and
  `header_compile_context._split_operand_survivor()` reconstructs it back
  into the two literal argv tokens a real compiler invocation needs by
  the time it reaches the rendered `CompileContext`. Deliberately not
  extended to the bare `-target`/`--sysroot`/`-isysroot` split forms,
  which already have their own dedicated structured fields. (3) A
  resolved `CompileContext` never carried which compiler understands its
  own option tokens — for an MSVC/clang-cl compile unit, the retained
  `/std:` survivor (from the ninth round's fix) was silently handed to
  whatever default compiler the caller's L2 backend resolves to, and a
  real `clang++` reads `/std:c++20` as a missing source file, not a
  language flag (confirmed empirically), turning an otherwise-working
  header parse into a hard failure. `header_compile_context.
  _derived_gcc_path()` now returns the matched compile unit's own
  `argv[0]` when it is genuinely MSVC/clang-cl-dialect
  (`adapters.base._is_msvc_command()`), `None` otherwise (a complete
  no-op for the non-MSVC case), and `service_input_resolution.
  _merge_l3_compile_context()` folds it in via the same "derived leads,
  explicit wins" precedence already used for `sysroot`/`gcc_options` —
  extended to two new fields, `gcc_path`/`gcc_prefix` — so a caller's own
  explicit `--gcc-path` is never overridden.
- **A twelfth review round found three more gaps in the same
  `-Xclang`/compiler-selector/launcher-wrapper areas, all in this PR's own
  latest commit.** (1) `adapters.base.extract_abi_relevant_flags()`'s
  split-operand normalization (`-target-abi aapcs` → one internal
  `-target-abi=aapcs` token) handled only the *bare* two-token spelling —
  but a real Clang driver invocation never passes a cc1-only flag like
  `-target-abi` bare at all; each token is individually wrapped in its own
  `-Xclang` (`-Xclang -target-abi -Xclang aapcs`), confirmed against a real
  `clang -cc1 --help` (documents `-target-abi <value>`) versus the plain
  driver rejecting the bare form outright ("unknown argument '-target-abi';
  did you mean '-Xclang -target-abi'"). The bare-form branch, reached one
  token early on `-target-abi`, silently consumed the *second* `-Xclang` as
  the value, producing the corrupted `-target-abi=-Xclang` and dropping the
  real value (`aapcs`) one token later. Fixed by recognizing the
  `-Xclang <flag> -Xclang <value>` wrapped shape explicitly (checked before
  the bare branch, for all four flags in `_SPLIT_OPERAND_ABI_FLAGS`, since
  each is equally cc1-only) and normalizing it into a distinct internal
  `-Xclang <flag>=<value>` encoding;
  `header_compile_context._split_operand_survivor()` reconstructs it back
  into the full, real four-token `["-Xclang", "<flag>", "-Xclang",
  "<value>"]` form at replay time, never the bare unwrapped form a normal
  `clang` driver invocation rejects. (2) `service_input_resolution.
  _merge_l3_compile_context()` treated `gcc_path`/`gcc_prefix` as two
  independent "derived fills an unset explicit field" slots, but
  `dumper_clang._resolve_clang_bin()` always checks `gcc_path` before
  `gcc_prefix` — so a caller who explicitly set *only* `gcc_prefix`
  ("use this prefix, no path override") could still have a *different*
  derived `gcc_path` merged into the unset slot and silently win over the
  caller's actual intent, since `gcc_path` is checked first. Fixed by
  resolving the two fields together as one logical compiler-selector: if
  the caller explicitly set *either* one, neither is inherited from
  `derived`; only when the caller set *neither* is `derived`'s own
  `(gcc_path, gcc_prefix)` pair adopted together. (3)
  `header_compile_context._derived_gcc_path()` returned `cu.argv[0]`
  unconditionally for an MSVC-dialect compile unit, assuming the compiler
  is always the first token — but a compiler-cache/launcher wrapper
  (`sccache`, `ccache`, `distcc`, ...) commonly precedes the real driver
  (`sccache clang-cl /std:c++20 ...`), and `argv[0]` in that case is the
  launcher, not a clang-family binary, so `_resolve_clang_bin()` rejected it
  and silently fell back to plain `clang++`, which cannot parse the
  retained `/std:` survivor at all. `adapters.base._is_msvc_command()` was
  refactored (additively — a new `_msvc_driver_scan()` shared internal, a
  new public `msvc_driver_token()`, `_is_msvc_command()`'s own signature and
  behavior unchanged) to also report which literal argv token it matched as
  the driver, and `_derived_gcc_path()` now uses that token — falling back
  to `cu.argv[0]` (the pre-fix behavior) only for the narrower case where
  MSVC dialect was detected some other way (a bare `/c` marker or an
  explicit `--driver-mode=cl` naming no `cl`/`clang-cl`-basename token) and
  no more specific token exists to prefer.
- **A thirteenth review round found three more gaps in the same
  launcher/driver-selector machinery, all fresh evidence beyond the twelfth
  round above.** (1) `adapters.base._msvc_driver_scan()`'s driver-token
  match was exact-name-only against `_MSVC_DRIVERS`
  (`cl`/`cl.exe`/`clang-cl`/`clang-cl.exe`), so a launcher-wrapped
  *supported* CL-style alias — a versioned `sccache clang-cl-20 /c ...`
  (LLVM/Debian packaging commonly ships a versioned executable) or Intel's
  `sccache dpcpp-cl /c ...` — matched neither that set nor
  `dumper_clang._is_cl_style_driver_name()`'s own already-broader
  recognition (which strips a trailing version suffix and accepts any
  `-cl`-suffixed stem), so the scan returned no driver token at all and
  `_derived_gcc_path()` fell back to the launcher (`sccache`), which is
  rejected as not clang-family — the exact failure this whole scan exists
  to prevent, just for a versioned/aliased driver name instead of a bare
  one. `_msvc_driver_scan()` now also recognizes any token
  `_is_cl_style_driver_name()` accepts, reusing that existing recognizer
  rather than growing a second, drifting name list. (2)
  `header_compile_context._derived_gcc_path()` returned a resolved driver
  token verbatim even when it was a *relative* path (`../llvm/bin/clang-cl`)
  or a home-redacted one (`~/llvm/bin/clang-cl`, ADR-032 D7) — the caller
  (`dumper_clang._resolve_clang_bin`'s `shutil.which`/subprocess execution)
  resolves such a token from abicheck's own current working directory, not
  the compile unit's own `directory`, so a genuinely executable compiler
  from the real build was reported missing. A new `_resolve_driver_token()`
  helper (mirroring the existing `_resolve_cu_relative_path()`'s treatment
  of every other redacted/relative `CompileUnit` path field) expands `~`
  and, for any token containing a path separator, resolves it relative to
  `cu.directory` when not already absolute — a bare PATH name (no
  separator) is left unchanged, since it's looked up on `PATH`, not
  resolved against any directory. (3) `_EffectiveContextSignature` never
  compared the derived compiler driver at all, so two otherwise-identical
  matched compile units resolving to *different* clang-cl/MSVC drivers
  (e.g. because compile-database iteration order changed which unit is
  "first") silently grouped into one signature and applied the first
  unit's driver — even though the compiler itself supplies ABI-relevant
  built-in macros, default include paths, and target defaults, so this
  could silently change the generated L2 snapshot without ever raising
  `HeaderCompileContextAmbiguousError`. `_EffectiveContextSignature` now
  carries a `gcc_path` field (`_derived_gcc_path()`'s own resolved value),
  masked to a shared placeholder — mirroring `standard`/`target_triple`/
  `sysroot` above — only when the caller's own explicit `CompileContext`
  already pins a `--gcc-path` (`_ExplicitPin.gcc_path`, new), since the
  caller's own value wins that dimension regardless of what the matched
  units resolve to.
- **A fourteenth review round found four more gaps across the same
  `_ExplicitPin`/ambiguity-signature/build-options machinery.** (1)
  `header_compile_context._ExplicitPin.of()`'s macro-pin scan recognized
  only GCC/Clang's `-D`/`-U` spelling, even though the raw-flag masking it
  feeds (`_mask_pinned_abi_flags()`, via `_pinned_define_macro()`) already
  recognized MSVC/clang-cl's `/D`/`/U` spelling too — so a clang-cl caller
  pinning an ABI macro via `/D_GLIBCXX_USE_CXX11_ABI=1` left `pin.defines`
  empty, and two matched units disagreeing only on that macro's value
  stayed spuriously ambiguous despite the documented override. `clang-cl
  /?` documents `/D <macro[=value]>` as the supported define form; `_
  ExplicitPin.of()` now recognizes both `-D`/`-U` and `/D`/`/U` uniformly,
  mirroring `_pinned_define_macro()`'s own recognition. (2)
  `adapters.base.derive_build_options()`'s broad `-target`-prefix filter
  (meant to drop a raw `-target`/`--sysroot`/`-isysroot` survivor already
  represented by the structured `target_triple`/`sysroot` fields) also
  silently dropped a `-target-abi`/`-target-cpu`/`-target-feature`/
  `-target-linker-version` survivor — none of those four is represented by
  any structured `CompileUnit` field at all (they carry real, independent
  ABI-relevant information), so a compile unit's own resolved value for one
  of them produced no `BuildOption` and no build-evidence drift finding,
  in both the bare-token normalized form (`-target-abi=aapcs`) and the
  `-Xclang`-wrapped normalized form. `_add_generic_flag_option()` now
  exempts these four (via a new `_is_split_operand_abi_flag_survivor()`
  predicate) from that broad filter, letting them fall through to the
  generic option path instead of being dropped. (3)
  `adapters.base._msvc_driver_scan()`'s CL-style-driver-name match
  (`_is_cl_style_driver_name()`, a bare, extension-agnostic name-suffix
  check) was applied to *every* argv token, not just the command's own
  executable/launcher position(s) — so a GNU translation unit whose source
  basename happens to end in `-cl` (e.g. `foo-cl.cpp`) was mistaken for a
  CL-style driver, flipping the whole command to MSVC dialect and, via
  `_derived_gcc_path()`, recording the source path itself as `gcc_path`;
  multiple otherwise-identical units could then spuriously fail ambiguity
  grouping, while a single unit lost its recorded compiler entirely. The
  scan is now restricted to the command's actual leading executable
  token(s) — `argv[0]`, plus `argv[1]` too when `argv[0]`'s basename is a
  recognized launcher (`sccache`/`ccache`/`distcc`) — via a new
  `_executable_token_positions()` helper. (4)
  `header_compile_context._ExplicitPin.gcc_path` checked only `explicit.
  gcc_path is not None`, even though `service_input_resolution.
  _merge_l3_compile_context()` (per the twelfth round above) already
  treats `gcc_path`/`gcc_prefix` as one mutually exclusive compiler
  selector — so a caller supplying only `gcc_prefix` against matched units
  naming different clang-cl drivers still raised
  `HeaderCompileContextAmbiguousError` before the merge step ever got a
  chance to apply the caller's already-resolved explicit selection.
  `_ExplicitPin.gcc_path` is now `True` when *either* `explicit.gcc_path`
  or `explicit.gcc_prefix` is set.
- **A fifteenth review round found two more gaps, both in the same
  launcher-recognition and split-operand-ABI-flag machinery.** (1)
  `adapters.base._executable_token_positions()`'s launcher recognition (the
  fourteenth round's own fix, above) hard-coded a 3-name list
  (`sccache`/`ccache`/`distcc`) and a fixed `{0, 1}` position pair — already
  narrower than `source_extractors._argv.COMPILER_LAUNCHERS`'s six
  recognized launchers (also `icecc`/`icerun`/`buildcache`), and unable to
  locate the driver behind a chained launcher or one carrying a ccache-style
  `KEY=VALUE` config-override token at all. A real
  `buildcache clang-cl /std:c++20 /c x.cc` command was therefore not
  recognized as MSVC-dialect: `msvc_driver_token()` returned `None`,
  `_derived_gcc_path()` fell back to `buildcache`, and the downstream
  resolver silently fell back to plain `clang++`, which cannot consume the
  retained `/std:c++20` survivor. `_executable_token_positions()` now
  reuses `source_extractors._argv.strip_launchers()` (the same, already
  more complete launcher parser `adapters.base` already imports) to compute
  the real driver's index directly, instead of an independent, already-
  drifted name/position guess. (2) The internal `-target-abi=aapcs`
  (bare) / `-Xclang -target-abi=aapcs` (`-Xclang`-wrapped) encoding the
  twelfth round introduced was decoded back into real argv tokens only on
  the L2 header-compile-context replay path
  (`header_compile_context._split_operand_survivor()`). The identical
  encoding also flows through L4 source replay
  (`source_extractors._argv.replay_extra_flags()`/
  `_carry_abi_relevant_flags()`), which read it unchanged: the bare
  spelling was silently dropped outright by
  `STRUCTURED_TOOLCHAIN_FLAG_PREFIXES` (it shares the `-target` prefix that
  filter drops as redundant for the *unrelated*, already-structured
  `target_triple`/`sysroot` survivors, but none of these four flags has a
  structured field of its own), and the `-Xclang`-wrapped spelling reached
  Clang as one malformed argv token instead of the required four real
  tokens. The decode (`split_operand_survivor()`, plus the
  `is_split_operand_abi_flag_survivor()` predicate) is now a single shared
  implementation living in `source_extractors._argv` — the leaf,
  tool-independent module both `adapters.base` (which *produces* the
  encoding) and the L4 replay path already depend on, keeping the module
  dependency a one-way DAG rather than introducing a real import cycle;
  `adapters.base`/`header_compile_context` re-export it under their
  existing names for backward compatibility. Both replay paths now
  reconstruct the identical, real argv token(s) from one implementation
  instead of drifting independently.
- **A sixteenth review round found three more gaps in the same CL-driver-
  mode, driver-path-grouping, and split-operand-decode machinery.** (1) A
  compile unit can select MSVC/CL dialect via an explicit
  `--driver-mode=cl` on a *generically-named* driver (`clang
  --driver-mode=cl /std:c++20 /c t.cpp`), not only via a CL-style binary
  name (`clang-cl`) — `_derived_gcc_path()` then has no CL-style basename
  to record (`msvc_driver_token()` falls back to the bare `argv[0]`,
  `"clang"`), and neither header command builder infers CL mode from a
  plain `clang` binary name the way it does from `clang-cl`'s own
  self-selecting basename, so the reconstructed command invoked GNU-mode
  clang against the retained `/std:c++20` survivor — confirmed empirically:
  the original CL-mode command succeeds, the reconstructed GNU-mode one
  fails, treating `/std:c++20` as a missing input file. `_context_flags()`
  now unconditionally emits `--driver-mode=cl` among the rendered tokens
  whenever the compile unit is MSVC/clang-cl-dialect
  (`adapters.base._is_msvc_command()`), mirroring the identical,
  already-established precedent in L4 replay's own command builder
  (`source_extractors.clang._clang_context_args`: `if msvc:
  cmd.append("--driver-mode=cl")`) — harmless when the driver's own
  basename already implies CL mode, load-bearing when it doesn't. (2)
  `header_compile_context._resolve_driver_token()` (from the thirteenth
  round above) joined a relative driver token onto the compile unit's own
  `directory` but never normalized the result — so two matched units in
  different build subdirectories spelling the *same* executable through a
  relative path containing `..` (`build/a/../../tool/clang-cl` and
  `build/b/../../tool/clang-cl`, both naming `tool/clang-cl` once `..`
  segments collapse) produced two textually-different joined strings,
  which `_EffectiveContextSignature`'s plain string comparison on
  `gcc_path` read as a genuine disagreement, raising a spurious
  `HeaderCompileContextAmbiguousError` for units that in fact agree on
  every ABI-relevant dimension. `_resolve_driver_token()` now normalizes
  the joined path with `os.path.normpath()` — a purely lexical
  normalization, not `Path.resolve()`, matching this module's own existing
  precedent of never touching the filesystem for a `CompileUnit` path
  field that may be redacted/relative and not guaranteed to exist locally
  (a persisted build pack collected on a different machine). (3)
  `source_extractors._argv.split_operand_survivor()` reconstructed two
  different output shapes depending on which of the two internal
  encodings it decoded: the bare `<flag>=<value>` form (what a direct
  `clang -cc1 -target-abi aapcs` capture produces, since `-cc1` mode
  accepts these flags bare) reconstructed into the bare two-token form
  `["-target-abi", "aapcs"]`, while the `-Xclang`-wrapped form
  reconstructed into the full four-token `-Xclang`-wrapped form. But every
  consumer of this function replays through an **ordinary Clang driver,
  never `-cc1` directly** — confirmed empirically: installed `clang -cc1
  --help` documents bare `-target-abi <value>`, while `clang -target-abi
  aapcs` (the ordinary driver) rejects it outright ("unknown argument
  '-target-abi'; did you mean '-Xclang -target-abi'"), requiring the
  `-Xclang`-wrapped four-token form regardless of the original capture
  shape. Both branches now converge on the identical `-Xclang`-wrapped
  reconstruction, computed from whichever internal encoding matched (the
  `` `-Xclang ` `` marker is stripped first, if present, before parsing).
- **A seventeenth review round found the sixteenth round's own
  `split_operand_survivor()` unification fixed *decode* but not *encode*.**
  `adapters.base.extract_abi_relevant_flags()` — the function that
  *produces* the internal `abi_relevant_flags` survivor in the first
  place, before it ever reaches `split_operand_survivor()` — still encoded
  a bare-captured `-target-abi aapcs` (`"-target-abi=aapcs"`) and its
  `-Xclang`-wrapped-captured equivalent (`"-Xclang -target-abi=aapcs"`) as
  two visually different strings for the same semantic value. That
  mattered because two consumers compare/key on this raw encoded string
  *directly*, never through `split_operand_survivor()`:
  `header_compile_context._EffectiveContextSignature` (ambiguity grouping)
  and `derive_build_options()` (build-option drift) — so a compile unit
  that captured `-target-abi aapcs` via a bare `-cc1` invocation and a
  semantically identical unit that captured the same value via an
  ordinary driver's `-Xclang`-wrapped spelling could spuriously raise
  `HeaderCompileContextAmbiguousError`, or report as build-option drift,
  even though they mean exactly the same thing. Fixed by having
  `extract_abi_relevant_flags()`'s `-Xclang`-wrapped branch encode to the
  same canonical `<flag>=<value>` token as the bare branch, with no
  `-Xclang` marker at all — `split_operand_survivor()` already reconstructs
  the identical `-Xclang`-wrapped replay form from either encoding, so
  nothing downstream of decode needed the two capture forms distinguished
  at the encoding level, and the marker's only remaining purpose is
  backward-compatible decoding of an evidence pack persisted by an earlier
  revision. A genuine value disagreement (`aapcs` vs. `aapcs16`) still
  produces two distinct encodings either way, since the value stays part
  of the canonical token regardless of which capture form was seen.
- **An eighteenth review round found the launcher-recognition machinery
  never unwrapped a leading POSIX `env` invocation.** A compile unit
  recorded as `env SDKROOT=... /opt/llvm/bin/clang-cl /c ...` — an
  environment-scoped invocation or wrapper script, POSIX `env` syntax being
  `env [-i] [-u NAME]... [NAME=VALUE]... command [args]` — computed driver
  index 0 (`env` itself): `source_extractors._argv.strip_launchers()`
  recognized only the six compiler-cache/distribution launcher names, so
  `adapters.base._msvc_driver_scan()` found no `cl`/`clang-cl`-basename
  token at any recognized executable position, `msvc_driver_token()`
  returned `None`, `header_compile_context._derived_gcc_path()` fell back to
  `argv[0]` (`"env"`), and `dumper_clang._resolve_clang_bin()` rejected that
  name and silently substituted plain `clang++` — losing the recorded
  toolchain's built-ins, default headers, and target defaults.
  `strip_launchers()` now unwraps a leading `env` invocation (bare `env` or
  a path ending in `/env`, its own no-operand/operand-taking flags, and any
  `NAME=VALUE` assignments — reusing the existing ccache-style config-
  override regex, since it's the identical shape) via a new
  `_skip_env_prefix()` helper, looped together with the existing
  launcher-chain stripping so `env` and a compiler-cache launcher can
  precede one another in either order (`env FOO=1 sccache clang-cl ...`).
  Since `strip_launchers()` is the shared primitive behind
  `_executable_token_positions()`/`_msvc_driver_scan()`,
  `pick_compiler_binary()`, `include_graph.py`, `build_context.py`, and
  `cc_wrapper.py`, every caller benefits from this fix, not just the L2
  clang-cl driver-selection path the finding was reported against.
- **A nineteenth review round (two findings) found round 18's `env`-prefix
  unwrapping recognized and discarded `env -C DIR`/`env PATH=...` as purely
  cosmetic, when both change how the *driver token* it locates must be
  interpreted.** (1) `env -C build ../llvm/bin/clang-cl ...` changes the
  effective working directory the driver runs from — GNU `env --help`
  documents `-C`/`--chdir=DIR` as "change working directory to DIR before
  running the command" — so a relative driver token following it is only
  meaningful relative to `<cu.directory>/DIR`, not bare `cu.directory`.
  `header_compile_context._derived_gcc_path()` previously resolved such a
  token straight against `cu.directory`, reporting a genuinely executable
  compiler as missing (or silently resolving to a different file that
  happens to exist one directory up). (2) `env PATH=/opt/llvm/bin
  clang-cl ...` scopes a `PATH` override to the launched command only, so a
  bare driver name resolvable exclusively through that overridden `PATH`
  (not abicheck's own inherited one) previously reached
  `dumper_clang._resolve_clang_bin`/replay subprocess spawning unresolved,
  reporting the recorded compiler as missing or silently substituting a
  different one found on the inherited `PATH` instead.
  `source_extractors._argv._skip_env_prefix()` now captures both values
  (the most recent `-C`/`--chdir[=DIR]` and `PATH=...` seen, closest to the
  driver) instead of discarding them, and a new `_apply_env_context()`
  helper folds their effect directly into the driver token
  `strip_launchers()` returns: a relative, path-shaped token is joined onto
  the chdir directory and lexically normalized (`os.path.normpath` —
  matching `_resolve_driver_token()`'s own existing lexical, symlink-blind
  convention, so the result composes correctly through that function's
  later join against `cu.directory` with no further changes needed there),
  and a bare token is resolved to an absolute path via
  `shutil.which(token, path=env_path)` when found, left unchanged
  otherwise. Because `strip_launchers()` is the shared primitive behind
  every caller listed above, folding the correction into its own return
  value (rather than threading two new fields through each call site)
  means every existing caller gets the corrected token for free, with no
  signature change. One additional fix was needed for the correction to
  actually reach `header_compile_context._derived_gcc_path()`:
  `adapters.base._msvc_driver_scan()` was reading the raw, unfolded
  `argv[driver_index]` directly instead of `strip_launchers(argv)`'s own
  (now-corrected) first element — silently discarding both corrections for
  `msvc_driver_token()`'s one real caller — so it now reads the resolved
  token from `strip_launchers()`'s return value at the stripped driver
  position instead.
- **Investigated a real, still-unreproduced CI discrepancy reported against
  this same PR (round 19), confirmed NOT pre-existing on `main`.** Three
  canonical-lane CI checks on this branch's own commits (`ai-readiness`'s
  `mypy-baseline` reporting 4 phantom errors with no printed diagnostic
  lines; `lint-and-types`'s `ruff check`/`mypy` both failing with zero
  output between their own start/result markers; 19-25 `NameError: name
  'os' is not defined` failures in `tests/test_header_compile_context.py`/
  `tests/test_header_compile_context_gcc_path.py` only under the exact CI
  `-n auto --dist worksteal` flags) do not reproduce against the identical
  commits' most recent `main` CI run (which is green apart from one
  already-known, unrelated Windows flake), ruling out CI-wide infra noise
  or a pre-existing repo issue as the explanation. Despite that, this pass
  could not reproduce any of the three locally either — including via a
  from-scratch `git clone` into a brand-new virtualenv with the exact CI-
  pinned tool versions (`mypy==1.19.1`, `ruff==0.16.3`) and a
  closely-matching Python patch (3.13.12 local vs. CI's 3.13.15), run with
  `-n 4 --dist worksteal` to match CI's runner core count: `mypy
  abicheck/` and `scripts/check_ai_readiness.py` both report a clean 0
  errors/0 findings-worth-blocking every time, and
  `python scripts/verify.py --profile pr --only lint,typecheck,docs-build`
  — the literal command `.github/workflows/ci.yml`'s `lint-and-types` job
  runs — passes all three steps cleanly with no output at all, matching
  CI's own green `main` run and giving no reason to expect the reported
  zero-output failure on this exact commit. No `NameError` of any kind
  appears anywhere in a full fast-lane run under CI's exact flags either
  (that run did surface 47 failures, but every one traces to this
  *sandbox's* own pre-existing, unrelated environment quirks — an
  unsupported PyPI `castxml` 0.6.3 on `PATH` tripping a version-policy
  guard before the code under test even runs, `python3 -I` isolated
  subprocess calls in `tests/test_action_resolve_baseline.py` not seeing a
  user-site-installed `abicheck` package, and one `agent-evals` test
  needing git history this clone's shallow fetch doesn't have — none
  matching the reported shape and reproducing identically against a
  from-scratch `main` clone too, so none are a regression from this
  round's own changes). One genuine, if tangential, side-finding along the
  way: a bare, whole-tree `ruff format --check abicheck/ tests/` (the
  `fmt-check` step `scripts/verify.py`'s own catalog defines, but which
  `lint-and-types` does **not** actually invoke — only `lint`/`typecheck`/
  `docs-build` are) fails broadly (482 of 1001 files) on this exact commit
  regardless of ruff version (reproduced identically under both the
  `pixi.lock`-pinned `ruff==0.15.22` and the unpinned `>=0.3` constraint's
  currently-latest `0.16.3`) — `ruff.toml` sets no `[format]`/`line-length`
  override, so the formatter defaults to wrapping at 88 columns despite
  this repo's own "No line length limit (ruff E501 ignored)" convention
  covering only the *linter*'s `E501`, not the separate formatter. Real,
  but not the cause of the reported mystery (that step isn't part of any
  CI job that runs today), and pre-existing on `main` rather than
  introduced by any of this PR's 19 rounds — noted here rather than
  "fixed" since deciding *how* to reconcile the formatter with the
  documented line-length policy (an explicit large `line-length`, disabling
  `fmt-check` from the catalog entirely, or reformatting 482 files) is a
  repo-hygiene decision for a maintainer, out of scope for this round's own
  two review findings. This is now a
  two-agent-plus-coordinator-independent failure to reproduce despite
  exhausting every practical local avenue (sequential and parallel runs,
  both Python 3.11 and 3.13, fresh worktrees, and now a fresh clone plus
  fresh venv); `.github/workflows/ci.yml`'s own `ai-readiness` job comment
  independently documents that at least one adjacent metric in this same
  job ("`fast_test_cases_collected`... observed to drift by a small,
  unexplained amount even between environments that match on Python
  version and every dependency version") is already known to be
  non-deterministic across otherwise-identical environments for reasons
  nobody has fully diagnosed, which is at least consistent with (though
  not proof of) a similarly environment-level, non-code explanation for
  this round's three findings too. Left open pending either a maintainer
  with direct CI-runner access, or the fresh CI run this round's own push
  triggers narrowing down which specific errors recur.

- **Round 20** closed four of the five remaining CodeRabbit findings on this
  PR (the fifth was already correct, verified rather than re-fixed):
  1. `adapters.base._add_generic_flag_option`'s generic-flag `BuildOption`
     projection now strips a legacy `-Xclang `-marker survivor prefix (the
     pre-round-17 internal encoding, still decode-only-recognized per
     `_XCLANG_WRAPPED_ABI_FLAG_MARKER`'s own docstring) before deriving the
     option's key/value, while still preserving the original, unstripped
     token in `raw` — without this, a legacy evidence pack's marked survivor
     and a freshly-captured pack's canonical survivor for the identical
     `-target-abi`/`-target-cpu`/`-target-feature`/`-target-linker-version`
     flag derived two different `BuildOption` keys
     (`-Xclang -target-abi` vs. `-target-abi`), so `build_diff` read one
     unchanged option as a false removal on one side plus a false addition
     on the other. Regression test:
     `test_split_target_abi_flag_legacy_xclang_marker_survivor_produces_same_option`
     in `tests/test_build_source_pack.py`.
  2. `_argv.py`'s `split_operand_survivor` docstring corrected — it no
     longer claims `extract_abi_relevant_flags` normalizes the
     `-Xclang`-wrapped capture shape into a second, distinct internal
     encoding; both capture shapes have encoded identically (no marker)
     since round 17, and the marker is decode-only backward compatibility.
  3. This changelog fragment's own markdownlint MD038 violation (a
     trailing-space code span in single backticks) fixed by padding with
     double backticks.
  4. The fifth CodeRabbit finding on `tests/test_header_compile_context_gcc_path.py`
     was verified already correct on this commit (no stray `os` usage
     outside a docstring) — nothing to fix, per CodeRabbit's own "✅
     Addressed" note.
  5. `tests/test_source_extractors.py`'s `_PLAIN_TOKEN` Hypothesis filter
     now compares each generated token's own basename (splitting on both
     `/` and `\`) against the launcher/`env` name sets, matching
     `_skip_env_prefix`/`strip_launchers`'s own basename-based matching —
     previously it compared the whole token, so Hypothesis could generate a
     token like `a/env` that `strip_launchers` correctly strips (by
     basename) but the test's own filter didn't exclude, causing
     intermittent assertion failures.
  Also hoisted the already-computed `_is_msvc_command(cu.argv)` local in
  `header_compile_context.py`'s flag-rendering function into one variable
  reused by both call sites, instead of recomputing it a second time in the
  per-flag loop (CodeRabbit nitpick).
- 21st round: closed the CI-only-reproducible failure that had been
  breaking every check on this branch since round 17 — `mypy` (both the
  `lint-and-types` and `ai-readiness` CI jobs) reported `Name "os" is not
  defined` twice inside `header_compile_context._resolve_driver_token()`,
  and the same function's use at runtime produced `NameError: name 'os' is
  not defined` in ~19-25 tests exercising it, but only under CI's exact
  invocation — the module's own top-level `import os` (present and
  correctly placed) resolved cleanly in every one of five independent
  local-reproduction attempts (fresh clones, fresh `pip install -e
  ".[dev]"`, matching CI's pinned mypy/Python versions, matching CI's
  `pytest -n auto --dist worksteal` flags), and a byte-level diff against
  the file fetched directly through the GitHub API (bypassing git
  entirely) showed no encoding anomaly either. Root cause not identified
  despite five independent attempts (a diagnostic pass added to
  `scripts/verify.py`'s `run_step`/`scripts/check_ai_readiness.py`'s
  `check_mypy_baseline` to always print subprocess output, rather than
  swallowing it on failure, is what surfaced the actual error text after
  three prior rounds of these checks failing with zero visible
  diagnostic output). Fixed defensively rather than diagnostically:
  `_resolve_driver_token()` now shadows the module-level `os` import with
  its own local `import os as _os` and refers to `_os` throughout its
  body — semantically a no-op (Python allows a local import identical to
  an outer one with zero behavioral difference at runtime) but it
  guarantees the name resolves within this function's own scope
  regardless of whatever caused the module-level binding to be invisible
  to mypy/the interpreter under CI's specific environment. Documented as
  a known, unresolved environmental anomaly rather than a fully
  root-caused fix — a future contributor hitting the same shape of
  CI-only, unreproducible `NameError`/mypy `name-defined` failure should
  not assume this file's `import os` is the only place it could recur.
