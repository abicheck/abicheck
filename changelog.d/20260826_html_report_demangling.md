### Fixed

- **The HTML report never actually demangled C++ symbols, despite
  appearing to support it.** `html_report._symbol_cell` read a
  `demangled_symbol` attribute that `Change` (the real production
  dataclass) never sets, so it silently fell back to the raw mangled
  symbol every time; the Description column's own embedded mangled
  names were never touched at all. `generate_html_report` now accepts
  a `demangle` parameter (default `True`) and demangles both the Symbol
  and Description columns for the native (non-`compat_html`) report --
  always running `demangle.demangle_text()` *before* `html.escape()`,
  so a demangled signature's own `<`/`>`/`&` (from a template argument)
  are escaped like any other text rather than injected raw. `--demangle`/
  `--no-demangle` now covers `--format html` the same way it already
  covered markdown/review; `--format json`/`sarif`/`junit` are
  unaffected and keep raw mangled symbols for downstream tooling.
- **Follow-up (Codex review, two findings).** The "Not Evaluated
  (Contract)" table (`_build_sections_html`) rendered `change.symbol`
  directly, bypassing `_changes_table`'s demangling entirely -- both
  the new default and an explicit `--demangle` left those symbols
  mangled. It now uses the same `_symbol_cell` rendering as every other
  section. Separately, a report with many distinct C++ symbols called
  `demangle_text` once per row, which -- when the fast in-process
  `cxxfilt` package isn't installed -- meant a fresh `c++filt`
  subprocess per row; `demangle.prewarm_demangle_batch` now batches
  every symbol/description across the whole report into one upfront
  `demangle_batch()` call, so every later per-row call is a cache hit.
- **Docs follow-up (Codex review):** `docs/contribute/config-key-review.md`
  still stated twice that HTML output always stays mangled, contradicting
  the new default. Corrected to describe HTML's actual, safe structural
  demangling (`<abbr title="mangled">demangled</abbr>`, both sides
  `html.escape`d).
- **Known gap, investigated and documented rather than fixed here
  (Codex review):** this module's `demangle()`/`demangle_batch()` only
  recognize Itanium-mangled (`_Z...`) names -- an MSVC-decorated PE/COFF
  export (`?run@Foo@@QEAAXXZ`) is never demangled anywhere in this
  codebase. Confirmed against real `c++filt` (GNU Binutils): its own
  `-s {none,auto,gnu-v3,java,gnat,dlang,rust}` format list has no MSVC
  entry, and `cxxfilt` (a binding to the identical libstdc++
  `__cxa_demangle`) is equally Itanium-only. Real MSVC demangling needs
  either the Windows-only `undname`/`dbghelp.dll` or a third-party
  pure-Python MSVC demangler package -- a new runtime dependency this
  deliberately lightweight tool has no other reason to carry. Documented
  in `demangle.py`'s own module docstring.
- **Second follow-up (Codex review): `--demangle` still left a finding's
  `old_value`/`new_value`/`affected_symbols` mangled.** `_changes_table`
  only ever applied `demangle_text` to the description and primary symbol
  cell, so a finding carrying mangled names in `old_value`/`new_value`
  (as `buildsource/crosscheck.py` does) or `affected_symbols` (as
  `diff_cpp_patterns.py` does) could show a demangled Symbol column right
  next to a still-mangled Affected list or value pair for the identical
  name. All three are now demangled the same way, before `html.escape`,
  and folded into `prewarm_demangle_batch`'s batch call.
- **Third follow-up (Codex review): `appcompat_html.appcompat_to_html`
  rendered its own Relevant/Irrelevant Changes tables via the same
  `_changes_table`/`_symbol_cell` helpers without ever calling
  `prewarm_demangle_batch` first**, so an appcompat report with many
  distinct C++ symbols paid a fresh `demangle_text`/`demangle_batch`
  call per row instead of one batched upfront call for the whole
  report -- the identical gap `generate_html_report` already closed,
  just left open on this second entry point into the shared table
  renderer.
