### Added

- **`SemanticIR` now canonicalizes functions and variables, and populates on
  every header-AST platform** (ADR-063 Phase 6's third slice). Both
  header-AST backends' `castxml`/`clang` functions/variables project into
  `CanonicalEntity.canonical_spelling`, built from the same
  cross-backend-spelling primitives `entity_id_for_function`/
  `resolve_function_identity` already use
  (`canonicalize_function_signature_param_type` per parameter,
  `canonicalize_type_name` for the return/variable type), with
  `is_const`/`is_volatile` carried via `CanonicalEntity.cv_qualification`. A
  function whose mangled name is castxml's own synthetic constructor/
  destructor snapshot key (not a stable cross-backend identity, since a
  later hybrid-merge step may rewrite it to a real clang-matched one) is
  excluded; a compiler-generated function with a real mangled name (e.g. a
  synthesized `operator=`) is normalized like any other. An unresolved
  type is detected structurally (a substring test, since castxml composes
  its `"?"` placeholder into the enclosing spelling for a pointer/
  reference/array/cv-qualified pointee), not by exact-string match, and
  the same fix applies to the pre-existing typedef-underlying-type check.
  `dumper.py`'s PE and Mach-O dumps now populate `AbiSnapshot.semantic_ir`
  too (previously ELF-only), via a new shared choke point,
  `extract/header_ast_fields.parse_header_ast_fields`. No detector,
  verdict, or exit code changes — `SemanticIR` remains additive and unread
  by the existing pipeline.
