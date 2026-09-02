### Added

- **`SemanticIR` now canonicalizes functions and variables, and populates on
  every header-AST platform** (ADR-063 Phase 6's third slice). Both
  header-AST backends' `castxml`/`clang` functions/variables project into
  `CanonicalEntity.canonical_spelling`, built from the same
  cross-backend-spelling primitives `entity_id_for_function`/
  `resolve_function_identity` already use
  (`canonicalize_function_signature_param_type` per parameter,
  `canonicalize_type_name` for the return/variable type), with
  `is_const`/`is_volatile` carried via `CanonicalEntity.cv_qualification`.
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
  `dumper.py`'s PE and Mach-O dumps now populate `AbiSnapshot.
  semantic_ir` too (previously ELF-only), via a new shared choke point,
  `extract/header_ast_fields.parse_header_ast_fields`. No detector,
  verdict, or exit code changes — `SemanticIR` remains additive and unread
  by the existing pipeline.
