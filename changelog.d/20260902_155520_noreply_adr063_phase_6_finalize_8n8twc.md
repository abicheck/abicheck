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
  compiler-synthesized implicit special member (a default/copy/move
  constructor, copy/move assignment, or destructor never written in the
  header) is excluded — clang's own AST walk never produces one at all, so
  including castxml's side would add a phantom occurrence with no
  cross-backend counterpart. `dumper.py`'s PE and Mach-O dumps now populate
  `AbiSnapshot.semantic_ir` too (previously ELF-only), via a new shared
  choke point, `extract/header_ast_fields.parse_header_ast_fields`. No
  detector, verdict, or exit code changes — `SemanticIR` remains additive
  and unread by the existing pipeline.