- **Fourth follow-up (Codex review): `appcompat_html`'s Missing Symbols
  table rendered `change.symbol`-equivalent raw mangled names directly**,
  bypassing `_changes_table`/`_symbol_cell` entirely -- so a report could
  show a demangled `Foo::bar()` in the Symbol column of a change row while
  the arguably more important missing-linker-symbol list right above it
  still showed the raw `_ZN3Foo3barEv`. Now demangled the same way
  (`demangle_text` before `html.escape`) and folded into the same batch
  prewarm via a direct `demangle_batch` call, since `missing_symbols` is a
  plain list of raw strings rather than change objects
  `prewarm_demangle_batch`'s attribute-based extraction can read.
- **Fifth follow-up (Codex review): the Missing Symbols fix above replaced
  the raw text outright, losing distinguishability between two
  ABI-distinct mangled names that happen to demangle identically** --
  `_ZN3FooC1Ev` (the complete-object constructor) and `_ZN3FooC2Ev` (the
  base-object constructor) both demangle to `Foo::Foo()`, so a report
  with both in `missing_symbols` showed two identical, indistinguishable
  rows with the exact linker names gone entirely. A new
  `_missing_symbol_cell` helper mirrors `_symbol_cell`'s own contract: the
  demangled text is shown with the raw mangled name preserved as an
  `<abbr>` tooltip.
- **Sixth follow-up (Codex review): the identical ambiguity in
  `_changes_table`'s own `affected_symbols` list.** A finding whose
  primary symbol is itself a placeholder (`<isa:...>`,
  `<sycl_overload_family>`) has no per-row `<abbr>` tooltip to recover a
  colliding demangled name from, so two ABI-distinct affected symbols
  (again, C1/C2 constructor variants) collapsed into duplicate,
  indistinguishable text. `_symbol_cell`'s rendering logic is now shared
  via a new `_abbr_symbol_text` helper, used by both `_symbol_cell` and
  the `affected_symbols` loop.
- **Seventh follow-up (Codex review): the same ambiguity in
  `old_value`/`new_value`.** A `SOURCE_TO_BINARY_MAPPING_CHANGED` finding
  changing between two ABI-distinct linker names that demangle
  identically rendered both value cells as identical text with neither
  exact linker name recoverable -- the row's own primary symbol is the
  source declaration label, not either mangled value, so there's no other
  tooltip to fall back on. `old_val`/`new_val` now render through the
  same `_abbr_symbol_text` helper instead of a bare `demangle_text` +
  `html.escape`.
- **Eighth follow-up (Codex review): `write_html_report()` had no way to
  opt out of demangling.** Unlike the CLI's own `--no-demangle`, this
  Python-API writer always called `generate_html_report()` with the
  implicit `True` default, so a caller needing raw linker names in the
  written file had no equivalent knob. `write_html_report()` now accepts
  and forwards a keyword-only `demangle` parameter (default `True`,
  matching `generate_html_report`'s own default -- no behavior change for
  an existing caller that omits it).
- **Ninth follow-up (Codex review): the identical gap on the appcompat
  entry points.** `appcompat_to_html()`/`write_appcompat_html()` had no
  `demangle` parameter either, so a Python-API caller of the appcompat
  report had no way to opt out of demangling at all. Both now accept a
  keyword-only `demangle` parameter (default `True`), threaded into the
  prewarm calls, `_changes_table`, and `_missing_symbol_cell`.
- **Tenth follow-up (Codex review): the Impact Summary table
  (`--report-mode impact` / `show_impact=True`) never demangled its own
  Root Change column.** `_build_impact_html` rendered `change.symbol`
  directly via `html.escape`, bypassing the demangling setting entirely --
  the normal change table right above it demangles the identical symbol,
  so the same root read as `Foo::Foo()` in one table and the raw
  `_ZN3FooC1Ev` in the other on the same page. Now rendered through the
  same `_abbr_symbol_text` helper, with `demangle` threaded through.
