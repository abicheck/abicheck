# ADR-084: A Binary-Depth Run May Not Keep a Source-Level Break It Cannot Back

**Date:** 2026-10-21
**Status:** Accepted — implemented. The exit-code change for header-less runs was approved by the maintainer. The follow-ups are recorded in "Not done" below and in `docs/contribute/known-gaps.md`.

Governed by: [ADR-009](009-verdict-system-and-exit-codes.md) (verdict and exit codes), [ADR-028](028-source-build-evidence-pack.md) (artifact evidence is authoritative; build and source evidence corroborates), [ADR-042](042-compatibility-and-gate-decision-separation.md) (`effective_verdict` modulation, annotate and never remove), [ADR-044](044-reachability-aware-suppression.md) (reachability tags), [ADR-067](067-change-intent-acknowledgment-and-disposition-audit.md) (record before disposing).

## Context

Every kind in `API_BREAK_KINDS` is a claim about the *source* contract: a header or source tree promised something a consumer compiles against. Some of those kinds are also producible from binary or debug-info input alone. Examples are `mandatory_template_param_added` (from an exported template instantiation's demangled arity), `inline_function_removed` (an unexported symbol with a legacy `PUBLIC` visibility), `type_removed` (a DWARF record), and `experimental_removed_without_replacement` (a name).

Before this decision the verdict for an API_BREAK kind was decided by registry membership alone (`policy/classification.py` `compute_verdict`). Nothing between detection and verdict asked whether a header or source producer had observed the entity. A binary-only run therefore reported `API_BREAK` (exit `2`) with no header or source evidence. Probes at `026a6109e` reproduced this for `mandatory_template_param_added` and for `inline_function_removed` and `type_removed`, both isolated and in a combined 10-stem shape.

Two more detectors set `public_reachable=True` unconditionally: `std_reexport_removed` and `tag_type_renamed`. The tag was not evidence, because it was set from the construction itself, so it could not gate the kind that set it.

The export-removal kinds (`func_removed`, `var_removed`, `func_removed_elf_only`) are *not* part of this problem. They are `BREAKING` by artifact proof (ADR-028) and stay `BREAKING` without headers. The gate is for claims whose source-level meaning the binary cannot establish.

## Decision

### D1. Three disjoint buckets partition `API_BREAK_KINDS`

`abicheck/policy/binary_gated_contract.py` assigns every API_BREAK kind to exactly one bucket:

- **`BINARY_GATED`**: producible from binary or debug-info input. The gate (D3) applies.
- **`HEADER_OR_SOURCE_GATED`**: the producer consumes only header, source or build data, so a binary-only run never produces the kind. Each entry in `NON_BINARY_REASONS` names the input it needs. Not demoted.
- **`OUT_OF_AXIS`**: governed by its own evidence axis (the Python `.pyi` surface, the Linux kABI symbol file) or a sentinel that is not a break claim (`evidence_required_missing`). Each names its axis in `OUT_OF_AXIS_REASONS`. Not demoted.

A bucket is a *claim about producibility*, and the bias is deliberate. Over-including a kind in `BINARY_GATED` costs nothing when the kind is in fact header-only: a header-backed run keeps `API_BREAK` (D3), and a binary-only run could not have produced it. Under-including a binary-producible kind would leave the bug in place. The test `test_buckets_cover_every_api_break_kind` fails when a new API_BREAK kind is added without a bucket.

Membership in `BINARY_GATED` was first set by reading the producers. `tests/test_binary_gated_probe_integration.py` (2026-10-21) now probes it on real gcc `-g` libraries for every DWARF-level kind and the header-gated `param_default_value_removed`, `constant_changed` and `constant_removed` rows. No `HEADER_OR_SOURCE_GATED` kind is produced binary-only. Eight `BINARY_GATED` kinds are reached from DWARF alone and are demoted. The others are reached only with headers, because DWARF does not carry the fact their detector reads (`final`, `explicit`, `mutable`, pure-virtual, enum scoping, pointee qualifiers). They stay in `BINARY_GATED`: one fixture cannot prove that no binary-only input produces a kind, and the over-inclusion costs nothing.

### D2. Observed source-contract evidence is a fact about a declaration, not about the binary

`abicheck/model/source_contract_evidence.py` `has_observed_source_contract_evidence` answers one question: did a producer that read a header or source observe this declaration as contract? A declaration qualifies when either holds:

- fact (a) `declared_in_headers` is confirmed (a header parse of this run declared it), or
- fact (b) `in_public_contract` is confirmed by a non-binary producer (a supplied public-header set).

A third class counts for a *name*, not a declaration: the snapshot's embedded L4 source pack (`build_source.source_abi`) records each declaration a source extractor found in a public header as a `SourceEntity` with a `public_header` or `generated` visibility (`PUBLIC_SOURCE_VISIBILITIES`, pinned to `buildsource/source_link.py` by a test). A removed declaration exists only on the old side, but its source entity is what the producer saw in the header, so `observed_source_contract_names` returns those names and the gate matches a finding's symbol and owner against them. This is how a `--sources` run keeps its `API_BREAK` (`case157_inline_function_removed`).

It does **not** qualify on:

- a binary export (`binary_exported`). The export table says a consumer *could link* against the symbol. It does not say the declaration was promised. `is_abi_visible`, which feeds the public-surface population, blurs this, and the gate deliberately does not use it.
- a DWARF or PDB-sourced (b). `extract/surface_fact_producers.debug_info_surface_facts` records (b) from a confirmed export, which is true of the binary and carries no header evidence.
- a legacy-derived fact (a pre-v46 `PUBLIC` visibility bridge). That is a derivation, not an observation.
- for a record or enum, a source location alone. Records and enums need both a header parse of their snapshot (`from_headers`) and a located `source_header_fact`.

The narrower predicate `has_observed_contract_evidence` (in `model/surface_facts.py`) answers a different question, whether an entity is known to be public for narrowing an export set, and is left unchanged.

### D3. The gate: keep the claim with evidence, demote and record it without

`demote_unproven_binary_api_breaks` runs once per disposition pass (`DemoteUnprovenBinaryApiBreaks`, the last step of `DEFAULT_PIPELINE`, so it follows the late detectors that build findings after suppression). For each finding whose kind is in `BINARY_GATED` and that carries no `effective_verdict` yet:

1. **Resolve the subject.** The finding's `symbol` is matched against the declarations of *both* snapshots by name, mangled name and qualified name, and against the owner of a member (`lib::A::f` also matches `lib::A`).
2. **Keep** `API_BREAK` if any resolved declaration satisfies D2.
3. **Unresolved subject.** The subject takes the run-level answer: in a run that parsed headers, the run's own evidence decides (an unresolved spelling is a name the index cannot match, not proof that nothing was observed); in a binary-only run there is no evidence and the finding is demoted.
4. **Otherwise mark** the finding with `modulation_rule = "binary_gated_api_break"` and a `modulation_reason` naming what is missing. The gate writes **no verdict**. It never removes the finding.

The verdict is a cap, applied where the policy resolves a category. `policy.classification.effective_category` (the one place a finding's category is decided, and what `compute_verdict` aggregates) and `policy.reclassify.effective_verdict_for_change` (the per-finding resolver) both do this: they resolve the policy first, and lower an `API_BREAK` the policy left standing to `COMPATIBLE_WITH_RISK`. A named policy's relaxation (`sdk_vendor` keeps a source-only rename `COMPATIBLE`) and a policy-file escalation (`BREAKING`) both survive a binary-only run. The gate's first design set `effective_verdict` instead, which overrides the policy, and the test `test_a_policy_file_escalation_is_not_lowered_by_the_gate` pins why it was changed.

The ADR-067 ledger records the demotion with its rule and reason (`binary_gate_rule_provenance`, passed to `finalize_ledger`). The report therefore shows a `COMPATIBLE_WITH_RISK` finding with its rule, and a passing run still lists the demoted finding.

The gate reads only the declarations' observed facts, and the source pack's public-header names. It never reads `public_reachable`, `reachability_state` or `reachability_kind` as evidence, because those tags are set from the same export population (circular), and they are absent in default runs.

### D4. Reachability tags are computed from the same evidence

`std_reexport_removed` and `tag_type_renamed` no longer hard-code `public_reachable=True`. They set the reachability tags from `public_reachability(has_observed_source_contract_evidence(...))`: a proven claim (`PROVEN_REACHABLE`, `direct_public_symbol`) when the declaration is observed as contract, and `public_reachable=False` with `UNKNOWN` otherwise. Reachability is recorded as unknown rather than asserted.

Two other detectors still assert reachability for BINARY_GATED kinds (`unspecified_return_now_named` at `diff_templates.py`, and `experimental_removed_without_replacement` via `subject_is_public` in `diff_namespaces.py`). Those are not changed here; the gate does not read their tags, so their verdict is correct, but their tags still overstate. See `known-gaps.md`.

### D5. The evidence label follows the demotion

`evidence_status_for_result` labels a gate-demoted finding `CONTEXTUAL_RISK`, not `SOURCE_CONTRACT`. The label is the report's statement of how the finding was established, and a `source_contract` label on a `COMPATIBLE_WITH_RISK` finding would restate the claim the run could not back. This is the one per-finding refinement added to a function that otherwise reads only the kind, and it is keyed on the gate's own rule id, not on the verdict. `evidence_status_for_change` (which has no run context) is unchanged.

## Consequences

- **Exit codes change for header-less runs.** A binary-only comparison whose only API_BREAK findings are BINARY_GATED kinds without source evidence now reports `COMPATIBLE_WITH_RISK` (exit `0` under the default exit map, as for any risk-only finding) instead of `API_BREAK` (exit `2`). Severity presets that map the risk category to a stricter level still gate. This is the approved change. A run that also has a BREAKING finding still exits `4`.
- **Header-backed runs are unchanged.** A kind with observed evidence keeps `API_BREAK`, verified by the positive control in each parametrized test.
- **Suppression.** A rule with `reachability: unreachable-only` now matches `std_reexport_removed` and `tag_type_renamed` when their reachability is unknown, because they no longer assert public reachability. This is the same behaviour every detector without a reachability tag already has (the default is `public_reachable=False`), so the two detectors now follow the general rule instead of a special case. The suppression is still recorded (ADR-067).
- **Release recommendation.** `recommend_release` reads the verdict, so a header-less enum rename that was a MAJOR bump (API_BREAK) is now a non-MAJOR bump (COMPATIBLE_WITH_RISK). A source break the run cannot back is a semver judgement that now needs the header evidence; `tests/test_workflow_scenarios.py` pins the new answer. This is a consequence of the approved verdict change and should be reviewed with it.
- **Policy precedence is preserved.** Because the cap sits after the policy, a policy's answer for a binary-gated kind is no longer silently replaced by the gate in either direction.
- **Mixed runs.** When the old side is header-backed and the new side is binary-only (or the reverse), a finding whose subject resolves to evidence on either side keeps `API_BREAK`. The gate asks whether the *claim* was observed by a producer that read a header, and one side's header observation is enough for that.

## Alternatives considered

- **Per-detector fixes only.** Making each detector require observed evidence fixes the instances. It does not close the class: the next detector that uses the export population repeats the mistake, as the existing "public by construction" comment and the hard-coded `public_reachable=True` sites already show. Kept as a cleanup, not as the mechanism.
- **Linkage-based classification.** Choosing a binary or source kind by linkage (`is_inline`, COMDAT, weak binding) is a classification choice, not proof of a source contract. Demoting vanished weak or vague-linkage exports on that basis was reverted twice (see `known-gaps.md`, "Do not re-attempt"). Not used.
- **Default-keep for an unresolved subject.** Rejected. A binary-producible producer always has a subject, so an unresolved subject in a binary-only run is a missing observation, and the gate narrows the conclusion rather than keeping an unproven claim.

## Testing

`tests/test_binary_gated_contract.py`:

- **Completeness.** The three buckets cover every `API_BREAK_KINDS` member, are pairwise disjoint, and each non-binary bucket states a reason. A new API_BREAK kind fails until it is placed.
- **Generalized gate, parametrized over `BINARY_GATED`.** For each kind: a binary-only comparison with an injected worst-case claim is not `API_BREAK`, the finding is demoted with its rule id, and the ledger records `binary_gated_api_break`. The same claim over a header-backed fixture keeps `API_BREAK` (positive control). A binary export alone does not keep the claim.
- **Evidence label.** A demoted finding reads `contextual_risk`; a header-backed `API_BREAK` keeps `source_contract`.
- **Subject resolution.** Evidence on a neighbouring declaration does not back an unrelated subject; an unresolved subject is demoted in a binary-only run and not in a header run.
- **Predicate.** A header parse, a non-binary contract judgement and a located record are evidence; a DWARF export, a legacy `PUBLIC` visibility and a record without a header location are not.
- **Detectors.** The two hard-coded sites now report unknown reachability without evidence and proven reachability with it.
- **Real pipeline (`integration`).** A castxml-dumped header-backed type removal is kept; the same library dumped binary-only is demoted.
- **Bucket probes (`integration`, `tests/test_binary_gated_probe_integration.py`).** A per-kind fixture table compares real old/new libraries binary-only and with headers. Every binary-only `API_BREAK` kind must be in `BINARY_GATED` and end at `COMPATIBLE_WITH_RISK`; a header-gated kind must be absent binary-only. Unprobed kinds carry a reason in a shrink-only table.
- **Control.** An exported function removal stays `BREAKING`.

## Not done

- `unspecified_return_now_named` and `experimental_removed_without_replacement` still assert reachability from the export population. The verdict is correct because the gate ignores tags, but the tags overstate. Tracked in `known-gaps.md`.
- The kABI and Python `.pyi` axes have their own evidence gates. This ADR does not build one for them.
- **A demotion does not yet state a coverage gap.** `tests/test_family_f1_evidence_ablation.py` requires that a run whose verdict narrows because a fact became unknown report that narrowing (a lower confidence, a new coverage warning, or a changed assurance record). A demoted finding does not do so yet: the verdict narrows and the finding records its rule, but `DiffResult.coverage_warnings` gains nothing. The natural place for that warning is `checker.py`, which is at its no-growth ratchet, so it needs a responsibility move there rather than a raised baseline. See `known-gaps.md`.
