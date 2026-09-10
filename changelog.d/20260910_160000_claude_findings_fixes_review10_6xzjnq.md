### Fixed

- **A vtable/RTTI-owner public-surface classification for a bare Itanium
  standard-substitution owner (`std::string`, `std::allocator`, ...) was
  demangler-implementation-dependent, not just demangler-*presence*-dependent
  as previously assumed** — GNU's demangler renders `Ss` as the fully-spelled
  `std::basic_string<char, std::char_traits<char>, std::allocator<char> >`,
  while LLVM's demangler (the backend macOS CI actually reaches once the
  `cxxfilt` package's libstdc++-only binding fails to load) renders the
  identical substitution with a different spelling, so the same comparison
  classified differently on two demangler-equipped hosts. Fixed by resolving
  the six Itanium ABI standard substitutions (`Ss`/`Sa`/`Sb`/`Si`/`So`/`Sd`)
  structurally in `model/mangled_name.py`, the same way the existing `St`
  scope-prefix substitution already is — never through the external
  `demangle()` fallback for this shape at all, closing the real macOS CI
  regression and the underlying host-dependence gap together.
- **A bare owner tail from a vtable/RTTI/VTT finding's type-candidate
  resolution could inherit the reachability of an unrelated, same-named
  type when its own qualified form was never modeled.** `surface.py`'s
  `_classify_type_level` now discards a bare-tail candidate when its
  qualified sibling was supplied but doesn't match anything in a snapshot
  that demonstrably tracks qualified type names elsewhere, so an unrelated
  bare `Wrapper` record can no longer stand in for an unresolvable
  `ns::Wrapper` owner and wrongly demote a genuine break.
- **A project-config (`.abicheck.yml`) `policy.overrides` downgrade never got
  the `HIGH RISK` warning an equivalent explicit `--policy <file>` downgrade
  already gets**, across every route that folds the project-config tier
  (scalar `compare`, `scan --against`, the stored-bundle-facts dispatcher,
  and the `compare-release` bundle/matrix paths). Fixed by warning on
  exactly the override kinds each fold's own delta adds, never re-warning
  about a kind an explicit file or `--pack` already claimed (and already
  warned about).
- **`CompatibilityPolicyConfig.pack_overrides` was neither validated as a
  real subset of `overrides` nor preserved across a resolved-config JSON
  round trip.** A direct caller could previously record a pack contribution
  no selected pack actually supplied, and `contract_context_io.py`'s codec
  silently dropped every pack-contributed override's provenance on
  deserialize. Both are now fixed: construction rejects a `pack_overrides`
  entry that isn't value-equal to `overrides`, and the codec serializes/
  reconstructs `pack_overrides` as its own lossless partition.
- **A project-config-contributed `policy.overrides` entry's `SelectedByEntry`
  omitted its own `.abicheck.yml` digest** when a selected pack also
  contributed a different override kind, leaving that entry as the only
  (and unverifiable) provenance record for the project-sourced kind.
- A round-8 test asserting the demangler-fallback fix patched the wrong
  module binding (`abicheck.demangle.demangle` instead of
  `abicheck.surface.demangle`, the name `surface.py` actually binds), so it
  did not verify that a structurally-parseable owner genuinely avoids
  demangling.
- **The bare-owner-tail fix above (this round's second item) regressed
  `compare`'s scaling on a large union-churn corpus** (CI's performance
  gates caught a real +48% wall-time regression and a quadratic tail-scaling
  exponent): it recomputed a scan over every modeled type name on *every*
  single type-level classification, instead of once per comparison. Fixed
  by hoisting that computation into `SurfaceUnions` (built once per surface
  pair and already reused across every finding), restoring the intended
  linear scaling.
- **`CompatibilityPolicyConfig.pack_overrides` accepted a raw string equal
  to a `Verdict`'s own value** (e.g. `"BREAKING"`) as if it were a real
  `Verdict` member, since `Verdict` is a `str, Enum` subclass and the
  subset check's `==` comparison could not tell the two apart — the value
  then reached `resolved_config_to_dict()`'s `.value` access and raised
  `AttributeError` instead of failing at construction. Construction now
  requires every `pack_overrides` value to be a real `Verdict` instance.
- **A persisted `evaluation_context`'s `policy.pack_overrides` read
  `null`/absent identically**, silently discarding real pack provenance for
  a present-but-malformed `null` value instead of failing loudly the way
  every other decoder in `contract_context_io.py` does. Only a genuinely
  *absent* key now defaults to empty; `EVALUATION_CONTEXT_SCHEMA_VERSION`
  is bumped to 4 and the key is required outright at/above that version.
- **A directory/package `compare`'s per-library `compatible_additions`/
  `quality_issues` counts disagreed with the scalar `compare` report's own
  split for the identical comparison** — the release path (and the
  PR-comment renderer reading its JSON) still used the old, raw-kind
  `ADDITION_KINDS` formula the scalar report's `compatible_additions`
  schema-4.0 correction had already moved away from. Both release call
  sites now call `report_summary.build_summary` directly, the same
  function the scalar report uses, so the two paths can no longer drift.
- **`surface.py`'s bare-owner-tail confirmation tested a candidate's
  qualified form only against `all_types`, which castxml/clang records never
  populate with their qualified spelling** (that identity lives separately
  in `origin_by_qualified_key`) — so a real, modeled-but-private owner was
  wrongly treated as unresolvable ("unknown, keep") instead of being
  confidently demoted, whenever an unrelated qualified name elsewhere in the
  snapshot pair happened to make qualification-tracking active. The
  confirmation check now also consults `origin_by_qualified_key` directly.
- **`ProjectCompatibilityInputs.policy_overrides` retained a caller's
  mutable dict by reference despite the dataclass being frozen**, so
  mutating the caller's dict (or a later resolution reusing the same
  input object) could silently change what an already-constructed input
  resolved to. Now copied into a real `MappingProxyType` on construction.
- **The `pack_overrides` `null`-coercion fix above was too narrow**: the
  underlying `.get(key, {})` fix already covers every non-mapping shape
  (`[]`, `false`, `""`, a plain string), not just `null` — the fix is
  unchanged, generalized with a parametrized regression test covering all
  five malformed shapes instead of only the one CodeRabbit reported.
