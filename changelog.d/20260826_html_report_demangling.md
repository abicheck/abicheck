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