- **Eleventh follow-up (Codex review): a Mach-O clang-derived symbol
  demangled with a stray leading underscore.** clang's own `mangledName`
  carries the platform global-symbol prefix on macOS (`__ZN3Foo3barEv`,
  not the plain ELF `_ZN3Foo3barEv`) -- `demangle()`/`demangle_batch()`
  required a bare `_Z` prefix and never recognized this doubled-underscore
  spelling, and the free-form-text token regex matched only its `_Z...`
  suffix, so a demangled occurrence in report text kept the extra leading
  underscore glued on (`_Foo::bar()` instead of `Foo::bar()`). Both
  functions, and the shared `_MANGLED_TOKEN_RE`, now recognize either
  spelling, canonicalizing to the plain `_Z...` form before invoking
  cxxfilt/c++filt (neither backend recognizes the doubled-underscore form)
  while keying results by the original symbol so callers see no change in
  lookup behavior. Fixed in the shared `demangle.py` module rather than in
  the HTML report specifically, since every consumer of `demangle()`/
  `demangle_text()` across the codebase (DWARF export matching, appcompat
  symbol matching, detector logic) shared the identical gap.
- **Twelfth follow-up (Codex review): the Mach-O fix above could read a
  `c++filt` echo-back as a real demangling for a malformed name.** GNU
  `c++filt` exits 0 and simply echoes back its input when it can't
  demangle a name; the batch and single-symbol `c++filt` fallbacks
  compared that echo against the *original* (possibly Mach-O-prefixed)
  symbol rather than the *canonical* form actually sent to the
  subprocess, so a malformed `__Z...`-looking token (not real Itanium
  mangling) silently "succeeded" -- `demangle_batch(["__ZNOTVALID"])`
  returned `{"__ZNOTVALID": "_ZNOTVALID"}` instead of `{}`. Both
  comparisons now check against the canonical input; the in-process
  `cxxfilt` batch path had the identical comparison gap (a `d != s`
  check against the original symbol) and is fixed the same way.
- **Thirteenth follow-up (Codex review): the single-symbol in-process
  `cxxfilt` path had no comparison at all.** `demangle()`'s direct
  `cxxfilt.demangle(canonical)` call returned unconditionally, unlike the
  batch `cxxfilt` path fixed above -- some `cxxfilt`/`__cxa_demangle`
  versions return the input unchanged on failure rather than raising, so
  `demangle("__ZNOTVALID")` returned the bogus `"_ZNOTVALID"` instead of
  `None` whenever `cxxfilt` (rather than `c++filt`) handled the malformed
  name. Now compares against `canonical` the same way the batch path does.
