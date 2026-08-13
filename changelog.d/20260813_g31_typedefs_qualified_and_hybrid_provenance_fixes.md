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
  PR): a method or type present *only* on the clang leg of a hybrid
  (`--ast-frontend hybrid`) snapshot pair got no provenance stamp for
  these two facts, so `both_known_backed_fact` saw no recorded producer
  and silently declined to compare a real override-specifier/abstractness
  transition — even though both facts are genuinely clang-sourced for a
  clang-only declaration. Mirrors the existing `deprecated` stamp already
  applied to clang-only-appended declarations.
