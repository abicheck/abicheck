<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Closed the reachable PDB `TYPE_VTABLE_CHANGED` fabrication.** `Fact[T]`
  gained a `producer` field (T9,
  `docs/contribute/plans/duplication-and-convergence-assessment.md` Phase 6
  item 4): which backend asserted a given fact, when known. PDB's own
  layout extractor now constructs every record's `vtable_fact`/
  `vptr_offset_bits_fact` as an explicit `Fact.unsupported(...,
  producer="pdb")` instead of omitting the fields (which previously
  resolved to the same `NOT_COLLECTED` status a hand-built/typed-API
  `RecordType` omitting `vtable=` also produces, for a different reason).
  `compare/vtable_evidence.vtable_transition_is_evidenced` now declines a
  `TYPE_VTABLE_CHANGED` finding outright whenever either side's
  `vtable_fact.status` is `UNSUPPORTED` — a status only an explicit
  producer-incapability claim can produce, never a typed-API omission — so
  an unrelated size delta or a PDB side's un-observed `Function.is_virtual`
  can no longer fabricate an apparent vtable transition. Existing
  `NOT_COLLECTED`/`FAILED` handling is unchanged, so the previously-fixed
  leaf-class regression (`tests/test_abicc_scenario_parity.py::
  TestLeafClassVirtualMethodAdditions::test_virtual_added_to_leaf_class`)
  is unaffected. `producer` round-trips through both the legacy
  (`storage/fact_codec.py`) and semantic-IR (`storage/semantic_ir_codec.py`)
  wire codecs, rejecting rather than coercing a non-string value, and
  `qualified_name_segments_walk.py`'s two structural `Fact` recognizers
  (used by the anonymous-closure-identity renumbering pass) were updated
  for the new field shape so they keep recognizing `Fact` at all.
