### Fixed

- **`_downgrade_opaque_struct_changes` (the DWARF-oriented, asymmetric-
  existence opaque-suppression path) now matches on stable `EntityId`
  first, `RecordType.name` spelling second** (ADR-063 Phase 10), closing a
  false negative the same class of qualified-vs-bare spelling mismatch
  `_downgrade_opaque_type_changes` already closed for its own, separate
  opaque-suppression path: a bare `Change.symbol` string compared against
  a bare/namespace-baked `RecordType.name` set could miss a declaration
  both sides agree on. This third opaque-suppression tracker was found and
  migrated onto `compare/opaque_types.OpaqueTypeIndex` via a new
  `OpaqueTypeIndex.build(declarations)` classmethod, always consulted
  non-strictly (a stable-tier miss still falls back to spelling, never
  proof of non-opacity) — a real behavior change only for a snapshot pair
  whose stable identities resolve and whose rendered spellings disagree;
  every existing bare-string-only case is bit-for-bit unchanged.
- **`_downgrade_opaque_struct_changes` no longer fabricates an addition for
  a matched removal or mutation.** A `STRUCT_FIELD_REMOVED`/
  `STRUCT_FIELD_OFFSET_CHANGED`/`STRUCT_FIELD_TYPE_CHANGED`/size/alignment
  change matched against the opaque-struct index used to be unconditionally
  replaced with `TYPE_FIELD_ADDED_COMPATIBLE`, misreporting an observed
  removal or mutation as an addition. Only a genuine `TYPE_FIELD_ADDED`
  match is still relabeled compatible; every other matched kind is now
  excluded on its own merits (the layout change is invisible to
  pointer-only consumers) and routed into the same non-gating,
  `--show-redundant`-visible bucket `_filter_opaque_size_changes`'s own
  opaque exclusions already use, rather than either kind being silently
  dropped or double-counted by the post-processing disposition audit.
- **`find_opaque_struct_types`'s stable-tier absence check now considers a
  declaration's `qualified_name`, not just its bare `name`.** A
  genuinely-present but identity-less counterpart spelled only via
  `qualified_name` (a header-AST backend commonly stores the bare leaf in
  `name` and the scoped spelling in `qualified_name`) used to be
  misclassified as absent from the other snapshot, letting an opaque
  declaration's stable id enter the index and then get borrowed by that
  same counterpart, suppressing a real layout break.
- **`find_opaque_struct_types`'s stable tier no longer lets one visible
  duplicate declaration under an id be silently discarded by another,
  order-dependently.** A snapshot can legitimately carry more than one
  declaration resolving to the same `StableEntityId` (an ODR-duplicate
  pair `SemanticIR.occurrences` deliberately never collapses, or an
  unreconciled header-AST TU-merge pair), and such a pair can disagree on
  `is_opaque`. A last-write-wins `dict[StableEntityId, RecordType]` kept
  whichever declaration was iterated last, making the opacity verdict for
  that id depend on iteration/insertion order; every declaration under an
  id, on each side that has one, is now required to agree before the id
  is treated as opaque.
- **`find_opaque_struct_types`'s stable tier no longer confirms an id
  opaque while an unresolved, visible duplicate sits under one of its own
  spellings.** A declaration that carries no resolvable stable identity at
  all may be an unreconciled TU-merge duplicate or mixed-producer
  occurrence of the very entity a stable id names -- missing identity
  *evidence*, not evidence of an unrelated declaration. Such an
  identity-less visible declaration, on either snapshot, now blocks
  confirming that id opaque (an ordinary bare-name collision with a
  *different*, positively-identified id is unaffected).
