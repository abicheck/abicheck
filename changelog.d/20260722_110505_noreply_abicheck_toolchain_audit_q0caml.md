### Fixed

- **The C++20 dialect detector now requires positive evidence of a
  preceding `template<...>` header before treating "concept" as a genuine
  declaration**, not just the absence of a `::` prefix. Excluding only
  qualified references still misdetected a plain, unqualified pre-C++20 use
  of "concept" as an identifier (e.g. `static concept C = {};` with no
  template anywhere before it), forcing `-std=gnu++20` on a header that
  would otherwise have parsed correctly. The check now looks for the
  template header's closing `>` — on the same line, or as the last thing on
  the previous non-blank line when "concept" itself starts a line —
  including across a template parameter list wrapped over several physical
  lines.