- **Fourteenth follow-up (Codex review): a missing `c++filt` binary was
  re-discovered on every call instead of once per process.** Neither the
  single-symbol nor the batch `c++filt` fallback remembered a
  `FileNotFoundError` beyond the call that hit it, since the existing
  `_BATCH_CACHE_FAIL` is deliberately never populated for that case (a
  timeout or non-zero exit might be transient/input-specific and is worth
  retrying, per that code's own comment) -- but a genuinely *missing*
  binary won't reappear mid-process, so a large HTML report with no
  demangler installed re-launched a fresh, doomed `subprocess.run()` pair
  per row instead of degrading once. A new `_cppfilt_binary_confirmed_
  missing` module flag, set on the first `FileNotFoundError` and checked
  before every later subprocess attempt, closes this without touching the
  existing (correct) retry semantics for a timeout/non-zero-exit/OSError.
- **Fifteenth follow-up (Codex review): the Mach-O fix treated every
  `__Z...`-prefixed symbol as Mach-O-mangled, everywhere.** `demangle()`/
  `demangle_batch()` are also used for correctness-critical symbol
  matching (`debian_symbols.py`'s Debian `.symbols` file generation,
  `dwarf_snapshot.py`, `appcompat.py`), all of which are ELF/PE-specific,
  not Mach-O -- a literal ELF export coincidentally named like a
  Mach-O-prefixed Itanium mangling (e.g. a hand-written assembler alias)
  would have been silently misdemangled. Both functions now accept a
  keyword-only `accept_macho_prefix` parameter (default `False`,
  restoring the strict, unambiguous pre-Mach-O-fix behavior for every
  existing caller); only `demangle_text()`/`prewarm_demangle_batch()` --
  used exclusively by the HTML/Markdown report renderers, with no
  matching-critical caller to put at risk -- opt in. The gate runs before
  any cache lookup, so a permissive caller's cached result can never leak
  into a stricter caller's answer for the same symbol.
- **Sixteenth follow-up (Codex review): a non-mangled symbol containing an
  embedded valid Itanium substring was corrupted.** `_abbr_symbol_text()`
  (the shared helper behind every symbol/value/affected-symbol cell) called
  `demangle_text()` -- built for scanning free-form prose for an embedded
  mangled token -- on a field that is itself always exactly one symbol, not
  prose. A real, non-mangled export whose name happens to contain a
  complete, valid Itanium mangling as a substring (e.g. the legal C
  identifier `prefix_Z3foov`, which embeds `_Z3foov` -> `foo()`) rendered
  as the bogus `prefixfoo()`, with the real export name surviving only in
  the `<abbr>` tooltip. `_abbr_symbol_text()` now demangles the *whole*
  string via `demangle()` directly, which requires the entire value to be
  valid Itanium mangling rather than merely containing a substring that
  is; `appcompat_html._missing_symbol_cell()` (which had the identical
  duplicated logic) is now a thin alias for it. The description field
  deliberately keeps `demangle_text()`'s embedded-token scan, since it is
  genuine prose that can legitimately name a mangled symbol inline.
- **Seventeenth follow-up (CodeRabbit review): the Missing Symbols prewarm
  didn't accept the Mach-O prefix.** `appcompat_to_html()`'s own
  `demangle_batch(list(missing))` call (used to batch-warm the cache before
  `_missing_symbol_cell()` -- now `_abbr_symbol_text()` -- renders each
  row) omitted `accept_macho_prefix=True`, unlike `_missing_symbol_cell()`
  itself, so a report with many distinct missing Mach-O `__Z...` symbols
  warmed none of them, falling back to one `demangle()` call per row
  instead of the single batched call this prewarm exists for. Now passes
  `accept_macho_prefix=True`, matching the cell it warms for.
- **Investigated, not fixed (Codex review): a report renderer has no
  binary-format context to gate `accept_macho_prefix` on.**
  `demangle_text()`/`prewarm_demangle_batch()` -- the two functions behind
  every human-readable report renderer (HTML and Markdown/review alike) --
  unconditionally opt into the Mach-O `__Z...` prefix, regardless of
  whether the `DiffResult` being rendered actually came from a Mach-O
  binary. A literal ELF/PE export coincidentally spelled like a
  Mach-O-prefixed Itanium mangling (e.g. a hand-written assembler alias
  using a reserved double-underscore identifier) would render with a
  fabricated demangled label. Not fixed here: neither `DiffResult` nor
  `LibraryMetadata` carries a binary-format field today (`evidence_tiers`
  records evidence *sources* -- `elf`/`dwarf`/`header` -- not container
  format), so a correct fix needs new model plumbing threaded through
  every report-renderer call site, not a one-line change to this module.
  The blast radius is bounded in the meantime, but not identically across
  formats: HTML always preserves the raw mangled/literal name verbatim as
  the `<abbr>` tooltip, while `service_render.render_output()` runs
  `demangle_text()` over the *entire* rendered Markdown/review document
  as a plain in-place substring replacement, with no tooltip or residual
  copy of the raw text -- a false-positive substitution there is a
  straight loss of the original spelling, not a cosmetic label next to
  it (Codex review, fresh evidence, correcting this entry's own earlier
  overstatement). In neither case does it affect any `ChangeKind`,
  verdict, or exit code -- this is a human-readable rendering path only,
  never a correctness input downstream of the report. `json`/`sarif`/
  `junit` output is untouched by any of this and always carries the raw
  symbol. Left as a documented residual per this repo's "known gaps over
  risky reactive patches" convention (AGENTS.md) rather than a same-PR
  structural change to add binary-format context to the report pipeline.
