---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Qualified typedef twin exists but diff_types/surface still read bare-keyed AbiSnapshot.typedefs

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-21, branch `fix/surface-qualified-typedefs`): PARTIAL, narrowed.** `AbiSnapshot.typedefs_qualified` exists (model/snapshot.py:466) and is read by `type_reachability.py` (`_merged_typedefs`) and, as of this pass, by the public-surface closure (`policy/public_surface_closure.py`, `typedef_keys_for_spelling` (`policy/typedef_spelling_keys.py`); see the closing section). Still bare-keyed: `diff_types.py` typedef diffing, and the graph identity table in `model/graph_entity_identity.py`/`compare/surface_graph.py` (see the closing section for the exact remainder).

`dumper_castxml.py`'s `parse_typedefs()` and
`dumper_clang.py`'s `parse_typedefs()` both do the identical
`typedefs[name] = underlying`, where `name` is the typedef's own local
`name` attribute/node field — never the scope-joined qualified spelling
`_qualified_name()`/`_qualified()` uses for every other declaration kind
in the same two modules. Verified directly against real CastXML XML
output (`--castxml-output=1`, clang-emulated) for a minimal repro: a
member typedef nested inside a struct (`struct WithUsing { using
value_type = int; ... };`) is emitted as an ordinary top-level
`<Typedef name="value_type" ... context="_12" .../>` element, structurally
indistinguishable from a namespace-scope typedef except for its
`context` attribute — which `parse_typedefs()` never reads. Two structs
each declaring their own `value_type` member alias (an extremely common
C++ pattern — STL-container-shaped types conventionally expose
`value_type`/`size_type`/`reference`/... as member typedefs) collide on
the identical bare key `"value_type"` in the resulting dict, and
whichever element is encountered last in document order silently wins —
the other's aliasing information is dropped from the snapshot entirely,
with no warning, error, or any user-visible sign of the loss. The same
collision shape reproduces on the direct-clang backend: `_typedefs` is
populated by the same flat walk used for every other decl kind, but
`parse_typedefs()` discards the entry's own recovered `scope` and keys
only on `node["name"]`. **Not fixed here**: this is a real, if narrow,
public-model change — `AbiSnapshot.typedefs`'s key shape is read by
`type_reachability.py`'s `_typedef_spelling_targets()` (see the
"Type reachability" entry above, which already works around this same
bare-key ambiguity from the *consumer* side via its own
ambiguity-counting helper, `_typedef_spelling_targets`, rather than
assuming a bare key uniquely names one typedef), by `diff_types.py`'s
typedef diffing, and by `surface.py`'s typedef-following in
`_walk_type_closure` — none of which currently have test coverage for
the cross-class member-typedef-collision case to validate a change
against. A correct fix needs a qualified (or at minimum
collision-detecting) key threaded through both producers and every
consumer simultaneously, each independently re-verified against the
FP-rate/mutation-score gates before trusting it — the same systematic,
cross-cutting shape (and the same "known gaps over risky reactive
patches" reasoning) as the already-documented bare-`RecordType.name`
opaque-type-suppression collision above, for a different model field.
Filed here per this file's own convention rather than attempted under
this pass's time budget.

**Closed, additively, in a later pass (G31 Phase C continued).** Rather
than replacing `AbiSnapshot.typedefs`'s key shape — which would have
meant re-verifying every one of its existing consumers
(`type_reachability.py`, `diff_types.py`, `surface.py`) against a changed
contract, plus every external Python-API caller reading that field
directly — the actual fix is purely additive: a new field,
`AbiSnapshot.typedefs_qualified` (schema v25), a fully-qualified-name-keyed
twin populated by both `dumper_castxml.py`'s and `dumper_clang.py`'s
`parse_typedefs_qualified()` (using the identical `_qualified_name()`/
`_qualified()` scope-joining every other declaration kind in those modules
already uses) alongside the existing, deliberately-unchanged
`parse_typedefs()`. Since a qualified name is unique per declaration, this
twin cannot suffer the bare-name collision at all — both `Foo::value_type`
and `Bar::value_type` survive as distinct entries where only one bare
`value_type` could before. Threaded through the ELF manifest per-TU merge
path too (`TuFragment`/`MergedTuFragments`/`ElfHeaderAstResult` in
`tu_fragment.py`/`tu_merge.py`/`dumper_manifest.py`), closing the identical,
separately-documented "Known, accepted limitation" comment `tu_merge.py`
carried for the multi-TU case specifically. Only one consumer was wired to
actually use the new field: `type_reachability.py`'s
`directly_referenced_stdlib_types()` (via a new `_merged_typedefs()` helper
that folds `typedefs_qualified` into the flat dict already passed to
`_typedef_spelling_targets()` and siblings) — closing the real false
negative where a public signature spelled with the qualified alias the
bare dict had already lost could silently miss a reachable `std::` field.
`diff_types.py`'s typedef diffing and `surface.py`'s typedef-following in
`_walk_type_closure` are **not** wired to the new field in this pass — each
is its own scoped follow-up, not a drive-by extension, since each has its
own call shape and (per this file's own established discipline) needs its
own test coverage before trusting a change to it. No reliability flag was
needed for the new field (unlike the `*_facts_reliable` flags v19–v23 use
for a real-but-wrong scalar default): an empty `typedefs_qualified` is
exactly the same value a genuinely-typedef-free snapshot would carry, so a
pre-v25 snapshot degrades cleanly to "no extra qualified data available"
rather than being misread as a real fact — see `serialization.py`'s own
v25 history-comment entry for the full reasoning. Verified via new unit
tests on both header backends directly (`_CastxmlParser.
parse_typedefs_qualified`/`_ClangAstParser.parse_typedefs_qualified`) and
an end-to-end `type_reachability` regression proving the bare dict's lossy
collision no longer hides a real `std::` field.

**Surface closure wired (2026-10-21).** `policy/public_surface_closure.py`'s
two typedef walks no longer look a spelling up by its bare key alone. A
spelling resolves through `typedefs_qualified` (schema v25): a bare spelling
matches every qualified typedef whose `::`-tail equals it, and a qualified
spelling matches its exact key. Resolution decides exactness as follows:

- **Unique match** -- the typedef is followed, its target's identifiers are
  walked, and both the spelling and the resolved qualified key are recorded
  in `exact_type_identities`.
- **Several matches** (two classes' colliding `value_type`) -- every match is
  followed and marked public, matching the anti-hiding rule the record/enum
  tail already applies. Nothing reached through the ambiguous fork is recorded
  as exact and nothing is expanded by the exact walk.
- **No qualified match, or a pre-v25 snapshot** (empty `typedefs_qualified`)
  -- the bare dict decides, exactly as before.

Verified end to end: a field-type change in the class only the colliding
`Foo::value_type` alias reaches was invisible to `compare()` (`NO_CHANGE`)
before this pass and is `BREAKING` (`type_field_type_changed`) after it.
Covered by `tests/test_public_surface_qualified_typedefs.py`, which the
`policy.public_surface_reachability` regression class seeds.

**Narrowed remainder (not closed here):**

1. **Qualified spellings are not exact-only in the closure.** The identifier
   tokenizer (`model/type_identifiers.py`) emits a `::`-qualified token's bare
   tail as a candidate spelling in addition to the qualified form. A
   qualified reference such as `Foo::value_type` therefore also reaches the
   bare-tail sibling `Bar::value_type` through the anti-hiding walk, and a
   unique tail can mark a deeper key exact. Records already behave this way
   for `Inner`-style tails. Making qualified spellings exact-only needs the
   walk to carry the originating token's form (provenance), which is a
   broader change than this fix.
2. **Graph identity table is still bare-keyed.** `model/graph_entity_identity.
   snapshot_identities` builds `SnapshotIdentities.typedefs` from
   `declarations.typedefs` only, and `compare/surface_graph.py`
   (`referenced_identifiers_by_node`, `type_spelling_index`,
   `build_public_surface_facts`) reads that bare table. `graph_entity_identity`
   uses `typedefs_qualified` only for ctor/dtor linker-name resolution
   (`resolve_special_member_linker_names`), not for typedef node identity. A
   qualified typedef therefore has no graph node. Changing node identity
   changes persisted graph content, so it needs its own schema decision and
   is left open.
3. **`diff_types.py` typedef diffing** -- unchanged, as described above.
