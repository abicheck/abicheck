### Added

- **`SemanticIR` now canonicalizes functions and variables, and populates on
  every header-AST platform** (ADR-063 Phase 6's third slice). Both
  header-AST backends' `castxml`/`clang` functions/variables project into
  `CanonicalEntity.canonical_spelling`, built from the same
  cross-backend-spelling primitives `entity_id_for_function`/
  `resolve_function_identity` already use
  (`canonicalize_function_signature_param_type` per parameter,
  `canonicalize_type_name` for the return/variable type), with
  a function's `is_const`/`is_volatile` carried via `CanonicalEntity.
  cv_qualification`, and a variable's own top-level `const`/`volatile`
  derived structurally from its type spelling (finding the last top-level
  pointer/reference sigil and reading qualifiers only after it, or from
  the whole string when there is none) rather than from the legacy
  `Variable.is_const` boolean, which conflates a mutable pointer to const
  data with a genuinely const declaration.
  Every function is normalized, including one whose mangled name is
  castxml's own synthetic constructor/destructor snapshot key: a later
  hybrid-merge step may rewrite such a key to a real clang-matched one, and
  that rewrite is now propagated into `semantic_ir` too
  (`dumper_hybrid._rewrite_semantic_ir_entity_ids`), so the merged snapshot
  never ends up with one representation keyed under the retired synthetic
  identity while another already carries the real one — the same treatment
  a pre-existing Mach-O mangled-name normalization gap needed and now gets.
  An unresolved type is detected structurally — depth-tracked over
  `()`/`[]`/`<>` so a real, resolved type that legally contains a literal
  `"?"` (e.g. clang's spelling of a dependent ternary inside a
  `decltype(...)`) is never mistaken for castxml's own unresolved-type
  placeholder, which the resolver only ever composes into the enclosing
  spelling for a pointer/reference/array/cv-qualified pointee at nesting
  depth zero — not by exact-string or plain-substring match, and the same
  fix applies to the pre-existing typedef-underlying-type check.
  castxml's own `_Atomic(...)` composition is a deliberate exception to
  plain depth-tracking (treated as a transparent wrapper), since it uses a
  real parenthesis pair as part of the resolver's own grammar to compose
  an unresolved wrapped type (`"_Atomic(?)"`), not an expression context.
  `dumper.py`'s PE and Mach-O dumps now populate `AbiSnapshot.
  semantic_ir` too (previously ELF-only), via a new shared choke point,
  `extract/header_ast_fields.parse_header_ast_fields`. No detector,
  verdict, or exit code changes — `SemanticIR` remains additive and unread
  by the existing pipeline.
  A variable's own top-level cv-qualification search also treats a
  declarator-grouping parenthesis as transparent (mirroring
  `signature_normalization.canonicalize_function_signature_param_type`'s own
  `_is_declarator_group` classifier), so a const function-pointer/
  pointer-to-array/pointer-to-member-function variable (clang's own spelling
  for `int (* const fp)(int)` is `"int (*const)(int)"`) reports its real
  top-level `const` instead of an empty tuple from a sigil hidden one
  nesting level too deep, and now recognizes a top-level `restrict` the
  same way — `CanonicalEntity.cv_qualification`'s vocabulary already names
  `restrict` alongside `const`/`volatile`, and clang's own variable
  qualType spells a restrict-qualified pointer verbatim (`"int *restrict"`
  for `int * restrict gp`), unlike castxml, which never emits the word by
  deliberate choice.

- **`SemanticIR` now also canonicalizes constants** (ADR-063 Phase 6's
  fourth slice). Both header-AST backends already attach a real `entity_id`
  to every public constant (`parse_constant_entity_ids()`, Phase 2), so this
  slice is wiring, not new identity work: `normalize_header_ast` gained
  `constants`/`constant_entity_ids` parameters (default `{}`) and projects
  each constant's raw `parse_constants()` value text verbatim as
  `canonical_spelling` — deliberately uncanonicalized, since (unlike a
  function/variable's type spelling, where a real cross-backend spelling
  disagreement is directly observed) there is no known cross-backend
  disagreement in constant-value spelling to canonicalize; this mirrors
  `diff_symbols._diff_constants`'s own long-standing `CONSTANT_CHANGED`
  detector, which has always compared the two backends' raw value strings
  with a plain `!=`. A constant carries no `cv_qualification`/
  `template_arguments`, since it has no captured type for either to
  describe. No detector, verdict, or exit code changes — `SemanticIR`
  remains additive and unread by the existing pipeline.
