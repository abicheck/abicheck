### Fixed

- No functional change. An earlier revision of this fragment described a
  new `surface.classify_change_surface` demotion,
  `REASON_STDLIB_INTERNAL_CLOSURE`, for a stdlib/runtime symbol
  instantiated over a caller-supplied lambda closure (e.g. a
  `std::call_once` guard). That change has been reverted after review
  (Codex, two findings): (1) the reasoning behind it — "no consumer's
  *source code* could ever name this exact mangled symbol, so removing it
  is safe" — conflates *source-level nameability* with *binary/ABI
  compatibility*. A consumer's own object code does not need to name a
  symbol in source to depend on it: a template instantiated from a public
  header (as this shape always is) can produce the *identical* mangled
  symbol in a consumer's own translation unit via vague/weak linkage, and
  that consumer can still be depending on the library's copy being
  resolvable. This is the same shape of mistake `AGENTS.md`'s
  "linkage-blind removal" entry documents being attempted and reverted
  twice already for a differently-shaped fix — a library-snapshot-only
  view cannot establish that a real consumer already carries its own copy.
  (2) Separately, the fix was reachable only through
  `classify_change_surface`'s own unit-level entry point, never through
  the production `compare`/`scan` pipeline for the ELF-only case it was
  written for: `post_processing.FilterNonPublicSurface.run` returns
  unmodified changes before ever calling this classifier when neither
  side's surface is resolvable, so the new demotion was dead code for its
  own motivating scenario. Given (1) alone rules out a sound fix with the
  evidence available (a two-snapshot comparison, no consumer-side
  information), this item is left open rather than shipped unsound;
  documented as a known gap in `AGENTS.md`.
