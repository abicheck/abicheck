<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->

### Changed

- **The typedef and constant detector cohorts' `SemanticIR` is now a real
  authority, not a fidelity-gated echo of the legacy projection** (ADR-063
  Track T3, "typedef/constant authority cutover"). `compare.typedefs.
  typedef_index_pair`/`compare.constants.constant_index_pair` previously
  built *both* an IR-backed and a legacy-projected index on every
  comparison and used the IR only when it exactly reproduced the legacy
  alias/value/identity map — so a real `SemanticIR` that disagreed with the
  legacy projection was never actually trusted, only silently routed
  around. They now read the real `SemanticIR` directly whenever both sides
  of a comparison carry one, with no second index built and nothing left to
  adjudicate against; a snapshot with no real `SemanticIR` on either side
  (DWARF-only, a producer without typedef/constant identity, a pre-v38
  reload) is unaffected and still reads through the legacy adapter on both
  sides, as before. One consequence: a real `SemanticIR`'s own emission
  order, and its own resolved value for a typedef/constant, are now
  authoritative even where they would have disagreed with a stale legacy
  projection — that disagreement is the whole point of authority transfer,
  not a bug to route around.
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
