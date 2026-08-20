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
- Merged `main` (27 commits of drift since this branch's original base)
  into this branch to resolve real conflicts (`mergeable_state: "dirty"`)
  across the four files this PR revises most:
  `abicheck/buildsource/adapters/base.py`,
  `abicheck/buildsource/header_compile_context.py`,
  `abicheck/buildsource/source_extractors/_argv.py`, and
  `abicheck/service_input_resolution.py`. Every conflict was a genuine
  case of both sides adding real, compatible work to the same region, not
  a duplicate or a stale change — resolved by preserving both:
  `adapters/base.py`'s MSVC/clang-cl driver-name recognition now goes
  through `main`'s single `header_utils.is_msvc_driver_stem` vocabulary
  (dropping this PR's own narrower, dumper_clang-based
  `_is_cl_style_driver_name`, which it subsumes) while keeping this PR's
  own executable-position restriction fix (never matching a source file
  whose name happens to end in `-cl`); `header_compile_context.py` keeps
  both branches' new `_EffectiveContextSignature` fields (`gcc_path` here,
  `forced_includes` from `main`'s PR D/3B) and both new `CompileContext`
  fields in the rendered command and the ambiguity-error message;
  `_argv.py` keeps this PR's new split-operand-ABI-flag decoding
  (`SPLIT_OPERAND_ABI_FLAGS`/`split_operand_survivor`) while adopting
  `main`'s move of the forced-include matchers into `header_utils` (a
  strict supersession of this PR's own now-dead local copies, verified
  identical logic); `service_input_resolution.py` drops this PR's own
  `_seeded_includes`/`_merge_l3_compile_context`/`_seeded_compile_context`
  in favor of `main`'s consolidated `_seeded_includes_and_compile_context`
  (`main`'s independent PR C dedup, landed after this PR's own Finding-3
  fix and superseding its home for that logic) — but `main`'s own moved
  copy of `_merge_l3_compile_context` (now living in
  `buildsource/l2_seed.py`) had NOT picked up this PR's Finding-3
  `gcc_path`/`gcc_prefix` "derived leads, explicit wins" precedence fix,
  since `main`'s move predates this PR's fix landing — ported forward into
  `l2_seed._merge_l3_compile_context` so the fix is not silently lost by
  the consolidation. Also fixed two merge-adjacent issues the merge itself
  exposed rather than caused: `tests/test_verify_profiles.py`'s
  `test_an_ordinary_failure_is_still_a_failure` (a `main`-only test)
  needed its mock subprocess result to carry `stdout`/`stderr` attributes
  once combined with this PR's own `run_step` diagnostic-printing change;
  and `tests/test_header_compile_context.py`/`tests/test_build_source_pack.py`
  both grew past the 2000-line AI-readiness hard cap once both branches'
  independent additions landed together, split into two new sibling files
  (`tests/test_header_compile_context_merge.py`,
  `tests/test_build_source_pack_redaction.py`) following this repo's
  established sibling-split convention.
- **Round 22 (Codex + CodeRabbit, seven findings on `73d4bb3cb9`).**
  (1) `_argv.py`'s `strip_launchers()` now recognizes GNU `env`'s
  `-S`/`--split-string` flag (`env -S 'clang-cl /c x.cc'`) and expands it
  with shell-word-splitting semantics (`shlex.split`, an env-compatible
  approximation), splicing the resulting tokens into argv in place of the
  single flag+string pair so downstream launcher/driver detection sees
  them as if passed directly — previously left as an opaque, unrecognized
  token, hiding the real compiler entirely. `strip_launchers` now
  operates on a local copy of argv so this in-place splice can never
  corrupt a caller's own `compile_unit.argv`.
  (2/4) Both header→compile-unit driver-path resolution
  (`header_compile_context._resolve_driver_token`) and the `env`-prefix
  driver-token folding (`_argv._apply_env_context`) now detect an
  absolute driver path using BOTH Windows (`PureWindowsPath`/`ntpath`,
  drive-letter and UNC forms) and POSIX (`PurePosixPath`/`posixpath`)
  grammars regardless of which host OS abicheck itself runs on, via two
  new shared helpers (`is_absolute_path_token`/`normalize_path_token`/
  `join_path_token` in `_argv.py`) — previously a Windows-shaped absolute
  path analyzed on POSIX (or the reverse) was misjudged relative and
  joined onto the compile unit's own `directory` a second time,
  corrupting `gcc_path` and spuriously raising
  `HeaderCompileContextAmbiguousError` for otherwise-identical units.
  (3) `env -C DIR PATH=../tool clang-cl ...`: a relative `PATH=` entry is
  now composed against the directory GNU `env` actually executes the
  command from (`cu.directory` + the effective `-C` chdir) before being
  handed to `shutil.which`, via a new `strip_launchers(..., directory=...)`
  keyword parameter wired from `pick_compiler_binary` (the one caller with
  a full `CompileUnit` in scope) — previously resolved from abicheck's own
  CWD, leaving the driver bare or silently resolving a different,
  incorrect executable.
  (5) `scripts/verify.py`'s stdout/stderr line-buffering reconfiguration
  moved out of module-import scope into a new `_enable_line_buffered_output()`
  helper called from `main()`, so importing the module (e.g. from a test)
  no longer has this process-wide side effect.
  (6) `run_step()` now prints a step's captured stdout/stderr immediately
  after `subprocess.run()` returns, before the `PARTIAL`-result early
  return — previously a step returning PARTIAL never had its own
  diagnostic output printed, defeating half the point of the round-20
  diagnostic-visibility fix for exactly the steps most likely to need it.
  (7) Windows-executable test fixtures (`tests/test_source_extractors_env.py`,
  `tests/test_header_compile_context_gcc_path.py`, `tests/test_adapter_base.py`)
  now create a `.exe`-suffixed file on `win32` while keeping the
  extensionless compiler token in argv, since `shutil.which` consults
  `PATHEXT` on Windows rather than doing an exact bare-name match.
  Several pre-existing tests asserting POSIX-style forward-slash driver
  paths via a self-referential `os.path.normpath(...)` expectation were
  also fixed to pin the actually-correct, host-independent literal value
  instead (discovered via live Windows CI signal on this same commit).
