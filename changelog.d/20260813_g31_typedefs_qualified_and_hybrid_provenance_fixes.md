### Added

- **`AbiSnapshot.typedefs_qualified` (schema v25)**: both header backends
  now also emit a fully-qualified-name-keyed twin of `typedefs`. The
  existing `typedefs` dict is keyed by *bare* (unqualified) name, so two
  distinct member typedefs sharing a bare spelling in different
  classes/namespaces (e.g. two unrelated `value_type` member aliases — a
  common STL-container-shaped pattern) silently collided: whichever
  declaration a backend visited last won, and the other's aliasing
  information was dropped from the snapshot entirely with no way to
  recover it downstream. `typedefs` itself is unchanged (same lossy
  behavior, for full backward compatibility); `type_reachability.py`'s
  stdlib-reference scan now also consults `typedefs_qualified`, closing a
  real false-negative where a public signature spelled with the
  previously-lost qualified alias could miss a reachable `std::` field
  entirely.

### Fixed

- **Hybrid-merge provenance gaps for `Function.is_override`/
  `RecordType.is_abstract`** (Codex review on the G31 Phase C backend-audit
  PR, three rounds): a method or type present *only* on the clang leg of a
  hybrid (`--ast-frontend hybrid`) snapshot pair got no provenance stamp
  for these two facts, so `both_known_backed_fact` saw no recorded
  producer and silently declined to compare a real
  override-specifier/abstractness transition — even though both facts are
  genuinely clang-sourced for a clang-only declaration. The first fix
  added the stamp using a namespace-qualified key (mirroring the existing
  `deprecated` stamp), but `diff_types.py`'s own `is_abstract` lookup only
  ever reads the *bare*-name key (matching `_merge_record_type`'s own
  pre-existing bare convention for that one fact) — so a qualified-key
  stamp was silently inert for any namespaced clang-only type. Fixed by
  keying the clang-only `is_abstract` stamp bare, matching the lookup it
  actually feeds.
