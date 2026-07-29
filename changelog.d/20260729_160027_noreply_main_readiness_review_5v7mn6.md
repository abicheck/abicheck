### Fixed

- **A cross-compiler binding whose invoked name embeds a dotted target-triple
  OS version (e.g. `x86_64-pc-solaris2.11-gcc`) no longer shadows the real
  `compiler_version`.** `_extract_version_token` picked the *first* dotted
  number in the `--version` banner, which previously worked because a
  cross-compiler prefix's own digits were bare (`x86_64` → `86`, `64`); a
  target-triple OS version embeds a genuinely dotted number ahead of the
  real version (`solaris2.11`'s `2.11`), so `x86_64-pc-solaris2.11-gcc (GCC)
  13.2.0` extracted `"2.11"` and rejected a valid `>=13,<14` constraint.
  Now restricted to the banner's first line (the real version is always
  there; later lines are copyright/warranty boilerplate) and takes the
  *last* dotted match on that line, since the real version is conventionally
  the last token after any invoked-name prefix and parenthetical
  package/build descriptor.
