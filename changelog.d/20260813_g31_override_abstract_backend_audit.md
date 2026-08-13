### Added

- **`Function.is_override` and `RecordType.is_abstract` are now populated by
  the direct-clang header backend (`--ast-frontend clang`/`hybrid`), not
  just castxml.** Both facts were castxml-only since their introduction,
  leaving the already-built `FUNC_OVERRIDE_SPECIFIER_ADDED`/
  `FUNC_OVERRIDE_SPECIFIER_REMOVED`/`TYPE_BECAME_ABSTRACT`/
  `TYPE_LOST_ABSTRACT` detectors permanently dead on any clang-parsed
  comparison. `is_override` is read from clang's `OverrideAttr` child node
  (whether the `override` keyword was written, matching castxml's own
  semantics exactly); `is_abstract` is read from clang's own
  `definitionData.isAbstract` (real semantic computation — a class that
  inherits an unoverridden pure virtual from a base is correctly abstract
  too, not just a direct-declaration check). Both detectors' producer gate
  moved from `both_castxml_backed_fact` to `both_known_backed_fact` now
  that the fact is genuinely cross-producer, matching `deprecated`'s
  earlier fix. Verified end-to-end against real compiled examples
  (castxml 0.7.0 + clang 18 + g++, conda-forge) before wiring this up.
