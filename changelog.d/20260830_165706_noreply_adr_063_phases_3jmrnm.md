### Fixed

- **`resolve_function_identity`'s NORMALIZED-tier signature no longer treats
  a top-level by-value `const`/`volatile` parameter qualifier as a distinct
  overload** (ADR-063 Phase 2, "finding_identity.py algorithm migration").
  For a function with no real mangled name (a DWARF-only snapshot, or a
  non-`extern "C"` C-linkage declaration), the per-parameter
  canonicalization used to build its identity now delegates to
  `model.signature_normalization.canonicalize_function_signature_param_type`
  -- the same primitive `model.identity.entity_id_for_function`'s own
  signature-fallback branch already uses -- instead of a second,
  independently-maintained `canonicalize_type_name`-only pass. Per the C++
  standard, `void f(int)` and `void f(const int)` name the same function;
  before this change, one function's before/after pair that merely
  gained or lost a top-level by-value `const` on a parameter fragmented
  into two distinct identities. A *pointee* cv-qualifier (`char *` vs.
  `const char *`) remains a genuine, independently-mangled overload
  discriminator and still distinguishes.
- **`canonicalize_function_signature_param_type` no longer crashes `compare`
  with an uncaught `RecursionError` on a pathologically nested callback
  parameter type.** A hand-crafted or corrupted snapshot's parameter-type
  string (e.g. hundreds of levels of nested function-pointer declarators)
  could exhaust Python's call stack and abort the whole comparison. A
  generous, bounded recursion depth now falls back to leaving the
  offending fragment unnormalized instead of crashing; no real,
  non-adversarial C++ signature is affected.
