### Fixed

- **The castxml C-linkage export-evidence override now recognizes a real
  bare-name export regardless of which mangling scheme castxml's guessed
  attribute happens to use.** The override (used to recover `extern "C"`
  identity when castxml's own language-mode guess is ambiguous) was
  gated on the guessed mangled name starting with Itanium's `"_Z"`
  prefix. A Windows-targeting castxml decorates a guessed C-linkage
  function or variable with its own `"?...@@..."` prefix instead, so the
  override never fired there even though the real export table already
  confirmed the bare name — leaving a bogus MSVC-decorated symbol
  standing for a genuinely `extern "C"` declaration.
