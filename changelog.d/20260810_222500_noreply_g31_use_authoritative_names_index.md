### Fixed

- **Direct-clang vtable reconstruction's default-translation fix now uses
  the fully-merged names index instead of a locally hand-rolled shadow.**
  The eighth round's translation fix (see previous entry) kept its own
  local "first name seen for this qualname" copy, updated only at a
  qualname's very first sighting — which silently went stale whenever an
  INTERMEDIATE redeclaration was the one that actually named a
  previously-unnamed parameter (unnamed parameters, then a redeclaration
  that names them, then a further redeclaration that adds a dependent
  default). The real, fully-merged names index resolved correctly, but the
  stale local shadow still held the original unnamed placeholders, so the
  added default's translation target was empty and the raw, untranslated
  text was kept — mis-indexing a specialization and leaving an inherited
  vtable invisible. Fixed by reusing the already-correct, fully-merged
  names index directly as the translation target instead of re-deriving
  (and risking re-diverging) a second, independently-tracked copy.
