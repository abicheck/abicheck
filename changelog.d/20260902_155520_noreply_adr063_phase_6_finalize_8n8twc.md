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
  nesting level too deep, and restricts the trailing-qualifier scan to
  before a pointer-to-member-function's own trailing parameter list
  (reusing `_split_at_trailing_param_list`) so the pointed-to member
  function's own qualifier (`void (C::*)(int) const`'s `const`) is never
  wrongly attributed to the pointer variable itself. `restrict` is
  deliberately NOT recognized for a variable, even though `CanonicalEntity.
  cv_qualification`'s vocabulary names it alongside `const`/`volatile`:
  recognizing it via a plain text scan is backend-asymmetric (clang's
  qualType spells it verbatim; castxml's `type_name_uncached` never emits
  the word at all, by castxml's own deliberate choice) and made a
  castxml-produced entity claim a confirmed absence of a qualifier its own
  backend cannot see, which a hybrid merge then treated as a genuine
  disagreement against clang's real evidence instead of backfilling it. A
  correct fix needs a structural, reliability-tracked `Variable.is_restrict`
  fact populated by both backends the way `Param.is_restrict` already is —
  a model-shape decision for a future slice, not a normalizer-only change.

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
  A hybrid dump's merge now filters unmatched clang-only `CONSTANT`
  occurrences out of the merged `semantic_ir` too
  (`dumper_hybrid._drop_unmatched_constant_occurrences`), matching the
  pre-existing, deliberate decision to keep the legacy `constants` field
  castxml-only — without this, a clang-only constant surfaced through
  `semantic_ir` with no corresponding flat entry at all. A clang-produced
  compound-initializer constant (`dumper_clang_expr._expr_fingerprint`'s
  own build-stable structural fingerprint, not a spelling of the source
  text) is now marked `Fact.unsupported()` rather than `Fact.present(...)`,
  since that module's own docstring is explicit that cross-backend constant
  values are not expected to match for this case — publishing it as
  present made every unchanged compound constant report a spurious hybrid
  conflict.
  `_has_unresolved_component`'s depth tracker is now a bracket-KIND-aware
  stack rather than a flat counter: a real right-shift operator inside a
  parenthesized non-type template argument (`"S<(N >> 1 ? 1 : 2)>"`) is not
  two nested template closers, and a flat counter's per-character `>`
  decrement wrongly dropped the running depth to zero while still inside
  the parenthesized expression, misreading a resolved ternary's own `"?"`
  as the sentinel. A `">"` now only closes a template level when the
  innermost still-open bracket is itself a `"<"` (a genuine
  `vector<vector<int>>`-style double-close via one `">>"` still works
  correctly); otherwise it is left untouched as a real operator character.
  A top-level qualifier hidden behind a typedef alias (`typedef int *
  const ConstPtr; extern ConstPtr p;`) is a documented, accepted
  limitation rather than a fix attempted here: neither backend's `Variable`
  spelling resolves through the alias the way this text-based scan would
  need to, and a real fix needs new structural evidence (clang's
  `desugaredQualType`, castxml's typedef-following `resolve_cv_restrict`)
  threaded onto `Variable` as a new field — a model-shape decision for a
  future slice, the same conclusion already reached for `restrict`. Pinned
  by a dedicated regression test so a future fix has something that starts
  failing once it lands.
  A direct function-pointer parameter/variable/return type that castxml's
  resolver can only render as its own opaque `"FunctionType"` tag (no
  dedicated rendering exists for an anonymous function type, unlike
  `Struct`/`Class`/`Typedef`/... — the identical shape `idioms.
  _is_callback_type` already checks for elsewhere) is now `Fact.
  unsupported()` rather than `Fact.present(...)` — a different, more
  accurate status than the unresolved-type sentinel case, since the
  resolver ran and produced a real, final (if useless) answer. Publishing
  it as present made a hybrid merge report a spurious conflict against
  clang's real, useful spelling for an unchanged callback declaration.
  The clang compound-initializer fingerprint check now matches the FULL
  fingerprint shape (`"expr:"` plus exactly 16 lowercase hex digits, the
  same regex `diff_default_value_reliability._is_expr_fingerprint`
  already uses) rather than merely the `"expr:"` prefix — a plain prefix
  test also matched castxml's own raw, verbatim source-text initializer
  whenever it happened to spell a qualified name whose next component is
  literally `expr` (e.g. `"expr::NAMESPACE_VALUE"`), silently discarding
  real castxml constant evidence as if it were a clang fingerprint.
  The `FunctionType` opaque-tag check is now anchored to the WHOLE
  (cv/sigil-stripped) string and gated on `producer == "castxml"`, not a
  bare substring test — a naive `"FunctionType" in raw_type` also matched
  a real, legitimately-named type (`"MyFunctionTypeWrapper*"`) and fired
  for clang too, even though clang never emits this literal tag text.
  `has_unresolved_component`/`is_castxml_opaque_function_type`/
  `CLANG_EXPR_FINGERPRINT_RE` moved into a new sibling leaf module,
  `extract/semantic_normalizer_artifacts.py`, once their accumulated
  docstrings pushed `semantic_normalizer.py` past the AI-readiness gate's
  800-line cap for a new file.
  `_variable_top_level_cv_qualification`'s own sigil-finding scan gets the
  identical bracket-KIND-aware stack fix as `has_unresolved_component`: a
  real comparison `<` inside a parenthesized non-type template argument
  (e.g. clang's own `"S<(N < 0)> *const"`) no longer pushes a spurious
  bracket level that a later real `)` would then incorrectly pop instead
  of the paren it actually closes — previously this corrupted the running
  depth enough that the real top-level `*`/`const` were never found at
  all, silently reporting no qualification for a genuinely const pointer.
  `has_unresolved_component` gets the identical symmetric fix proactively,
  since it carries the same latent primitive weakness for the same shape.
  The opaque-`FunctionType` regex now also recognizes a cv-qualifier
  AFTER a pointer/reference sigil, not only before the tag — castxml
  renders a cv-qualified pointer VALUE (not pointee) as a suffix
  (`"FunctionType* const"`), which an earlier revision of the anchored
  regex missed, wrongly publishing it as present and conflicting with
  clang's real spelling for an unchanged const callback.
  A clang-produced boolean constant is also `Fact.unsupported()` rather
  than `Fact.present(...)`: clang's compound-initializer parser stringifies
  a captured Python `bool` with plain `str(...)`, spelling `"True"`/
  `"False"` instead of either backend's own real `true`/`false` source
  text, which made an unchanged boolean constant report a spurious hybrid
  conflict the moment clang's stringification diverged from castxml's
  genuine source-text capture. The decimal-integer/character/float-literal
  residual (e.g. `0x10` vs. `16` for an unchanged constant) remains a
  documented, accepted limitation, pinned by a regression test, since no
  structural signal in the value text alone distinguishes a normalized
  spelling from a real value difference — a correct fix needs a shared
  literal-grammar normalizer or a new per-backend original-token fact, a
  model-shape decision for a future slice.
  `tests/test_semantic_normalizer.py` split a second time, mirroring the
  production-code split above, once these two new tests pushed it past the
  AI-readiness gate's 1200-line cap for a new test file: the artifact-
  recognition-primitive tests now live in a new sibling file,
  `tests/test_semantic_normalizer_artifacts.py`.
