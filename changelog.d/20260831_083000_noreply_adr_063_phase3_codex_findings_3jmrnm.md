### Fixed

Several real findings from an automated review pass on ADR-063 Phase 3, each
verified and fixed:

- **`PublicSurfaceQuery.resolve()` now returns `None`, not a confirmed-empty
  `frozenset`, when a snapshot cannot support a real `EntityId`-based
  answer.** Two distinct unavailability cases previously collapsed to an
  empty `frozenset`: `resolve_public_surface()` itself unresolvable (no
  header-derived visibility at all), and a whole snapshot carrying no
  `entity_id`-bearing declaration (a pre-ADR-063-Phase-2 snapshot, schema
  < 28). Since `service.compare_snapshots()` forwards `.resolve()`'s
  result unconditionally as `old_public_entity_ids`/`new_public_entity_ids`,
  either case previously activated strict `EntityId` membership filtering
  in `build_surface_graph()`/`compute_surface_metrics()` with an empty
  set — silently zeroing `--surface-metrics`/`--pattern-verdicts` output
  for any snapshot predating entity-id population, instead of falling
  back to the legacy `Visibility.PUBLIC`-only answer every
  `*_public_entity_ids=None` consumer already handles correctly. A single
  declaration missing its own `entity_id` while siblings have theirs is
  unaffected — that declaration alone still drops out of a real,
  non-`None` resolved set, the already-accepted per-declaration
  degradation.
- **`compare/surface_graph.py`'s type index no longer resolves an
  ambiguous bare type name arbitrarily.** Two types sharing one bare name
  across namespaces (`ns1::Foo`/`ns2::Foo`) previously registered the
  same bare index key via first-wins `setdefault`, making an unqualified
  `Foo*` signature reference resolve to whichever type happened to
  iterate first — order-dependent and possibly wrong. Mirrors
  `surface.py`'s own `ambiguous_type_names` convention properly this
  time: an ambiguous bare key is dropped from the index entirely rather
  than resolved arbitrarily, so such a reference now produces no edge
  instead of a wrong one. The qualified spelling is unaffected.
- **`storage/surface_graph_codec.py`'s decoder no longer discards a
  genuinely distinct nested `build_source.source_graph`.** The encoder's
  own dedup is identity-gated — it drops the nested `source_graph` key
  only when it is the *same object* as the top-level `surface_graph`,
  and independently encodes both when they differ. The decoder
  previously rebound `build_source.source_graph` to the top-level graph
  unconditionally, silently discarding every node/edge of a real,
  independently-encoded nested graph on a save/load round-trip. It now
  rebinds only when the nested `source_graph` key is actually absent
  from the document (the signal that the encoder's dedup ran), matching
  the encoder's own stated contract.
- **`PublicSurfaceQuery.resolve()`'s function/variable membership check no
  longer lets a hidden overload inherit a public sibling's bare name.**
  `surface.py`'s `_seed_public_roots` unions both the mangled name and the
  bare demangled name into `public_symbols` for a public declaration
  alone, but the membership check here was a plain `mangled in ... or
  name in ...`, which still matched a *hidden* overload sharing that bare
  name with its public sibling. A new `_linker_key_is_public()` helper
  prefers the mangled identity whenever one is available and only falls
  back to the bare name when there is no mangled spelling to disambiguate
  with, closing the false-inclusion.
- **`surface_graph.py`'s root-seed-type collection no longer lets an
  excluded overload's own referenced types leak through an included
  sibling.** `_build_root_seed_types()` previously unioned *all*
  `Visibility.PUBLIC` overloads' seed types by bare name first, then
  relied on `public_roots()` to narrow by `EntityId` afterward — but a
  post-hoc, name-level narrowing pass cannot un-union one specific
  excluded overload's own seeds back out of a name two-or-more overloads
  share. Filtering now happens per declaration, before the union, via the
  existing `_is_public()` helper; `public_roots()` is now a plain
  `frozenset(self._root_seed_types)` with no narrowing logic left to get
  wrong, and the now-dead `_entity_ids_by_name` field and
  `_build_entity_ids_by_name()` helper are removed.
- **`compare/surface_graph.py`'s fallback node ids no longer collide
  across entity kinds.** `_approximate_node_id()`'s fallback (used
  whenever a declaration/type has no parse-time `entity_id`) now
  namespaces by kind (`"declaration::"`/`"type::"`/`"typedef::"` instead
  of a flat `"approx::"`/`"typedef::"` scheme), so a function and an
  unrelated record/enum/typedef sharing one bare spelling (legal C:
  `struct stat` alongside a function `stat()`) no longer resolve to the
  same graph node.
- **`SurfaceGraphLike`'s Protocol now declares `to_dict()`**, matching
  what every real implementation and consumer already required; the
  now-unnecessary `# type: ignore[attr-defined]` at the codec's one call
  site is removed.
- **`snapshot_to_dict()` no longer wastes work recursing into
  `surface_graph`'s nodes/edges through `dataclasses.asdict()`** before
  `encode_surface_graph()` unconditionally replaces that subtree with the
  graph's own `to_dict()` encoding — mirroring the existing lazy-cache
  handling in the same function, `surface_graph` is now cleared for the
  duration of the `asdict()` call and restored afterward.
- **`docs/reference/snapshot-format.md`'s schema-version history now
  covers v26-v28** (previously jumped straight from v25 to v29), and the
  `surface_graph` field row now says the key is *omitted* rather than
  written as `null` when no graph exists, matching
  `encode_surface_graph()`'s actual behavior.
