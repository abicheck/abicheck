### Fixed

- **Same-named types in different namespaces no longer collide in the
  snapshot type index** — `AbiSnapshot`'s type index now keys records on
  their qualified name (`algo_a::Parameter`, `algo_b::Parameter`) instead of
  the bare spelling header backends store in `name`. The misleading
  "Duplicate type names skipped (first-wins) ... Parameter (×2)" warning is
  gone; it now fires only for a genuinely duplicated qualified name.
  `AbiSnapshot.type_by_name()` resolves a qualified name exactly, and a bare
  name only when exactly one scope declares it — an ambiguous bare name
  returns `None` instead of whichever record was indexed first.
