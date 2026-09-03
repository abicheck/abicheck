### Added

- **Snapshots can now carry a canonical `SemanticIR`** (ADR-063 Phase 6's
  first slice): `abicheck/model/semantic_ir.py` defines the one
  backend-independent shape every extraction backend will canonicalize into
  — `SemanticIR.occurrences`, keyed by `OccurrenceId` so a complete
  definition and an ODR-duplicate/incomplete declaration sharing one
  `EntityId` both survive, with `canonical_entities()` as the explicit,
  deterministic reduction for a consumer that genuinely wants one view per
  identity. `CanonicalEntity` carries only the non-identity payload
  (canonical spelling, template arguments, CV-qualification, each wrapped in
  `Fact[...]`), never a second copy of the identity its key already states.
  `AbiSnapshot.semantic_ir` persists through schema **v38**, encoded as a
  list of entries rather than a string-keyed map so the typed `ScopePath`
  inside each key round-trips losslessly. The field is additive and
  currently `None` on every snapshot a real `dump` produces — no backend is
  narrowed onto the shared normalizer yet, so no detector, verdict or exit
  code changes, and such a snapshot's document differs from the one this
  release's predecessor wrote only in its `schema_version` stamp (both new
  keys are written only when there is something to record).
- **`--ast-frontend hybrid` reconciles `semantic_ir` across both backends**
  the same way `merge_snapshots()` already reconciles every legacy field:
  castxml is the base, clang backfills only the facts castxml left
  unresolved, a clang-only entity is unioned in, and a fact both backends
  resolved to different values keeps castxml's while recording the discarded
  one in the new `AbiSnapshot.semantic_ir_conflicts` — keyed per
  *occurrence*, since `fact_provenance`'s declaration-only key cannot
  separate two matched occurrence pairs sharing one `EntityId`. Matching is
  fail-closed: two occurrences pair only when at most one side supplies a
  disambiguator, and a group with no unique complete matching keeps every
  occurrence from both sides rather than being paired by guess — except an
  occurrence the two sides key *identically*, which is one occurrence by
  definition and still merges.
