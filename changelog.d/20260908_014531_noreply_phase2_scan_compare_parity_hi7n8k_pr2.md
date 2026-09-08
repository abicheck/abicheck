### Changed

- **`compare` now runs all eleven cross-source hygiene checks
  automatically, not just six** — `header_build_context_mismatch`,
  `odr_type_variant`, `identity_collision_detected`,
  `compile_context_conflict`, and `source_surface_dso_mismatch` join
  `unversioned_exported_symbol`, `private_header_leak`,
  `exported_not_public`, `public_not_exported`, `rtti_for_internal_type`,
  and `public_to_internal_dependency` on `compare()`'s automatic,
  evolution-stated (`persistent`/`introduced`/`resolved`/`not_evaluated`)
  pipeline stage (`cross_source_checks`, still default `True`, still no
  CLI/API/Action opt-in flag — ADR-068 D4/D5). These five read build (L3)
  and source-ABI-replay (L4) evidence rather than binary/header (L0-L2)
  evidence, and evidence-gate to `not_evaluated` the same way the first six
  checks already did whenever a side lacks `--sources`/`--build-info`/
  `--depth source` evidence — no new mechanism, no new flag. `scan` is
  unaffected and not yet retired.

### Fixed

- **`odr_type_variant`/`identity_collision_detected` no longer silently
  drop genuinely distinct findings sharing one identity key.**
  `workflows.cross_source_evolution._run_one_side` folds a check's findings
  into a dict keyed by a per-check identity function; for these two checks
  the identity was not always unique across a single side's own findings —
  three divergent per-TU definitions of one type recorded against the same
  header all compare against the same original baseline hash and produce
  multiple `Change` records sharing `(symbol, source_location)`, and a
  three-way collision on one L4 identity key produces multiple records
  sharing `(symbol, new_value)` — so the dict comprehension kept only the
  last record, and `run_crosschecks` had returned all of them. Both checks
  now stamp the additional distinguishing evidence they already compute
  (the ODR conflict's own per-TU layout hashes; the identity collision's
  own transition USR) onto `Change.old_value`/`new_value`, and their
  per-check identity functions read the extra field(s) (Codex review, PR
  #1147 finding 1). Two follow-up rounds (Codex review, PR #1148): first,
  that distinguishing evidence is assigned by TU/entity visitation order
  within one side's own replay, not by any OLD/NEW-snapshot meaning, so the
  same persistent conflict/collision could be recorded as `(A, B)` on one
  side and `(B, A)` on the other and read as a spurious RESOLVED+INTRODUCED
  pair instead of one PERSISTENT finding — sorting each pairwise record
  fixed the two-variant/two-participant case. Second, sorting a *pairwise*
  record is still traversal-dependent for three or more variants/
  participants: `{A, B, C}` visited in different orders yields different
  sets of pairwise edges even though the underlying unordered set is
  identical. Both checks were rewritten to group their producer's raw
  per-pair records by the same key `source_link._route_type`/
  `_route_declaration` themselves group by (`(qualified_name, header)`;
  `identity`) and union each group's evidence into one order-independent
  `Change` per group, rather than one `Change` per pairwise record. Third
  follow-up round: grouping alone still left `identity_collision_detected`'s
  own `symbol` order-dependent when colliding declarations carry different
  qualified names — retaining whichever record's name arrived first in the
  group was exactly as order-dependent as the USR pair was before the
  second round. Fixed by deriving the group's canonical `symbol` as `min()`
  over the group's qualified-name set (a deterministic function of the set,
  not of arrival order). `odr_type_variant` has no equivalent seam since its
  group key already includes `qualified_name` itself.
- **`odr_type_variant` findings on a public type could be wrongly demoted
  as "not-exported."** `abicheck/surface.py`'s public-surface scoping
  classifies a finding as symbol-level or type-level before deciding
  in/out of surface; `odr_type_variant`'s `symbol`/`caused_by_type` both
  name the conflicted *type*, but the kind was missing from
  `_TYPE_LEVEL_KIND_NAMES`. When that type name also happened to exist in
  the snapshot's `all_symbols` as an unrelated, non-public function (a
  common C shape — a struct tag and a private helper sharing a spelling),
  the symbol-level path ran first and demoted the finding before type
  reachability was ever consulted. Now classified type-level, like
  `rtti_for_internal_type`'s and the other cross-source kinds' own
  carve-outs (Codex review, PR #1147 finding 2).
