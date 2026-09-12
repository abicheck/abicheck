### Added

- **ADR-071 records the decision behind `assurance.require_complete` at
  release cardinality, and makes a gated release say so.** The fold itself —
  `max` over every compared member's own `0`/`1`, into the *existing*
  `ExitReason.ANALYSIS_ASSURANCE` axis rather than a release-only sibling —
  landed in parallel; this adds the decision record, one owner for the rule
  (`policy/release_assurance.py`, which the exit resolver now calls instead of
  restating), and the publication half. `max`, never `min`: a member's
  incomplete analysis cannot be masked by a complete sibling, and over a single
  member the fold is the identity, so a one-member package gates and reports
  exactly as a scalar `compare` of the same pair does.
- **Every document a gated release publishes now carries the gate.** Release
  JSON gains an `analysis_assurance` fold block (aggregate status, member
  counts, the `0`/`1` contribution, and `incomplete_members` rows naming each
  short member and why) and the canonical top-level
  `analysis_assurance_exit_contribution` — the key `abicheck aggregate` and the
  Action's `gate_mode: deferred` path read; `--output-dir`'s `summary.json` and
  each per-library `{library}.json` carry both too, and the
  `effective_config_fields`/digest receipt names
  `gate.require_complete_analysis` so a gated run is distinguishable from an
  ungated one. A non-JSON format gets the same facts as a one-line stderr
  notice, worded from the real resolved code — beside a genuine ABI break it
  says the axis contributes below that exit rather than claiming it floored
  anything (release schema 1.3). With the floor visible only inside `exit`, a
  release that correctly exited `1` published documents every consumer read as
  clean. No new policy setting and no new exit number;
  `assurance.require_complete` defaults `false`, so every pre-existing release
  invocation's exit code, report bytes and stderr are unchanged.

### Removed

- **The last of the four guards that existed only to stay ahead of the missing
  semantics** (ADR-071 D8): the stored-`BundleFacts` operand's rejection of
  `assurance.require_complete`. It rested on the stored side carrying "no
  per-library analysis-assurance rollup to aggregate" — but the assurance being
  folded belongs to each *comparison*, not to either operand: every member of a
  stored-baseline release is still compared against a live NEW artifact and
  still produces its own `AnalysisAssurance`. That driver now folds, gates and
  publishes identically to the live fan-out. What a shallow stored side cannot
  do is manufacture evidence it never captured — it reports `partial`, which the
  gate names rather than ignores. `checks[].analysis.assurance: complete` on a
  `kind: bundle` check and the Action's `analysis-assurance-complete` input for
  `kind: bundle` validate and run.

### Fixed

- **A stored-`BundleFacts` decode that exceeds the container-node budget now
  says how to raise it.** Its remedy named `max_json_object_nodes`, an
  internal parameter no CLI user can pass, which made a legitimate
  large-toolkit bundle read as an unfixable refusal; it now names the real
  `resource_limits.max_bundle_facts_decode_nodes` config key and states that
  only an *explicit* `--config` (the composite Action's `build-config` input)
  may raise it, since an auto-discovered `.abicheck.yml` can come from the
  same untrusted checkout being decoded. The conservative default is
  deliberately unchanged: the node count is read from the payload under
  inspection, so no scale-aware default can tell a real oneDAL-scale bundle
  from a decode bomb of the same declared size. `action.yml`'s `old-library`
  input also documents that it accepts a stored `BundleFacts` document (the
  operand is classified from its own content, so no separate input or flag
  selects that shape) and why `--bundle-facts-out` stays an `extra-args`
  option.
