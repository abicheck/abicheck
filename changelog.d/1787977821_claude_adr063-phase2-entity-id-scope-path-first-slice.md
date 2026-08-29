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
  `is_variadic` alongside `mangled_name`/`param_types`/`is_const`/
  `is_volatile`, so its discriminator set matches `finding_identity.
  resolve_function_identity`'s: an `extern "C"` function (mangled_name
  absent, is_extern_c set) never differentiates by parameter list, and
  `C::f() &` vs. `C::f() &&` / `void f(int)` vs. `void f(int, ...)` no
  longer collide.
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
  canonicalized (cross-producer spelling, via `name_classification.
  canonicalize_type_name`) before joining into `extra`, so CastXML's and
  Clang's differing spellings of an otherwise-identical parameter type no
  longer fragment identity across header-AST backends. `entity_id_for_function`'s `mangled` branch also
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
  A new `model.signature_normalization.
  canonicalize_function_signature_param_type` additionally drops a
  top-level BY-VALUE cv-qualifier from a parameter type (`"int"` and
  `"const int"` now canonicalize the same, per the C++ standard's own
  parameter-type-adjustment rule -- the qualifier plays no part in the
  function's type, and is consequently absent from a real ABI's mangled
  name too, e.g. under the Itanium C++ ABI this codebase targets) while
  deliberately leaving a *pointee* cv-qualifier on a pointer/reference
  parameter untouched (`"char *"` and `"const char *"` remain genuinely
  distinct overloads) -- narrower than `name_classification`'s existing
  `_strip_cv_qualifiers`/`func_signature_cv_only_differ`, which are
  permissive at the pointee level for a different, diff-reporting
  question and would have wrongly merged distinct overloads if reused
  here. A cv-qualifier trailing the parameter's own outermost pointer
  sigil (`int * const`) is also now correctly dropped as by-value, while
  an intermediate pointer level's own qualifier (`int * const *`) stays
  genuinely distinguishing. An array parameter is decayed to its adjusted
  pointer type first (`int []`/`int [3]`/`int [4]`/`int *` now all
  canonicalize identically, the bound plays no part in the real adjusted
  type); a genuinely multi-dimensional array (`int [3][4]`) is left as an
  accepted, documented, unchanged limitation, since correctly re-spelling
  its adjusted type needs declarator-rewriting this primitive doesn't
  implement. A parenthesized declarator (`int (*)[3]`, "pointer to
  array") is NOT decayed either (the trailing `[3]` there is the
  *pointee's* bound, not the parameter's own top-level shape) -- but,
  unlike the multi-dimensional case, it is not left fully untouched: the
  later, separate by-value cv-normalization step still treats its
  grouping parens as transparent and drops a cv-qualifier on its own
  outermost pointer the identical way (`int (* const)[3]` -> `int (*)[3]`
  in effect), since that qualifier is by-value regardless of the
  parenthesization. This whole primitive lives in its own new sibling leaf
  module, `abicheck/model/signature_normalization.py` (not
  `name_classification.py`, a frozen, no-growth legacy file under
  ADR-061's debt ledger; and split out of `model/identity.py` itself once
  it grew past the AI-readiness gate's 800-line production maximum),
  with its own dedicated primitive-level test file,
  `tests/test_signature_normalization.py`. `entity_id_for_function`'s
  `cv_qualifiers: tuple[str, ...]` parameter is replaced by `is_const: bool
  = False, is_volatile: bool = False` -- matching `resolve_function_
  identity`'s own two-boolean representation and eliminating a real
  qualifier-token-order-dependence bug by construction (`("const",
  "volatile")` vs. `("volatile", "const")` previously collided as two
  different ids for one identical member-cv qualification). The by-value
  cv fix now also treats a top-level `[` (array declarator) as pointer-
  shaped: a function parameter's array type always decays to a pointer
  (`int []` -> `int *`), so `void f(int[])`/`void f(const int[])` are
  distinct overloads and must not collapse. `entity_id_for_type`/
  `entity_id_for_enum` gained an opt-in `anonymous_ordinal: int | None =
  None` keyword, folded into `extra` only when `leaf_name` is empty: two
  anonymous sibling records/enums previously collided onto one `EntityId`
  regardless of which is meant, since `ScopePath` names only the
  containing scope and `Anonymous.ordinal` disambiguates a *descendant's*
  scope, not the anonymous declaration's own identity.
  `model.signature_normalization.canonicalize_function_signature_param_type`
  now also treats a parenthesized declarator's own grouping parens
  (`void (* const)(int)`, a callback parameter; `int (* const)[3]`, a
  pointer to an array) as transparent for by-value cv purposes -- the
  cv-qualifier on the declarator's own outermost pointer is by-value and
  dropped, exactly like an unparenthesized `int * const`, while a
  callback's own parameter types and a pointer-to-array's own trailing
  bound stay untouched. An unused, dead leftover private helper,
  `_has_top_level_ptr_or_ref`, was removed from this module.
  The same primitive now also recognizes a pointer-to-member-function
  declarator's qualified-name prefix (`void (C::* const)(int)`) as
  transparent the identical way, and recursively canonicalizes each
  parameter of a declarator's own trailing parameter list (a callback or
  member-function-pointer's parameters, to any nesting depth) -- so
  `void (*)(int)` and `void (*)(const int)` canonicalize identically,
  while a nested parameter's genuine pointee cv still distinguishes.
  A calling-convention keyword (`__cdecl`/`__stdcall`/`__fastcall`/
  `__thiscall`/`__vectorcall`) preceding a declarator's own sigil is now
  recognized too (kept verbatim, not erased), and a pointer-to-member-
  function's own TRAILING cv/ref-qualifiers (`void (C::*)(int) const`,
  distinct from the pointer's own by-value qualifier before the parameter
  list) are now preserved as the genuine, distinguishing type content they
  are -- only reordered for cv, never dropped, and a trailing `&&`
  ref-qualifier (which `canonicalize_type_name` itself spells `"& &"`) no
  longer corrupts the declarator's own sigil detection.
