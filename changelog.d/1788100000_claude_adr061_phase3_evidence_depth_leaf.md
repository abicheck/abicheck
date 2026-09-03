### Changed

- **ADR-061 Phase 3 (converge artifact workflows)**: the evidence-depth
  vocabulary — the depth ladder, "what depth did this artifact actually
  reach", and the stricter "may an explicit `--depth source` be considered
  satisfied" gate — now has one owner, `abicheck/evidence_depth.py`, instead
  of living in `cli_dump_helpers.py` where every non-CLI consumer had to
  either import through the CLI layer or keep a private copy. Both had
  happened: the ladder existed four times (`scan_levels.USER_DEPTHS` plus
  three separate `_DEPTH_RANK` dicts), and `analysis_assurance.py` carried a
  hand-copied depth-label function whose own comment recorded that it was
  "duplicated rather than imported" to avoid the CLI import. The rank map is
  now *derived* from `USER_DEPTHS`, so the ordering has exactly one
  definition and a new rung cannot leave a copy silently disagreeing.
  `cli_dump_helpers.evidence_depth_label`, `_gated_source_label`,
  `_l4_source_abi_was_attempted` and `_DEPTH_RANK` remain as delegating
  aliases, so every existing caller is unchanged.
