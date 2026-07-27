<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: type-level findings no longer
  confirmed by a colliding symbol name** (no behavior change outside this
  still-unwired shadow module): `_in_surface_result_is_confirmed()`
  checked `sym in public_symbols` unconditionally, but
  `classify_change_surface()` never consults `public_symbols` for a
  type-level kind at all (exactly to prevent a type name coincidentally
  matching an unrelated exported function/variable of the same spelling —
  e.g. castxml representing an implicit same-named constructor unmangled).
  A type completely unknown to either snapshot (the classifier's own
  "cannot place it, keep it" fallback — not genuine confirmation) whose
  symbol happened to collide with an unrelated public function was
  therefore wrongly resolved to `IN_CONTRACT`. The symbol-universe check
  is now gated to non-type-level findings, mirroring
  `classify_change_surface`'s own gating.

- **Pack manifests: assignment keys are now canonicalized to plain `str`**
  (`compatibility_evaluation_packs.py`): a directly constructed `LoadedPack`
  could carry a mutable `str` subclass as an assignment *key* itself,
  aliasing the caller's object — mutating it after construction (if its
  `__eq__`/`__hash__` read mutable state) could change the field identity
  `detect_pack_conflicts()` consumes without changing `identity.sha256`.
  Mirrors the identical, already-fixed concern for assignment *values*.
  Both `_parse_field_assignments` and `_parse_policy_assignments` now
  reconstruct every accepted key as a plain `str`.
