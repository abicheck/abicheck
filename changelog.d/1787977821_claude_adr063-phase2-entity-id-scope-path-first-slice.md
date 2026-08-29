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
  its own segment kind. `LocalToFunction.owner` is now the owning
  function's own `EntityId` rather than a bare string, so two overloads
  that each declare a same-named local no longer collide just because
  both share one bare function name. `entity_id_for_function`'s
  `is_extern_c` branch now returns `scope=()` regardless of the caller-
  supplied `scope`, matching `resolve_symbol_identity`'s own choice to
  base an extern-"C" identity on the raw export rather than a qualified
  name (evidence-tier scope availability varies for this case: an export-
  table-only snapshot cannot recover a namespace an `extern "C"` symbol
  was declared in). `param_types` in the signature-fallback branch are now
  canonicalized via `name_classification.canonicalize_type_name` before
  joining into `extra`, so CastXML's and Clang's differing spellings of an
  otherwise-identical parameter type no longer fragment identity across
  header-AST backends. `entity_id_for_function`'s `mangled` branch also
  now returns `scope=()` (matching `resolve_symbol_identity`'s real
  `f"mangled:{real_mangled}"` primary id, which folds in no scope at all)
  -- a genuine mangled name already fully encodes scope, so keeping a
  caller-supplied `scope` fragmented identity across evidence tiers that
  differ in whether they can supply one, the same problem the
  `is_extern_c` branch was fixed for. `entity_id_for_variable` gained the
  same `is_extern_c` parameter `entity_id_for_function` has, and its
  `mangled`/`is_extern_c` branches get the identical `scope=()` treatment
  -- closing the same gap for variables that had no linkage signal at all
  before this fix. Both constructors' `mangled` branch now also ignores
  `leaf_name` (using `""` instead of the caller-supplied value): a
  confirmed real code path, `dumper_elf_fallback.py`'s ELF-only fallback,
  constructs `Function`/`Variable` with `name=sym, mangled=sym` -- the raw
  exported symbol reused for both fields -- so a header/DWARF
  observation's demangled `name` and that export-only observation's raw
  name would otherwise disagree despite an identical, genuine mangling.
