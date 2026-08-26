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
