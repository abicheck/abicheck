### Changed

- **ADR-063 Phase 2 (first slice), "`EntityId`/`ScopePath` as the one
  identity primitive"**: added `abicheck.model.identity`, a new leaf module
  defining the typed `ScopePath` segment vocabulary (`Namespace`, `Record`,
  `InlineNamespace`, `Anonymous`, `LocalToFunction`, each stating which of
  its own fields are identity and which are payload) and the corrected
  `EntityId` shape (`scope`, `kind`, `leaf_name`, `extra` -- never a bare
  `(scope, kind)`, which collides sibling declarations of the same kind in
  one scope). `EntityKind`/`ObservationKind` relocate from
  `storage.entity_ids` into this new module (domain vocabulary belongs in
  `model`, not the storage wire layer, per ADR-061's `storage -> model`
  import direction); `storage.entity_ids` now imports rather than redefines
  them, so exactly one `EntityKind`/`ObservationKind` exists in the
  repository. Purely additive and internal: nothing outside this module and
  its own tests constructs a `model.identity.EntityId` yet -- no parser,
  detector, or storage writer is wired to it in this slice. See the plan
  doc's own Phase 2 section for what remains (deriving a real `ScopePath`
  from parser scope-tracking state, and the still-open "carrier field vs.
  resolved on demand" design question that decision determines).
  `entity_id_for_function` accepts `is_extern_c`/`ref_qualifier`/
  `is_variadic` alongside `mangled_name`/`param_types`/`cv_qualifiers`, so
  its discriminator set matches `finding_identity.resolve_function_
  identity`'s: an `extern "C"` function (mangled_name absent, is_extern_c
  set) never differentiates by parameter list, and `C::f() &` vs.
  `C::f() &&` / `void f(int)` vs. `void f(int, ...)` no longer collide.
  `LocalToFunction` gained a required `block_ordinal` field alongside
  `owner`, so two same-named locals declared in sibling compound blocks of
  one function no longer collapse onto the same `EntityId` -- the same
  sibling-collision shape `Anonymous.ordinal` already closes, applied to
  its own segment kind.
