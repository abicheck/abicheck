### Fixed

- **Two more comparability-gate correctness bugs (Codex review): a
  malformed owned-header pair could masquerade as evidence of real growth,
  and an opaque scope-fingerprint mismatch was silently waived.**
  `_include_sequence_is_additive_owned_growth`'s `try`/`except TypeError`
  guard didn't catch a bare string member (e.g. `"xx"`) among owned-header
  pairs — strings are themselves iterable, so `tuple("xx")` silently
  succeeds as `("x", "x")` instead of raising, and if that happened to
  match (or sit alongside) a real pair, the set comparison could look like
  genuine additive growth even though the evidence was malformed. Fixed
  with a new `_is_owned_header_pair` validator requiring an exact
  two-element string list/tuple. Separately, the scope-side carve-out
  called `_scope_field_is_additive_superset` over every `SCOPE_FIELD_KEYS`
  entry rather than only the ones that actually differ — since that
  function returns `True` on an unchanged field, an entirely-unchanged
  known-field set always trivially satisfied the carve-out, so a
  deserialized/externally-constructed contract carrying an opaque
  `scope_fingerprint` that doesn't match its `scope_fields` (nothing
  recognized explains the mismatch at all) was silently waived through.
  Fixed by requiring at least one recognized field to genuinely differ
  before the carve-out can apply, mirroring the profile side's equivalent
  opaque-mismatch fix from the previous round.