- **Round 23 (Codex, four findings on `87ec909570`, plus four more found by
  a parallel triage pass in the same area — all eight in the `env`-prefix
  unwrapping machinery, `_argv.py`/`adapters/base.py`/
  `header_compile_context.py`).**
  (1) `_argv.py`'s `join_path_token()` used to pick its join grammar from
  the RELATIVE token's own separator style alone — wrong whenever *base*
  is itself unambiguously absolute in the OTHER grammar (a Windows compile
  unit `directory` composed with a POSIX-spelled relative `env`-supplied
  `PATH=` entry treated the whole Windows base as one opaque `posixpath`
  component, corrupting the result). A new `_join_grammar()` now prefers
  *base*'s own grammar whenever it is unambiguous, falling back to the
  token's own style only when *base* isn't.
  (2) `adapters.base._msvc_driver_scan()`/`_executable_token_positions()`
  computed the driver's argv index via `len(original_argv) -
  len(strip_launchers(original_argv))` — silently wrong once `env
  -S`/`--split-string` changes the token count (e.g. `env -S 'clang-cl /c
  x.cc'` coincidentally kept the same length, landing on index 0, `"env"`,
  instead of the real driver). A new shared primitive,
  `_argv.expand_env_split_prefixes()`, is now applied once and both sides
  of the scan/subtraction operate on that SAME expanded list.
  (3) `_argv._resolve_env_path_entries()` left an EMPTY `env`-supplied
  `PATH=` component (`PATH=:`, `PATH=/a::/b`) unchanged, falling through to
  `shutil.which` with an empty entry resolved against abicheck's own
  process CWD rather than the compile unit's effective (chdir'd)
  directory — POSIX/GNU both document an empty component as `.` (the
  effective cwd). Now substituted with the same composed base a genuine
  `.` entry already resolves against.
  (4) `_argv.py`'s recognized `env` no-operand flag set had the WRONG long
  spelling for `-v` — `--verbose` instead of GNU env's real, documented
  `-v, --debug` (confirmed against a real installed `env --help`; a real
  `env --debug ...` command was not recognized as an `env` prefix at all,
  losing the real driver entirely). Corrected to `--debug`.
  (5) `header_compile_context._derived_gcc_path()`'s fallback for a
  GENERIC (non-CL-named) driver behind a launcher — MSVC dialect detected
  only via `--driver-mode=cl`, e.g. `sccache /opt/llvm/bin/clang
  --driver-mode=cl /c x.cc` — used raw `cu.argv[0]` (the launcher itself)
  instead of unwrapping it via `strip_launchers`, the same fix already
  applied for the CL-named case.
  (6) `_argv._skip_env_prefix()` now recognizes GNU/POSIX `env`'s bare `-`
  (deprecated equivalent of `-i`) and `--` (option-parsing terminator)
  command-separator forms — previously neither matched any recognized
  flag/assignment shape, so the scan broke on them and mistook the literal
  `-`/`--` token itself for the driver.
  (7) Chained `env -C` prefixes (`env -C a env -C b driver ...`) now
  COMPOSE their relative chdirs (`a/b`, matching what real `env` actually
  does) instead of the most recent `-C` value silently overwriting and
  discarding every earlier one; a later genuinely absolute `-C` still
  fully replaces the accumulated value, matching real `env` semantics.
  (8) `env -C DIR`'s effective chdir now folds into every operand AFTER
  the driver too (a positional source-file argument, and known
  path-bearing flags like `-I`/`-isystem`/`-iquote`/`-idirafter`/
  `-isysroot`/`--sysroot`/MSVC `/I`/`/FI`, both combined- and
  separate-token spellings), not just the driver token itself — real `env
  -C` chdirs the WHOLE invoked process, so every relative filesystem
  argument the command receives is affected, not merely its own
  executable name. Deliberately conservative: an unrecognized flag or a
  `-D`/`-U` macro value is left untouched to avoid corrupting an unrelated
  flag that merely contains a path separator.
  Also fixed, from live Windows CI signal on `87ec909570` (pre-existing,
  not introduced by this round): two `tests/test_source_extractors_env.py`
  fixtures omitted capturing `_make_executable()`'s own return value,
  silently asserting against the wrong (missing-`.exe`) expected path on
  Windows; and every "resolved via `shutil.which`" assertion across
  `test_source_extractors_env.py`/`test_adapter_base.py`/
  `test_header_compile_context_gcc_path.py` now compares case-insensitively
  (`os.path.normcase`) — Windows' `PATHEXT` resolution can return a
  resolved path with different extension casing (e.g. `.EXE`) than the
  fixture file's own on-disk casing, both naming the same file on a
  case-insensitive filesystem.
