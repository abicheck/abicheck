### Added

- **ADR-070: `assurance.require_complete` now works for a directory/package
  (release) comparison**, and for a stored `BundleFacts` operand. It used to
  be a usage error (exit 64) on the premise that the per-library fan-out "has
  no single `analysis_assurance` result to gate on"; it has one per compared
  member, and the release's contribution is `max` over those members'. The
  fold goes into the *existing* `ExitReason.ANALYSIS_ASSURANCE` axis and
  `ExitDecision.analysis_assurance_contribution`, not a release-only sibling:
  over a single member the fold is the identity, so a one-member package gates
  and reports exactly as a scalar `compare` of the same pair does, and a
  consumer reading that field never sees `0` for a run this axis floored.
  `max`, never `min` — a member's incomplete analysis cannot be masked by a
  complete sibling, and adding a member can only raise the contribution.
  Orthogonal to ADR-065's completeness axis and independent of it: that one
  asks whether every selected member was compared at all, this one asks
  whether the comparisons that ran had complete evidence, so a release can be
  scope-complete with a partial analysis or the reverse, and on a tie both are
  named in `reasons`. Release JSON gains an `analysis_assurance` fold block
  (aggregate status, member counts, the `0`/`1` contribution, and the
  `incomplete_members` rows naming each short member and why) plus
  per-`libraries[]` `analysis_assurance_status`; a non-JSON format gets the
  same facts as a one-line stderr notice (release schema 1.3). `--output-dir`'s
  `summary.json` folds the axis into its own `exit` block too, so it cannot
  report a different exit code than the process took, and the notice is
  worded from the real resolved code — beside a genuine ABI break it says the
  axis contributes below that exit rather than claiming it floored anything.
  No new policy
  setting and no new exit number — `assurance.require_complete` defaults
  `false`, so every pre-existing release invocation's exit code, report bytes,
  and stderr are unchanged.

### Removed

- **The four guards that existed only to stay ahead of those missing
  semantics** (ADR-070): `cli_compare_options._reject_set_input_flags`'s
  release rejection, `compare_bundle_facts_rejections`'s mirror of it for a
  stored-`BundleFacts` operand, `buildsource/project_targets.py`'s rejection
  of `analysis.assurance: complete` on a `kind: bundle` run-plan check, and
  `actions/check-target/validate-inputs.sh`'s `_fail` for
  `analysis-assurance-complete` on `kind: bundle`. Each was correct while the
  semantics were absent; a declared, now-supported setting is not a finding.
  `checks[].analysis.assurance: complete` on a bundle check and the Action's
  `analysis-assurance-complete` input for `kind: bundle` both validate and run
  now.

### Fixed

- **A stored-`BundleFacts` decode that exceeds the container-node budget now
  says how to raise it.** The refusal named `max_json_object_nodes`, an
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
