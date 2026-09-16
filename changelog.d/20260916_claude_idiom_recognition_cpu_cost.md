### Changed

- Idiom recognition (`abicheck/idioms.py`) no longer repeats work it can prove
  is redundant. `OPAQUE_POINTER`'s two record-local conditions (the definition
  must be incomplete, and no field may be `PUBLIC`) are now evaluated *before*
  the public-signature search rather than after it, so a complete record or one
  with public fields never triggers that search; both are pre-existing
  necessary conditions, so every tag, its evidence and its proof fields are
  unchanged. The pointer/cv-token strip is now memoised through a new
  dependency-free leaf, `abicheck/policy/type_spelling.py`, whose cache is
  bounded (4096 entries, spellings over 512 characters bypass it and are still
  normalised in full) and stores only plain strings, so it cannot extend the
  lifetime of any graph or snapshot. The existing *sequential* keyword
  substitutions are preserved exactly -- they are deliberately not folded into
  one combined alternation, which would change the result for valid spellings
  such as `Foo*const`.