- **Round 24 (Codex, four more findings on `eafb343061` in the same
  `env`-prefix area, plus a self-found host-pathsep bug live Windows CI
  surfaced in round 23's own new tests).**
  (9) `_argv.expand_env_split_prefixes()` only ever checked position 0 for
  a leading `env`, so a launcher preceding a NESTED `env -S`
  (`sccache env -S 'clang-cl /c x.cc'`) never got its split-string
  expanded, reproducing round 23's Finding 2 bug through a different
  prefix shape. Fixed by extracting the one shared interleaved-
  unwrap traversal (`_traverse_env_and_launcher_prefix`) that
  `strip_launchers`, `expand_env_split_prefixes`, and the new
  `effective_directory` (below) all now build on, instead of three
  independently-drifting copies of the same loop.
  (10) Two real L4-replay call sites
  (`source_extractors.castxml.CastxmlSourceExtractor.extract`,
  `source_extractors.clang.ClangSourceExtractor.extract`) never consumed
  round 23's own chdir-folding fix at all: both still spawned their
  compiler subprocess with `cwd=compile_unit.directory` unchanged, while
  `replay_extra_flags()` carries raw, unmodified relative flag values
  straight from `compile_unit.argv`. A new `_argv.effective_directory()`
  composes `directory` with any leading `env -C`/`--chdir` prefix
  in `argv`, now threaded into both extractors' subprocess `cwd`
  computation (their `source`/compile-database `file` resolution
  deliberately keeps using the raw, un-chdir'd `directory`, since that
  pairing is independent build-system metadata, not part of what `env -C`
  affects).
  (11) `_fold_chdir_into_operands()` (round 23 Finding 8) classified each
  token independently by string shape (separator-presence, leading
  `-`/`/`) rather than tracking flag CONTEXT while walking -- so a
  SEPARATE-form macro value (`-D FOO=a/b`, two tokens) had its own value
  token misread as a bare positional path operand purely because it
  contains a `/`, corrupting the macro definition. Rewritten as a
  stateful, context-aware scan that tracks a macro flag's own following
  token and never resolves it (mirroring the already-correct combined-form
  handling).
  (12) The same string-shape guess also silently skipped a real relative
  path with NO separator at all (`-Iinclude`/`-I include`, a one-level
  subdirectory) -- fixed by the same context-aware rewrite: a
  known path-bearing flag's value now folds unconditionally once flag
  context has established it as a path, regardless of whether the text
  itself contains a separator. Also newly recognizes the bare
  (non-`=`) `--sysroot DIR` separate-form spelling and GCC's `-include
  FILE` (separate-form only, no combined spelling exists).
  Separately, live Windows CI on round 23's own push surfaced a genuine
  production bug in `_resolve_env_path_entries()` (Finding 3's own
  helper): it split/joined on host-native `os.pathsep` (`;` on Windows)
  instead of POSIX `env`'s own fixed `:` PATH separator, and then handed
  a `:`-joined string to a single `shutil.which(path=...)` call, which
  ALSO splits on host-native `os.pathsep` internally -- doubly wrong on
  Windows, and additionally ambiguous there against a Windows drive-letter
  colon (`C:\...`). Fixed by always splitting on a literal `:` via a new
  `_split_posix_path_value()` (which specifically does NOT split a
  Windows drive-letter colon), and by returning a LIST of candidate
  directories that the caller now searches with one single-directory
  `shutil.which` call per candidate, sidestepping any pathsep-
  representation question entirely. The round-23 tests this broke were
  also fixed to stop computing a handful of expected values via
  host-native `os.path.join`/`os.path.normpath` for POSIX-spelled
  evidence tokens (a driver path, an `-I`/positional operand containing
  `/`) -- those are pinned as literal, always-forward-slash strings now,
  matching this module's own pre-existing pinning discipline (see
  `test_strip_launchers_env_chdir_multiple_dotdot_segments_normalized`)
  instead of silently reintroducing host-dependence into the test itself.
- **Round 25 (Codex, three findings on `2dde42aef9`, same `env`-prefix /
  chdir-fold area).**
  (13) `_fold_chdir_into_operands()`'s SEPARATE-form flag-context tracking
  (round 24 Finding 11/12) exempted only the macro-flag family
  (`-D`/`-U`/`/D`/`/U`) from being misread as a positional path operand --
  every OTHER separate-form value-taking flag was still classified purely
  by textual shape, so `-x c++` (a language selector, not a path) was
  corrupted into `-x build/c++`, and the identical corruption applied to
  `-target <triple>`, `-arch <name>`, and `-Xclang <value>`. Fixed the same
  way the macro exemption was: a new
  `_CHDIR_FOLD_NON_PATH_VALUE_FLAGS` set (`-x`/`-target`/`-arch`/`-Xclang`)
  consumes its flag and value token verbatim, checked ahead of the generic
  positional fallback -- an unrecognized flag's own possible operand is
  still left to the pre-existing, documented conservative fallback
  (unchanged from before this fix), matching the same "extend only for a
  positively-confirmed-non-path flag, not an attempt to enumerate every
  flag" principle the macro fix already established.
  (14) `_skip_env_prefix()`'s recognized no-operand ``env`` flag vocabulary
  was still missing four flags a real installed `env --help` documents:
  `--block-signal[=SIG]`, `--default-signal[=SIG]`, `--ignore-signal[=SIG]`
  (each valid both bare and with an `=SIG` suffix), and
  `--list-signal-handling` (bare only). A real recorded command like `env
  --ignore-signal=PIPE clang-cl /c x.cc` fell through every recognized
  branch and mistook the literal `--ignore-signal=PIPE` token itself for
  the driver, losing `clang-cl` and everything after it -- the identical
  failure shape round 23 Finding 4 (`--debug`) and Finding 6 (bare
  `-`/`--`) already fixed for this same function. All four bare forms
  added to `_ENV_NO_OPERAND_FLAGS`; the three optional-`=SIG` forms
  additionally matched via a new `_ENV_OPTIONAL_SIG_FLAG_PREFIXES` prefix
  check (GNU documents no separate-token form for any of these three, so
  no additional operand-flag entry is needed).
  (15, **known gap, not fixed**) `_expand_env_split_string()`'s
  `shlex.split()`-based approximation of GNU `env -S`/`--split-string`
  gives WRONG results for that flag's own, ENV-SPECIFIC backslash-escape
  grammar -- verified directly against a real installed GNU coreutils 9.4
  `env` (`env -v -S '...'`, which prints its own parse trace): `\_`
  (backslash-underscore) means "split an argument here" (a token
  boundary), NOT a literal underscore (`env -S 'a\_b'` splits into `a`
  and `b`; `shlex.split` produces one token `a_b`), and `env` separately
  documents/exhibits a `\t`-family literal-character-preserving escape and
  a `\c` comment-terminator escape (`env -S 'a\cREST'` yields only `a`,
  silently discarding `REST`), none of which is POSIX-shell-word-splitting
  and none of which `shlex.split()` implements. Deliberately left as a
  documented known gap rather than implemented as a full custom parser,
  per this repo's own established "known gaps over risky reactive
  patches" convention (`AGENTS.md`): `-S`/`--split-string` exists
  specifically for shebang-line use, a context with no relationship to how
  a build system's compile database records an already-fully-expanded
  `argv` -- a captured build action essentially never contains a literal,
  unexpanded `env -S` invocation carrying one of these exotic escapes, and
  building/maintaining a byte-for-byte reimplementation of GNU coreutils'
  own `-S` grammar is a genuinely large, narrowly-scoped subsystem for a
  case with no known real-world reproduction in captured build evidence.
  The common, realistic case -- plain space-separated arguments, and
  single/double-quoted arguments containing a space -- is handled
  correctly today; see `_expand_env_split_string()`'s own docstring for
  the full detail. Replied to the review thread explaining this choice
  rather than silently leaving the finding unaddressed.
  New regression tests (`tests/test_source_extractors_env.py`): positive
  coverage for each of the four non-path-value flags plus a negative
  control confirming a genuine path-taking flag (`-I`) and an unrecognized
  flag's pre-existing conservative fallback are both unaffected (13); one
  test per signal-handling flag (bare and `=SIG` forms) plus a negative
  control confirming an unrelated, genuinely unrecognized flag still isn't
  swallowed (14).
- **Round 26 (Codex, three findings on `71151d3735`, same `env`-prefix /
  compiler-driver-resolution area).**
  (16) `pick_compiler_binary()` returned `strip_launchers()`'s driver token
  verbatim, without joining it onto the compile unit's own `directory` --
  for `env -C build ../tool/clang++ ...` recorded in directory `/work`,
  `strip_launchers()`/`_apply_env_context()` correctly fold the `-C build`
  value into the token (`join_path_token("build", "../tool/clang++")` ==
  `"tool/clang++"`), but that result is still *relative* to `/work`, not to
  whatever cwd a caller happens to run a subprocess from.
  `CastxmlSourceExtractor` runs from the effective `-C` cwd (`/work/build`,
  per round 23/24's chdir-cwd fixes), so the unjoined token resolved a
  second, wrong time to `/work/build/tool/clang++` instead of the real
  `/work/tool/clang++`. Fixed with a new `_resolve_relative_driver()`
  helper mirroring `header_compile_context._resolve_driver_token()`'s own
  treatment of this exact shape (host-independent absolute check/join via
  the already-local `is_absolute_path_token()`/`join_path_token()`/
  `normalize_path_token()`, rather than importing that private helper back
  from `header_compile_context` -- which imports `adapters.base`, which
  imports from this module at module scope, so the reverse import would be
  a genuine cycle). Generalized (per this repo's "fix the cause, not the
  instance" convention) to join *any* relative driver token this function
  returns onto `directory`, not only an `env -C`-composed one --
  `pick_compiler_binary()` was the one caller inconsistent with
  `_derived_gcc_path()`'s already-established identical behavior for a
  plain relative driver path with no `env` prefix at all.
  (17) `header_compile_context._derived_gcc_path()` called
  `adapters.base._is_msvc_command()`/`msvc_driver_token()` with no
  `directory` argument -- the parameter didn't exist on either function --
  so for `env -C build PATH=../tool clang-cl /c x.cc`, the internal
  `strip_launchers()` call inside `_msvc_driver_scan()` resolved the
  relative `PATH=../tool` entry against abicheck's own process CWD instead
  of the compile unit's real base directory + effective `-C` chdir, the
  identical wrong-CWD failure (16) fixes for `pick_compiler_binary()`'s own
  env-unwrapping. Added an optional `directory: str | None = None` keyword
  to `_executable_token_positions()`, `_msvc_driver_scan()`,
  `_is_msvc_command()`, and `msvc_driver_token()` (all defaulting to the
  pre-fix behavior, so every other, directory-agnostic caller is
  unaffected), threaded through to `strip_launchers()`; `_derived_gcc_path()`
  now passes `directory=cu.directory` to both calls it makes.
  (18) Nested `env` wrappers had no way to represent "PATH was just reset
  to empty" as distinct from "no PATH opinion stated, inherit whatever an
  outer layer already established" -- both were folded into plain `None`.
  For `env PATH=/tmp/tool /usr/bin/env -i clang-cl ...`, real GNU `env -i`
  starts the wrapped command with a genuinely empty environment (confirmed
  via `env --help`: "start with an empty environment"), so the outer
  `PATH=/tmp/tool` must not survive through the inner `-i` -- but
  `_traverse_env_and_launcher_prefix()` only ever *overwrote* its tracked
  `env_path` on a new `PATH=` assignment, so `-i`'s `None` looked
  identical to "this layer said nothing," and the outer PATH incorrectly
  carried through. The same applies to `env -u PATH ...`/`env --unset=PATH
  ...` (explicitly unsetting just `PATH`) and the deprecated bare `-`
  alias for `-i`. Fixed with a new `_EnvPathCleared` sentinel (one module-
  level instance, `_ENV_PATH_CLEARED`) `_skip_env_prefix()` returns instead
  of `None` for exactly these four shapes; `_traverse_env_and_launcher_prefix()`
  resets its accumulated `env_path` to `None` on seeing it (never letting
  the sentinel itself escape to any other consumer -- `_apply_env_context()`
  and every other reader still only ever sees a real `str` or `None`,
  unchanged). New regression tests
  (`tests/test_source_extractors_env_round26.py`, a sibling split of
  `tests/test_source_extractors_env.py` to stay under its 1500-line
  AI-readiness soft cap, plus additions to `tests/test_adapter_base.py`
  and `tests/test_header_compile_context_gcc_path.py`): positive coverage
  for the `env -C`-relative-driver-token join (16) with negative controls
  for a bare PATH-searched name, an already-absolute token, and an empty
  `directory`; positive coverage for the `directory`-threaded MSVC-driver
  PATH resolution (17) with a negative control confirming an ordinary
  non-`env`-wrapped command is unaffected; and positive coverage for each
  of the four PATH-clearing shapes (18) -- bare `-i`, `-u PATH`,
  `--unset=PATH`, bare `-` -- with negative controls confirming unsetting
  an unrelated variable does *not* clear PATH, and (Codex's own explicitly
  requested case) a plain nested `env` carrying only an unrelated
  `NAME=VALUE` assignment still correctly *inherits* the outer PATH=,
  proving the fix is scoped to the two explicit-clearing shapes and not a
  regression in ordinary nested-`env` PATH inheritance.
- **Round 27 (Codex review, 3 findings).** (1) Continuing round 26 Finding
  3: `_traverse_env_and_launcher_prefix()` correctly tracked "PATH was
  explicitly cleared" (`env -i`/`env -u PATH`) *within* its own loop, but
  discarded that fact the moment it returned -- every caller saw only
  `env_path is None`, indistinguishable from "no `env` prefix ever
  mentioned PATH, inherit abicheck's own inherited process PATH."
  Concretely, `strip_launchers(["env", "-i", "cc", ...])` correctly
  returned the bare, unresolved token `"cc"`, but nothing stopped
  `pick_compiler_binary()`/`header_compile_context._derived_gcc_path()`
  from later treating that bare name as trusted toolchain identity, which
  a downstream `shutil.which`-style lookup could then resolve against
  abicheck's OWN inherited `PATH` -- silently substituting a different,
  unrelated compiler than the one the real, genuinely PATH-less `env -i`
  invocation could ever have found (real `execvp` cannot search PATH at
  all with none set; only an absolute/relative path resolves). Fixed with
  a new `path_cleared: bool` returned from `_traverse_env_and_launcher_prefix()`
  and a new `env_path_cleared_for_bare_token(argv, token)` predicate both
  `pick_compiler_binary()` and `_derived_gcc_path()` now consult before
  trusting a still-bare driver token, degrading to the pre-existing
  "no compiler binary derivable" fallback instead
  (`tests/test_source_extractors_env_round27.py`, a new sibling split: 17
  cases covering the predicate directly, `pick_compiler_binary`, and
  `_derived_gcc_path`, each with negative controls for a real `PATH=`
  override un-clearing PATH, a path-shaped token, no `env` prefix at all,
  and an explicit caller override). (2) `header_compile_context.
  _resolve_driver_token()`'s already-fixed foreign-ABSOLUTE-driver-path
  handling did not extend to a foreign-RELATIVE token: a Windows compile-
  unit `directory` (`C:\work\build`) joined with a Windows-style relative
  driver (`..\llvm\bin\clang-cl.exe`) on a POSIX analysis host used
  host-native `pathlib.Path`/`os.path.normpath`, which recognizes neither
  string's backslashes as separators at all -- both were treated as ONE
  opaque path component, producing the corrupted, unnormalized
  `C:\work\build/..\llvm\bin\clang-cl.exe` instead of the correct
  `C:\work\llvm\bin\clang-cl.exe`. Fixed by delegating to the existing
  `join_path_token()` helper (already used for the absolute case), which
  chooses the join grammar from `directory`'s own unambiguous grammar
  (`PureWindowsPath`/`PurePosixPath`, host-independent) rather than the
  host's (new tests in `tests/test_header_compile_context_gcc_path.py`:
  the exact Windows-directory/Windows-relative-token repro, a deeper `..`
  chain confirming real normalization, the symmetric POSIX case, and two
  negative controls for an ordinary host-native relative join and an
  empty `directory`). (3) `adapters.base.derive_build_options()` captured
  each `-target-feature=<sign><name>` survivor as an independent (key,
  value) pair deduplicated by exact value -- but Clang applies repeated
  `-target-feature` occurrences SEQUENTIALLY per feature name (confirmed
  empirically against a real `clang -cc1`: `+sse2 -sse2` leaves `__SSE2__`
  undefined, while `+sse2 -sse2 +sse2` leaves it defined), so
  `[+sse2, -sse2]` (net disabled) and `[+sse2, -sse2, +sse2]` (net
  enabled) reduced to the identical two-entry set, silently losing which
  state was actually in effect and risking a missed real ABI-relevant
  difference between two compile units. Fixed by resolving repeated
  `-target-feature` occurrences to their final per-feature-name state
  (last occurrence wins, keyed by feature name so two *different* features
  stay independent) via the existing last-one-wins `mode_values`
  resolution `derive_build_options()` already runs every flag through --
  scoped to `-target-feature` only; the sibling `-target-abi`/`-target-cpu`/
  `-target-linker-version` split-operand flags are single-scalar settings
  with an already-pinned `-target-abi`-shaped key/value test contract and
  were left untouched (new tests in `tests/test_build_source_pack.py`:
  the net-disabled and net-enabled repro pair, a different-feature-names
  independence control, a single-non-repeated-occurrence negative control,
  and a cross-unit disagreement-still-diffable end-to-end case). Also
  fixed two live Windows-CI-only test bugs surfaced while verifying this
  round's own `_resolve_driver_token` changes, both in round 26's own new
  test file (`tests/test_source_extractors_env_round26.py`), neither a
  production-code bug: `test_pick_compiler_binary_resolves_env_chdir_relative_driver_path`
  compared against a filesystem-resolved, PATHEXT-`.exe`-suffixed fixture
  name for a token that is never filesystem-resolved at all (a
  path-shaped, not bare, driver token is composed purely lexically, by
  design); `test_pick_compiler_binary_resolves_plain_relative_driver_without_env_too`
  asserted a host-native `os.path.normpath` expectation for a `directory`
  (`/work`) that is unambiguously POSIX-absolute regardless of host --
  the intentional, already-established host-independent grammar this same
  PR series' own `join_path_token()` implements, matching the literal-
  expected-value pattern `test_header_compile_context_gcc_path.py`'s own
  foreign-absolute-path tests already use.
- **Follow-up to round 26/27: closed the test-coverage gap that let those
  two test bugs go undetected for a full round.** Live Windows CI on this
  PR (run for commit `04c700b9`, round 26) failed exactly those two tests
  with the same symptoms round 27's changelog entry above already
  diagnosed and fixed (a `.exe`-suffixed fixture-name comparison, and a
  host-native `os.path.normpath` expectation); by the time round 27's own
  commit (`2bb7e0d2`) landed the corrected, literal, host-independent
  assertions, `_resolve_relative_driver()` itself needed no change --
  investigation (re-reading `_resolve_relative_driver()`,
  `pick_compiler_binary()`, and `_resolve_driver_token()`'s already-fixed
  sibling treatment side by side, confirmed via `git diff` between the two
  commits) showed `_resolve_relative_driver()` already delegated to the
  same host-independent `is_absolute_path_token()`/`join_path_token()`/
  `normalize_path_token()` grammar `_resolve_driver_token()` uses, in round
  26's own original commit -- only the *test's* expected values were wrong,
  never the production code. What was still missing is why that
  round-26-vintage test bug survived this PR's own Linux-only local
  verification for a whole round before a live Windows runner caught it:
  neither existing test ever drove `_resolve_relative_driver()` through its
  `ntpath` branch on a non-Windows host -- the `env -C` test's `directory`
  is host-native (POSIX on every CI runner that isn't `windows-latest`),
  and the plain-relative test's `directory="/work"` is POSIX-literal by
  construction. `test_header_compile_context_gcc_path.py` already has this
  exact host-independent shape for the sibling `_resolve_driver_token()`
  (`test_resolve_driver_token_windows_relative_driver_joined_with_windows_
  directory`/`test_resolve_driver_token_posix_relative_driver_joined_with_
  posix_directory`) -- `_resolve_relative_driver()` had no equivalent.
  Added the missing pair directly to
  `tests/test_source_extractors_env_round26.py`
  (`test_resolve_relative_driver_windows_relative_driver_joined_with_
  windows_directory`/`test_resolve_relative_driver_posix_relative_driver_
  joined_with_posix_directory`), calling `_resolve_relative_driver()`
  directly with a literal `C:\work\build`/`..\llvm\bin\clang-cl.exe` pair
  (and the symmetric POSIX pair) so this class of grammar bug -- in either
  the production helper or a future test's own expectations -- is caught
  on ANY CI runner, not only a Windows one. No production code changed for
  this part; a fresh full local verification pass (`ruff check`,
  `ruff format --check`, `mypy abicheck/`, the fast test suite,
  `check_ai_readiness.py`) and a live Windows CI re-run on this PR's
  current head both confirm the two originally-reported tests already
  pass.
- **The live Windows CI re-run triggered by the investigation above
  surfaced one more, genuinely NEW instance of the identical test-bug
  class, in a third sibling test.** `test_header_compile_context_gcc_path.
  py::test_resolve_driver_token_host_native_relative_join_unaffected` -- a
  round 27 negative control asserting that an "ordinary host-native
  relative join" (a bare, separator-free `directory="build"` plus a
  POSIX-spelled relative token, `"../tool/clang-cl"`) is "unaffected by
  this fix" -- failed on the real Windows runner with
  `assert 'tool/clang-cl' == 'tool\\clang-cl'`. The premise was wrong, not
  the production code: `_join_grammar()` (round 27's own fix, described
  above) resolves an ambiguous `directory` like `"build"` by falling back
  to the *token*'s own separator style, and `"../tool/clang-cl"` is
  unambiguously POSIX-spelled -- so `_resolve_driver_token()` correctly,
  deliberately composes it with `posixpath` regardless of host, exactly
  like every other case this same round's fix targets. The test's
  `os.path.normpath(...)` expectation baked in host-native grammar for a
  case that was never actually host-native, the identical mistake round 26
  made for the two tests documented above -- confirmed by re-deriving the
  expected value directly from `_join_grammar()`'s own documented fallback
  rule rather than from a live Windows run. Fixed by replacing the
  host-native expectation with the literal, host-independent
  `"tool/clang-cl"` and correcting the docstring to explain why this
  negative control's assumption was wrong (this IS host-independent
  behavior, not "unaffected by the fix"). A second live Windows CI re-run
  on this PR's current head, after this fix, confirms all three tests pass
  together with no other failures.
- **Round 29 (Codex review, 6 findings, all in the same `env`/build-evidence
  argv-parsing area).** (A) `_fold_chdir_into_operands`'s known-path-flag
  vocabulary was missing `-cxx-isystem`/`/imsvc`/`/external:I` -- three
  include-option families `header_utils._INCLUDE_FLAG_PREFIXES` already
  classifies as include-search flags elsewhere, so an attached-form token
  like `/imsvcinclude` under `env -C build` stayed relative to the wrong
  pre-chdir directory. Added to both `_CHDIR_FOLD_COMBINED_PATH_PREFIXES`
  and `_CHDIR_FOLD_SEPARATE_PATH_FLAGS`. (B) `pick_compiler_binary`/
  `header_compile_context._derived_gcc_path` unconditionally degraded a
  PATH-cleared bare driver token (`env -i`/`env -u PATH`) to the
  language-based `gcc`/`g++` fallback -- but a genuinely PATH-less
  `execvp` still searches the platform's own default path
  (`confstr(_CS_PATH)`, confirmed empirically: `env -i cc --version`
  succeeds on a real system; Python's `os.defpath` is the identical
  value). New `resolve_bare_token_with_default_path()` is tried first,
  falling back to the language default only when unresolvable there too.
  (C) `_fold_chdir_into_operands` corrupted a response-file token
  (`@args.rsp`) by rebasing the WHOLE token including its leading `@`
  sigil, producing `build/@args.rsp` -- unrecognizable as a response file
  by any consumer (`include_graph.depfile_args_from_argv()` included)
  since the sigil is no longer the first character. Fixed by preserving
  the sigil and resolving only the payload after it. (D)
  `adapters.base._mode_flag_option`'s new last-wins `-target-feature=`
  classification (round 27 Finding 3) never stripped a legacy
  `-Xclang`-marked survivor before checking `flag.startswith(...)`, so a
  persisted pack carrying the pre-canonicalization `-Xclang
  -target-feature=+sse2` spelling fell through to the generic option path
  and recorded the OLD key/value shape (`-target-feature`/`-Xclang
  -target-feature=+sse2`) while a freshly-produced pack recorded the NEW
  shape (`target_feature:sse2`/`+sse2`) for the identical effective
  compiler state -- a spurious pack-diff false remove+add. Fixed by
  stripping the marker at the top of `_mode_flag_option`, mirroring
  `_add_generic_flag_option`'s own already-established strip. (E)
  `source_extractors.clang._clang_context_args` emitted `CompileUnit.
  include_paths`/`system_include_paths` verbatim -- both already
  STRUCTURED, absolute paths resolved by the producing adapter against
  the compile unit's own `directory`, before any leading `env -C DIR`
  prefix in `argv` is considered. `extract()`'s own effective-cwd fix
  (round 26 Finding 10) only corrected the replay SUBPROCESS's cwd, not
  these two already-absolute fields, so `build_clang_command()` still
  emitted the wrong absolute `-I` for a unit recorded under `env -C`. New
  `_rebase_structured_path()` rebases each path from its old base
  (`compile_unit.directory`) onto the real effective directory via a
  literal string-prefix substitution. (F) `_skip_env_prefix` did not
  recognize GNU short-option ATTACHMENT (`-Cbuild`, DIR attached directly
  to `-C` with no space -- confirmed empirically: `env -Ctmp pwd` chdirs
  into `/tmp`) or CLUSTERING (`-iv`, multiple no-operand short flags
  grouped into one token -- confirmed against a real `env`). Both forms
  previously fell through every recognized shape and hit the terminal
  `break`, misreading the whole token as the compiler driver. Added
  recognition for both, the clustering half split into a new sibling leaf
  module (`source_extractors/_argv_shortopts.py`, alongside the new
  default-path resolver from (B)) purely to keep `_argv.py` under this
  repo's 2000-line AI-readiness hard cap -- both helpers are pure/
  stdlib-only, carrying none of `_argv.py`'s own documented import-cycle
  risk with `header_compile_context.py`.

  New regression tests: `tests/test_source_extractors_env_round29.py`
  (Findings A/B/C/F -- 22 cases, each with negative controls: an unrelated
  MSVC flag not accidentally matched, a genuinely-unresolvable-anywhere
  bare token still degrading to the language fallback, a response-file
  token with no `env -C` prefix left unchanged, the pre-existing
  separate-token `-C DIR`/`--chdir=` forms unaffected by the new attached
  form, and a cluster containing an unrecognized character falling
  through unchanged); `tests/test_source_extractors_clang_round29.py`
  (Finding E -- 6 cases, including a negative control for a path outside
  `compile_unit.directory` entirely, and the exact-directory-as-include
  edge case); `tests/test_adapter_base.py` gained 3 cases for Finding D
  (legacy vs. fresh survivor converge on the identical `BuildOption`
  shape, last-wins still applies across a mixed legacy/canonical pair, an
  unrelated `-Xclang`-wrapped flag is unaffected). Two round 27 tests
  (`test_pick_compiler_binary_falls_back_to_default_under_env_dash_i`/
  `..._falls_back_to_gcc_for_c_language_under_env_dash_i`) were updated to
  use a token guaranteed absent from `os.defpath` -- their previous
  example, `"cc"`, now genuinely resolves via Finding B's own fix on a
  typical POSIX sandbox (`/bin/cc`), which is correct new behavior, not a
  regression, but meant `"cc"` was no longer a valid example of an
  unresolvable token.

  Verification: `ruff check`/`ruff format --check` clean, `mypy
  abicheck/` 0 errors (373 source files), `check_ai_readiness.py` 0
  errors / 130 warnings (the new `_argv.py` line-count split keeps it at
  exactly the 2000-line hard-cap boundary, WARN not ERROR), the full
  env/gcc_path/clang/adapter_base/header_compile_context test family
  (748 tests) passes, full fast suite run locally.

  **Round-29 follow-up (live Windows CI, Codex review): the new
  `tests/test_source_extractors_env_round29.py` cases from the paragraph
  above themselves carried the exact bug class several earlier rounds
  already fixed in *production* code -- a host-native-separator
  assumption baked into a hardcoded expected literal.** Every rebased-path
  assertion hardcoded a forward-slash-joined literal (`"build/x.cc"`), but
  `_fold_chdir_path_value()` composes its result via `join_path_token()`,
  which falls back to *host-native* `os.path.join`/`os.path.normpath`
  whenever neither operand is unambiguously Windows- or POSIX-absolute --
  exactly the shape every one of these fixtures uses (a bare relative
  `chdir` value like `"build"` composed with a bare relative operand like
  `"x.cc"`, neither carrying a separator of its own). On a real Windows
  runner this correctly produces a backslash-joined path, which a
  hardcoded POSIX literal can never match -- 20 of the 22 new cases failed
  on live Windows CI this way (the two using a token that already
  contains a `/` of its own, forcing `join_path_token`'s POSIX-grammar
  branch regardless of host, were unaffected). Fixed by adding a `_join()`
  test helper that computes every expected literal through the identical
  `join_path_token()` the production code itself calls, instead of a
  hardcoded separator -- structurally immune to this bug class recurring,
  since the expected value is derived from the same function under test
  rather than authored by hand.

  The same live run also failed the two `os.defpath`-driven cases
  (`test_resolve_bare_token_with_default_path_finds_real_binary`/
  `test_pick_compiler_binary_resolves_default_path_driver_under_env_dash_i`)
  for an unrelated, genuine cross-platform gap in the TEST DATA, not in
  Finding B's production fix itself: both relied on a real POSIX system
  binary (`cat`) being reachable through the platform's *actual*
  `os.defpath` -- true on any POSIX sandbox (`/bin/cat`), but Windows'
  own `os.defpath` is not a populated search path the way POSIX's is (it
  names no directory a real compiler driver lives in), so `cat` never
  resolved there and both tests' assumption that it would was simply
  wrong test data for that host. Fixed by building a synthetic fixture
  executable under a directory each test itself monkeypatches onto
  `os.defpath` (mirroring `test_source_extractors_env_round26.py`'s own
  `_make_executable` helper), which portably validates the SAME
  resolution mechanism (`shutil.which(token, path=os.defpath)`) on any
  host without depending on what happens to already exist there. The
  `_derived_gcc_path` sibling case was fixed the identical way. This
  closes the third failure the coordinator separately flagged as a
  "genuinely broken test assertion" (`assert 'g++' != 'g++'`) too --
  it was not a copy-paste bug, it was the exact same `cat`-unresolvable-
  on-Windows root cause surfacing as `pick_compiler_binary` correctly
  falling back to the language default, which a hardcoded `!= "g++"`
  assertion then failed against.

  Re-verified: `ruff check`/`ruff format --check` clean, `mypy abicheck/`
  0 errors, and the full env/gcc_path/clang/adapter_base test family (138
  tests across the six affected files) plus the full local fast suite
  pass. Every expected-value computation in the fixed file is now
  derived from the production `join_path_token()` grammar directly, so
  no further host-specific literal remains to diverge from a real
  Windows runner's output.

- **Round 30: four remaining Codex review findings on this same PR, plus
  four CodeRabbit findings on the same commit, closed together.**
  1. `_skip_env_prefix()`'s `--` handling (round 23 Finding 6) broke out of
     the whole scan on `--`, treating the very next token as the driver
     unconditionally -- but real GNU `env`'s `--` only terminates OPTION
     parsing, not the `NAME=VALUE...` assignment list that still precedes
     the command. `env -- FOO=bar clang-cl -c x.cc` was mistaking
     `"FOO=bar"` for the compiler and losing `clang-cl` and everything
     after it entirely. Fixed by continuing to consume `NAME=VALUE`-shaped
     tokens (still tracking a `PATH=` assignment) after `--`, without
     re-entering any OPTION-shaped branch -- a token that looks like an
     env flag after `--` stays a literal argument, proving `--` genuinely
     still suppresses option-reinterpretation.
  2. `_rebase_structured_path()` (round 29 Finding E) could not distinguish
     an explicitly absolute `-I/work/include` operand from a relative
     `-Iinclude` operand an adapter had already resolved to the same
     absolute string by joining it onto `compile_unit.directory` -- both
     looked identical (an absolute string starting with the compile
     unit's directory), so an explicit absolute include incorrectly moved
     under `env -C`. Fixed with new provenance threaded from the point
     it's still known: `BuildContext.include_paths_explicit`/
     `system_includes_explicit` (parallel to `include_paths`/
     `system_includes`, populated in `_try_consume_include`/
     `_try_consume_isystem` before the relative-to-absolute join), a new
     `explicit_absolute_paths()` accessor, and a new
     `CompileUnit.explicit_absolute_include_paths` field populated by all
     four adapters that route through `_extract_flags`
     (`CompileDbAdapter`, `NinjaAdapter`, the Make and Bazel adapters).
     `_rebase_structured_path()` gains an `explicit_absolute` keyword that
     short-circuits the rebase; `clang.py`'s two call sites pass it from a
     `set(compile_unit.explicit_absolute_include_paths)` membership check.
  3. Both castxml's `_parse_root()` and clang's AST/macro dependency paths
     resolved `resolve_read_files()` against the raw, un-chdir'd
     `compile_unit.directory` instead of the EFFECTIVE (chdir-composed)
     directory the real subprocess actually ran from under `env -C` (round
     26 Finding 10's `effective_directory()` helper already threads
     correctly into the subprocess `cwd=`, just not into this dependency
     resolution). A relative dependency file name reported by the tool
     therefore resolved to the wrong absolute path in the per-TU cache
     fingerprint, silently omitting the real dependency and risking a
     stale cache hit after the real header changes. Fixed by resolving
     against `effective_directory(compile_unit.argv, compile_unit.directory)`
     in all three call sites (castxml's `_parse_root`, clang's
     `source_abi_from_clang_ast`, and clang's `_attach_macros`).
  4. `_fold_chdir_into_operands()`'s chdir-folding scan treated ANY bare,
     non-flag-shaped token as a foldable positional path by default,
     protected only by an ever-growing denylist of individually-enumerated
     non-path-taking flags (`_CHDIR_FOLD_NON_PATH_VALUE_FLAGS`) -- so
     `env -C build clang -target armv7-none-eabi -meabi gnu -c x.c`
     corrupted `-meabi`'s real value `gnu` into `build/gnu`, since
     `-meabi` (confirmed via `clang --help`) wasn't yet in that list. Fixed
     by inverting the default rather than adding a fifth flag: a new
     `_looks_like_source_file_operand()` helper (in `_argv_shortopts.py`,
     moved there to keep `_argv.py` under the 2000-line hard cap) folds a
     bare token only when it is independently recognizable as a genuine
     source/header operand by suffix; every other bare token -- an
     unrecognized flag's own non-path value included, whichever flag it
     followed -- is now safe by default without needing to be named
     anywhere in this module. Verified with a parametrized regression test
     covering several hypothetical never-before-seen flags, proving the
     fix is a property of the default and not another one-off addition.
  5. (CodeRabbit) `resolve_bare_token_with_default_path()` relied on
     `shutil.which(token, path=os.defpath)`, but on Windows `os.defpath`
     itself starts with `.` and `shutil.which`'s own implementation can
     still silently prepend the process's current working directory to
     the search regardless of an explicit `path=` argument (confirmed
     across CPython 3.10-3.13 source and the underlying
     `NeedCurrentDirectoryForExePathW` Windows API behavior) -- letting a
     PATH-cleared (`env -i`/`env -u PATH`) resolution pick up an unrelated
     binary from abicheck's own cwd, which a real PATH-less `execvp` never
     would. Fixed by filtering `.`/empty components out of `os.defpath`
     and checking each remaining directory directly (a new
     `_windows_candidate_names()` PATHEXT-aware helper plus a manual
     `os.path.isfile`/`os.access` check), bypassing `shutil.which` and its
     cwd-prepending behavior entirely.
  6. (CodeRabbit) `_expand_env_split_string()`'s `shlex.split()` call
     raised `ValueError` on a malformed `-S` value (e.g. an unbalanced
     quote in persisted, untrusted `compile_unit.argv`), which could abort
     an entire adapter's collection for every other compile unit alongside
     it. Fixed by catching `ValueError` and degrading to a best-effort
     `value.split()` rather than crashing.
  7. (CodeRabbit, minor) A stale test docstring claimed a PATH-less lookup
     "cannot" search at all; corrected to state it searches
     `os.defpath`, just never abicheck's own inherited `PATH`. Also
     (CodeRabbit, minor) `test_importing_verify_does_not_reconfigure_stdout_or_stderr`
     only spied on `sys.stdout.reconfigure`, matching neither its own name
     nor guarding against a stderr-only import-time side effect; now spies
     and asserts on both streams.
- **Round 31 (Codex review, fresh evidence on commit `1248386`): three
  more findings refining round 30's explicit-absolute-include-provenance
  and chdir-fold-generalization fixes.**
  1. `CompileUnit.explicit_absolute_include_paths` tracked provenance by
     VALUE (a set of which absolute path strings were "explicitly
     absolute"), not by list position -- so a unit recording BOTH a
     relative `-Iinclude` (resolves to `directory/include`) and an
     explicit `-I<directory>/include` at once, where both normalize to
     the identical final string, had BOTH entries marked "explicitly
     absolute" by the shared value-keyed set, and `_clang_context_args()`
     never emitted the real relative-rebased path the first occurrence
     should have produced. Fixed by replacing the value-keyed set with
     position-aligned `CompileUnit.include_paths_explicit`/
     `system_include_paths_explicit` lists (parallel to `include_paths`/
     `system_include_paths`), consumed via a new
     `CompileUnit.explicit_or_unknown()` helper, and wiring the identical
     fix into `castxml.py`'s `build_castxml_command()` too -- which had
     the same env-`C`-rebase gap for its own structured include paths,
     never previously fixed at all.
  2. `_fold_chdir_into_operands()`'s suffix-only source-file-operand
     detector (round 30 Finding 4) never recognized an EXTENSIONLESS
     source file paired with an explicit `-x <language>` flag (e.g.
     `clang -x c++ generated -Iinclude` -- `generated` has no suffix at
     all, but the preceding `-x c++` already states unambiguously that it
     is the source file). Left un-rebased under `env -C`, this loses the
     TU's include graph or macro evidence downstream in
     `include_graph.py`/`preprocessor_scan.py`. Fixed by tracking, during
     the same scan, whether an explicit `-x` was already seen, and
     treating a later bare token as a source operand either way (suffix
     match OR context) -- an unrecognized flag's own non-path value with
     no preceding `-x` is still left untouched, preserving round 30's
     safe-by-default behavior.
  3. A pack persisted by a version of abicheck that predates
     `include_paths_explicit`/`system_include_paths_explicit` loads with
     both lists absent -- indistinguishable, by list length alone, from a
     directly-constructed `CompileUnit` that simply never populated
     per-entry provenance (every pre-round-31 test in this repo). Those
     two cases need OPPOSITE defaults: an old, previously-correct
     persisted pack was never rebased at all under the pre-round-30
     pipeline, so it must degrade to "unknown, do not rebase" (not
     "derived", which would newly rebase a path and silently change that
     pack's replay semantics) -- while a direct construction should keep
     the pre-round-30 "derived" (rebase) default every existing caller
     already assumed. Fixed with a new `CompileUnit.include_provenance_known`
     flag: `True` (the default for direct construction and every
     adapter-produced unit) trusts the paired lists at face value,
     falling back to "derived" only on an unexpected length mismatch;
     `from_dict()` sets it `False` only when loading a dict with NEITHER
     new provenance key present at all -- the actual legacy-schema
     signal -- forcing "unknown, do not rebase" for that pack regardless
     of the (necessarily empty) lists' contents.

  `_rebase_structured_path` (renamed `rebase_structured_path`, now shared
  by both header backends), `_fold_chdir_into_operands`,
  `is_absolute_path_token`, `normalize_path_token`, and `join_path_token`
  moved from `_argv.py` into `_argv_shortopts.py` to keep `_argv.py` under
  the 2000-line AI-readiness hard cap after these additions.

  `CompileUnit.to_dict()`'s new keys (`include_provenance_known`,
  `include_paths_explicit`, `system_include_paths_explicit`) drifted the
  four committed L3 example fixtures (`case152_enum_size_flag_flip`,
  `case153_struct_packing_flip`, `case154_lto_mode_flip`,
  `case155_char_signedness_flip`) against `scripts/gen_l3l4l5_examples.py`
  -- regenerated via that script (`--check` confirms no other case
  drifted; the diff is purely additive, no other fixture content
  changed).
