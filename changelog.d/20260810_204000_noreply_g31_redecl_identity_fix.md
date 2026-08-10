### Fixed

- **Direct-clang vtable reconstruction no longer corrupts a template's
  redeclaration identity when an unrelated declaration happens to have
  equal metadata.** `_register_template_param_metadata` (used to resolve
  a class template specialization's base for direct-clang vtable
  reconstruction) previously advanced its tracked `previousDecl`-chain
  identity whenever a later registration's kind/default/name metadata
  merely happened to *equal* the stored value — even when the two
  declarations were genuinely unrelated (e.g. two different explicit
  outer specializations' own same-named nested member templates). A
  legal out-of-class redeclaration of the FIRST entity, with renamed
  parameters, could then have its real `previousDecl` link mismatch the
  corrupted tracked id and get wrongly dropped as ambiguous, leaving a
  dependent default unresolved and an inherited vtable invisible — hiding
  a real virtual-method addition as `NO_CHANGE`. Fixed by only ever
  advancing the tracked identity on a confirmed same-entity link (the
  first registration, or a matching `previousDecl`), never on a bare
  coincidental value match.
