<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--contract-evaluation --contract public` no longer over-rejects an exact,
  fully-qualified public reference.** When a public signature names a type
  fully qualified (e.g. `ns1::Point`) and an unrelated sibling elsewhere in
  the snapshot shares the same bare tail (`ns2::Point`), the evaluator
  previously reported `UNKNOWN_UNRESOLVED` for both, indistinguishable from a
  genuinely ambiguous bare-only reference. `PublicSurface` now records which
  qualified identities were reached via an exact, unambiguous spelling
  (`exact_type_identities`), so `ns1::Point` confirms `IN_CONTRACT` while a
  type reached *only* through a shared, ambiguous bare tail (no side ever
  spells the qualifier) is still correctly left unresolved. The closure's
  own type index (`_index_surface_types`) now also keys on `RecordType`/
  `EnumType.qualified_name`, not just the bare `.name` and its trailing
  `::` segment, so this fix applies under the bare-name-plus-separate-
  qualified-name convention (castxml/clang) as well as DWARF's
  qualifier-baked-into-`.name` convention. `exact_type_identities` is
  computed by a dedicated, ambiguity-vetoing closure walk that stops the
  instant it hits a colliding `::`-tail, rather than a per-spelling check
  inside the ordinary (deliberately over-keeping) closure walk — a type
  reached only by speculatively following one of several same-tail
  candidates' fields/bases is never marked exact, even if its own spelling
  happens to be otherwise unique. A publicly-reachable typedef alias's own
  name (not just the record/enum its target resolves to) is also recorded
  exact, so a `TYPEDEF_REMOVED`/`TYPEDEF_BASE_CHANGED` finding on a directly-
  exposed alias confirms correctly too. Every record/enum's *bare* `.name`
  is now recorded alongside its `.qualified_name` (each independently
  re-checked for its own uniqueness, since a node reached via one exact
  form must not grant the other form exactness for free if that other
  form happens to independently collide elsewhere) — `diff_types.py`
  always emits a type-level finding's own candidate as the bare `.name`,
  so this was previously the single most common castxml/clang
  confirmation shape left unfixed.
