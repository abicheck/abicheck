### Fixed

- **A quoted literal (`'N'`) sharing a template parameter's spelling no
  longer gets mistaken for a reference to that parameter.** The
  rename-blind substitution used to canonicalize a dependent type
  spelling against a template's own parameter names rewrote a same-
  spelled character or string literal's own contents (e.g. `'N'` inside
  `Literal<'N'>`) into `type-param-0`, fingerprinting an unrelated,
  unused parameter's own rename as a remove+add for an otherwise-
  identical declaration.
