<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->

### Changed

- **An anonymous-namespace typedef or constant no longer disappears from
  comparison.** `_aliases`/`_values` now key an entity by its bare leaf name
  (`model.semantic_ir_legacy_adapter.render_display_name_or_leaf`) when its
  identity's scope contains an unrenderable segment (`Anonymous`/
  `LocalToFunction`), instead of skipping it outright (Codex review: the
  legacy adapter's own synthetic identity for the identical declaration
  always renders — an empty scope built purely from the flat map's own
  string key — so using the real `SemanticIR` directly was silently
  dropping an anonymous-namespace declaration the legacy path always
  surfaced).
- **The typedef and constant detector cohorts' `SemanticIR` is now a real
  authority, not a fidelity-gated echo of the legacy projection** (ADR-063
  Track T3, "typedef/constant authority cutover"). `compare.typedefs.
  typedef_index_pair`/`compare.constants.constant_index_pair` previously
  built *both* an IR-backed and a legacy-projected index on every
  comparison and used the IR only when it exactly reproduced the legacy
  alias/value/identity map — so a real `SemanticIR` that disagreed with the
  legacy projection was never actually trusted, only silently routed
  around. Each side of a comparison is now decided independently: a side
  with a real `SemanticIR` reads it directly, with no second index built
  for that side and nothing left to adjudicate against; a side with none
  (DWARF-only, a producer without typedef/constant identity, a pre-v38
  reload) still reads through the legacy adapter's projection of its own
  flat collection, unaffected. Deciding per side rather than
  both-or-neither (Codex review) matters: the two shapes are matched by
  rendered alias name, not by `EntityId`, so mixing them is safe, and a
  both-or-neither rule would have discarded a side's real `SemanticIR` in
  favor of a legacy reconstruction of it whenever the *other* side lacked
  one — fabricating a removal if that reconstruction happened to disagree
  with (or lack) content the real IR still has. One consequence: a real
  `SemanticIR`'s own emission order, and its own resolved value for a
  typedef/constant, are now authoritative even where they would have
  disagreed with a stale legacy projection — that disagreement is the
  whole point of authority transfer, not a bug to route around.
- **A constant's addition/removal is no longer masked by an unsupported
  value.** `diff_constants` now emits `CONSTANT_ADDED`/`CONSTANT_REMOVED`
  for a membership change regardless of whether the constant's own value
  can be rendered — previously, a constant whose `canonical_spelling` is
  `Fact.unsupported()` (a clang compound-initializer fingerprint or
  bool-literal spelling) on the side where it actually exists caused the
  whole comparison to skip before the membership check ever ran, silently
  dropping a real addition or removal (Codex review; only reachable in
  practice once `SemanticIR` became the sole comparison-time source above).
  Only the value *comparison* (`CONSTANT_CHANGED`) still requires both
  sides' values to be comparable text.
- **The Track T3 consistency check now also runs on a loaded snapshot.**
  `serialization.snapshot_from_dict` constructs `AbiSnapshot` before
  decoding `semantic_ir` from the document and assigning it onto the
  already-constructed snapshot directly — bypassing `AbiSnapshot.
  __post_init__` entirely, so a stored v38+ snapshot whose sidecar
  disagreed with its own `SemanticIR` loaded without the new check ever
  running (Codex review). `snapshot_from_dict` now re-runs it explicitly
  right after decoding.
- **A per-side-independent typedef comparison no longer mixes bare and
  qualified key spaces.** When one side does not trust qualified typedef
  naming (a genuinely pre-v25 baseline, which also predates `SemanticIR`
  entirely), `typedef_index_pair` now renders *both* sides through the
  legacy adapter over the comparison's own bare-keyed maps, even when the
  other side carries a real `SemanticIR` — which always renders under its
  own fully qualified name (Codex review: using it directly there would key
  that side as e.g. `"ns::Alias"` against the bare-mode side's `"Alias"`
  for the identical declaration, fabricating a removal out of a naming
  granularity mismatch). Per-side independence still applies whenever both
  sides trust qualified naming, which is the common case.
- **A constant value hidden behind an unsupported fact is no longer
  compared as unchanged.** `diff_constants` now falls back to each
  snapshot's own flat `AbiSnapshot.constants` raw text — the same text the
  legacy declaration parser always populated, independently of
  `SemanticIR`'s cross-backend-safety decision — when the canonical
  `SemanticIR` value is `Fact.unsupported()`, gated through the existing
  `is_fingerprint_comparison_unreliable` predicate like any other
  fingerprint comparison (Codex review: without this, a real edit to a
  clang compound initializer or a `constexpr bool` aliased to a
  `True`/`False`-named identifier between two same-backend snapshots
  produced no finding at all).
- **A `SemanticIR` disagreeing, by identity, with its own legacy sidecar is
  now a hard, loud failure instead of a silently-absorbed fallback.** The
  one piece of the old fidelity gate still worth checking once the IR is
  the sole comparison-time source — whether a real `SemanticIR`'s resolved
  `EntityId` for a rendered typedef/constant name agrees with the same
  snapshot's own `typedef_entity_ids`/`constant_entity_ids` sidecar, both
  written by the same producer pass — moved to `AbiSnapshot.__post_init__`
  (`model.semantic_ir_legacy_adapter.assert_typedef_ir_consistent`/
  `assert_constant_ir_consistent`), which now raises the new
  `errors.SemanticIrAuthorityError` on a genuine disagreement. This runs
  once per snapshot construction rather than once per comparison, and a
  snapshot carrying a `SemanticIR` with no populated legacy sidecar at all
  (the common, forward-looking shape) is unaffected — the check only fires
  when both representations are actually present and disagree.
