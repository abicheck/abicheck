<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Pack manifests: an aware `datetime`'s `tzinfo` is now snapshotted to an
  immutable, fixed-offset `datetime.timezone`** (`compatibility_evaluation_packs.py`):
  a directly constructed `LoadedPack` assignment carrying a custom, mutable
  `tzinfo` implementation (`utcoffset()` reading mutable instance state)
  previously kept that same `tzinfo` object aliased into `pack.assignments`
  -- mutating it afterward changed the stored value's effective equality
  without changing `identity.sha256`, letting two initially-agreeing packs
  flip into a spurious `PackConflictError` (or vice versa). Fixed by
  reconstructing the `tzinfo` as `datetime.timezone(value.utcoffset())`,
  which preserves this specific instant's exact comparison semantics while
  being immutable and independent of the original object. A `tzinfo` that
  doesn't report a UTC offset is rejected outright, consistent with this
  module's existing "reject rather than silently produce an ambiguous
  value" rule.

- **ADR-049 Phase 3 shadow evaluator: a confidently public-header
  declaration is now recognized as `IN_CONTRACT`, not left `UNKNOWN_UNRESOLVED`**
  (no behavior change outside this still-unwired shadow module):
  `REASON_NOT_EXPORTED` (a symbol declared but not ELF-exported, e.g. an
  inline or explicitly hidden-visibility function) is correctly *weak*, not
  terminal, per ADR-049 D2's `public` domain including "declared-public
  providers" independent of export status -- but a bare "weak, so stay
  unresolved" treatment under-claimed exactly the case D2 names: a
  declaration whose authoritative-side origin *is* confidently
  `PUBLIC_HEADER` is genuine "declared public" proof, not merely "we
  couldn't tell." Fixed by checking the authoritative side's origin
  directly when `REASON_NOT_EXPORTED` is reached, resolving to
  `IN_CONTRACT` when it's confidently `PUBLIC_HEADER` and falling back to
  the weak default only when it's genuinely unknown.
