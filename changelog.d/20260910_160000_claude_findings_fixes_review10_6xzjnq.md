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
