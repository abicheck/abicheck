# ADR-085: Template-Parameter Default Arguments Are a Recorded Fact, and a Header-Level Default Change Is an API Break

**Date:** 2026-10-21
**Status:** Accepted (2026-10-21). Implementation: see the Implementation slices below and [G28 Phase 5](../plans/g28-castxml-clang-l2-parity-hardening.md).

Governed by: [ADR-009](009-verdict-system-and-exit-codes.md) (verdict and exit codes), [ADR-028](028-source-build-evidence-pack.md) (artifact evidence is authoritative), [ADR-061](061-responsibility-package-architecture.md) (owner of each stage), [ADR-063](063-one-semantic-pipeline.md) (the `Fact` availability model that the schema history cites for field conversion), [ADR-084](084-binary-depth-source-contract-gate.md) (`API_BREAK` buckets and the binary-depth gate).

## Context

A template parameter may carry a default argument: `template <typename T, typename U = T> struct Box;`. Changing that default changes which type `Box<char>` names. Whether the change breaks a consumer depends on what the library exports and what its public signatures name.

Three shapes are relevant, measured on both header backends (`compile.frontend: castxml` and `clang`) in G28 Phase 5:

- **A. An exported instantiation carries the default** (`template class descriptor<float>;`, `case87_default_template_arg_changed`). The default-substituted symbol changes. The current detector `detect_default_template_arg_changed` (`abicheck/compare/template_param_defaults.py`) pairs the removed and added instantiation and reports `DEFAULT_TEMPLATE_ARG_CHANGED`. This is `BREAKING` on both backends. It is binary evidence, so `BREAKING` is correct.
- **B. Header-only, nothing public names the template.** No symbol and no public signature depends on the default. CastXML emits no record at all for an uninstantiated class template, so the change reads `NO_CHANGE`. The clang AST has the template pattern and `TemplateTypeParmDecl.defaultArg`, but `dumper_clang.py` does not record the default, so it also reads `NO_CHANGE`. A consumer's own `Box<char>` compiles against a different type, which is a source-level change that the L4 source replay reports as a risk (`public_dependency_role_changed`).
- **C. Default-substituted type reaches a public signature** (`void take(const Box<char>&)`). The mangled name changes, and the change is reported as `func_removed` plus `func_added` with no default label on either backend. The verdict is correct and only the explanation is missing.

Two facts are in tension:

1. `DEFAULT_TEMPLATE_ARG_CHANGED` is registered as `BREAKING`. Shape B is a source-level consequence with no exported symbol behind it, so it has nowhere to land. Today it reads `NO_CHANGE` and is recorded as a known gap, not as a verdict.
2. The clang dumper has the default available and discards it. castxml cannot provide it at all. A detector that fires only where clang can see the default would make the same header produce a different verdict depending on `compile.frontend`. That is the producer-dependent verdict the parity gate exists to prevent.

The current known-gaps entry (*A header-only default template-argument change reads `NO_CHANGE` at L2 on both backends*) is deliberately open with no detector change. This ADR decides how to close it.

## Decision

### D1. The snapshot records template-parameter defaults as an availability-carrying fact

Add the default to the ABI model, not the dumper. `model/` owns ABI entity values shared across stages (ADR-061), so the field lives on the template pattern `RecordType` (the entity with `is_template_pattern`) and is not a dumper-local attribute.

- Field: `RecordType.template_param_defaults`, typed as a `Fact` (`Fact[tuple[str | None, ...]]`), one entry per template parameter in declaration order. An entry is the spelled default argument, or `None` when that parameter has no default.
- Three states, decided by the fact's availability rather than by a sentinel value:
  - **Recorded, with defaults:** the producer observed the template pattern and its parameters. Each entry is a spelling or `None`.
  - **UNKNOWN / not collected:** the producer did not record the field. This is the state for every castxml snapshot and every snapshot written before this schema.
  - **Never "unchanged" from absence.** An UNKNOWN side never compares equal to a recorded side.
- Persisted through `storage/fact_codec.py` with its own availability family, so it follows the same rules as the other `Fact` fields.
- **Schema bump: `SCHEMA_VERSION` 60 → 61.** A pre-61 reader would silently drop the field and read the pattern as defaultless, which is the failure the bump exists to prevent. The version history in `storage/snapshot_schema_versions.py` records the bump the same way as v60.

Scope of the first slice: class templates only. Function-template default arguments are a follow-on under the same D1 contract.

### D2. A header-level default change is a new `API_BREAK` kind; `BREAKING` stays for exported-symbol evidence

Introduce `TEMPLATE_PARAM_DEFAULT_CHANGED` (name to be checked against `model/change_catalog/kind_names_*.py` at implementation), with `default_verdict = API_BREAK`. It is emitted when:

- both sides carry recorded defaults (D1) for the same template pattern,
- a parameter's default spelling differs, and
- no `DEFAULT_TEMPLATE_ARG_CHANGED` finding already covers the same template. Shape A keeps its `BREAKING` finding and does not also emit this kind, so one root cause is not counted twice. The covered relation is recorded as `DEDUPLICATED` with its rule, per the "record before disposing" rule in `AGENTS.md`.

`DEFAULT_TEMPLATE_ARG_CHANGED` keeps `BREAKING` and its exported-instantiation meaning. It is unchanged by this ADR.

**Bucket (ADR-084).** The new kind is produced only from header data, so it is `HEADER_OR_SOURCE_GATED`, with a `NON_BINARY_REASONS` entry naming the public header input. A binary-only run never produces it.

**Rejected: one kind with evidence-tiered verdict.** Keep `DEFAULT_TEMPLATE_ARG_CHANGED` for both shapes and let an `effective_verdict` drop it to `API_BREAK` when no exported symbol changed. Rejected because the registry's `default_verdict` would stop describing the kind. Every consumer that reads a kind's verdict from the registry, including the `*_KINDS` sets derived from it, would be wrong for one of its two evidence tiers. ADR-084's gate writes no verdict and only caps `API_BREAK` findings, so using it to demote a `BREAKING` kind would invert its contract. The rejected option is also the one that would weaken the "`BREAKING` means symbol evidence" meaning this ADR is trying to keep.

**Rejected: keep shape B `BREAKING` at header level.** This is what the registry says today. Rejected because a header-only edit with no exported symbol and no public signature is not an ABI break. It is a source-compatibility break in the `API_BREAK` sense that ADR-084 defines.

**Rejected: `COMPATIBLE_WITH_RISK`.** A risk-level verdict would let a changed default pass a default gate. The user's contract for source-level changes is `API_BREAK`, which fails the gate the same way other source-compatibility breaks do.

### D3. Backend coverage is an explicit assurance gap, not a silent no-change

`compile.frontend: castxml` cannot populate D1's fact. Its snapshots therefore carry the UNKNOWN state, and the run reports reduced coverage for this fact:

- a castxml run where a template pattern's defaults are UNKNOWN on either side emits a coverage/assurance gap naming `template-parameter defaults (castxml cannot record them)`, and does not produce `NO_CHANGE` for that template;
- a clang run where both sides are recorded decides the verdict from D2;
- **the verdict must not differ between backends for the same observed evidence.** When both backends observe the same defaults, they agree. Where one backend observed nothing (castxml), the result is a coverage gap, not a clean verdict. The two outcomes are different claims, so "different verdict" is not a parity violation here. A parity violation would be the same observed defaults producing different verdicts, and this design rules that out.

This resolves the Phase 5 rationale against a clang-only detector. The earlier objection was to a `BREAKING` verdict with no binary evidence that castxml could reproduce. Under D2 the verdict is `API_BREAK`, and under D3 castxml does not claim the opposite. It reports that it cannot see the fact.

Consequence for shape B: once implemented, a clang run returns `API_BREAK` where it returned `NO_CHANGE`, and a castxml run returns a coverage gap where it returned `NO_CHANGE`. The `NO_CHANGE` answer in G28 Phase 5 is superseded by this ADR when it is accepted.

**Amendment (2026-10-23): the castxml verdict stays `NO_CHANGE`.** The bullet
"does not produce `NO_CHANGE` for that template" and the castxml half of
"Consequence for shape B" above are amended. This was the open question in
*Remaining* item 1, and the maintainer's delegate decided it as follows:

- A castxml run whose headers declare a template-parameter default, where the
  defaults are not recorded, reads `partial`. The analysis-assurance status
  names the gap `template-parameter defaults (castxml cannot record them)` for
  each castxml side whose header scan found one (ADR-085 D3, slice 4). The
  compare verdict for that template stays `NO_CHANGE`. castxml cannot observe
  the change, so the verdict does not claim one.
- Only the clang answer is superseded: a clang run on shape B reads `API_BREAK`
  (`TEMPLATE_PARAM_DEFAULT_CHANGED`) instead of `NO_CHANGE`.

Rationale, from the product rules in `AGENTS.md`: weaker evidence narrows
conclusions and is reported, but it never fabricates a finding. The verdict
reports what was observed; assurance reports what could not be observed. A
castxml verdict of anything but `NO_CHANGE` would be a finding with no observed
evidence behind it, and it would break the same-evidence-same-verdict parity
that the test plan requires. A castxml run that reads `NO_CHANGE` with
`partial` assurance is not a clean pass: the gap is named, and the run is not
reported as complete.

The test plan's shape-B castxml expectation is amended the same way: the castxml
run reads `NO_CHANGE` with `partial` assurance and the named gap, pinned by
`tests/test_default_template_arg_l2_matrix.py` (including on
`catalog/cases/case224_header_only_template_default_added`).

## Consequences

- **Behaviour change (after acceptance).** A clang run on shape B changes from `NO_CHANGE` to `API_BREAK`. This is a new finding on a header-only default change. It needs a changelog fragment and is reviewed as a policy change, not a fix.
- **Coverage now says something.** A castxml run on a template with recorded-elsewhere defaults reports the gap instead of a clean result. This is the intended direction (ADR-028: weaker evidence narrows the conclusion) but it will show up in existing castxml reports.
- **Schema v61.** Stored baselines written before this version load the fact as UNKNOWN. A pre-61 reader rejects the document rather than dropping the field.
- **Known-gap closure.** The known-gaps entry stops reading as "deliberate, no detector change" and points here. It closes when D1–D3 land.
- **Kind count.** One new `ChangeKind`. The `<N> ChangeKind` counts in the docs must follow `len(ChangeKind)` (`check_ai_readiness.py` doc-count-sync).

## Migration

1. **Stored snapshots.** Pre-v61 snapshots load the field as UNKNOWN. They are not re-classified. Comparing a pre-v61 baseline with a v61 dump of the same headers reports a coverage gap for the template, not `NO_CHANGE` and not `API_BREAK`. The fix for the user is to re-dump the baseline. Nothing silently changes.
2. **Mixed producers.** A castxml baseline against a clang candidate is UNKNOWN on the castxml side, so it reports the gap. It is not compared as if both sides carried defaults.
3. **Existing `DEFAULT_TEMPLATE_ARG_CHANGED`.** No change in kind, verdict or detector behaviour for shape A.
4. **Readers.** The v61 bump follows the existing `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` path, so an older reader fails loudly.

## Test plan

Each claim below is tested through the public workflow (`abicheck dump` then `abicheck compare`), and the rendered report is checked, not only the internal detector.

- **Both backends, shapes A/B/C.** Run `case87_default_template_arg_changed` (A) and the two inline headers (B and C) with `compile.frontend: castxml` and `clang`. Pin:
  - A: `BREAKING`, `DEFAULT_TEMPLATE_ARG_CHANGED` on both backends (unchanged); the new kind is not emitted for it.
  - B: clang `API_BREAK`, `TEMPLATE_PARAM_DEFAULT_CHANGED`; castxml `NO_CHANGE` with `partial` assurance and the named gap (amended 2026-10-23, see D3).
  - C: `BREAKING` via `func_removed`/`func_added` on both backends (unchanged); the new kind is not emitted when the mangled change already reports the break.
- **Parity.** For each shape where both backends observe the same defaults, the verdict and kind set are equal. A parametrised test asserts this, so a backend that drops the fact fails it.
- **Old-snapshot comparison.** A v60 fixture snapshot (from before the bump) compared with a v61 clang dump of the same header yields a coverage gap for the template and no `NO_CHANGE` and no `API_BREAK`. A v61 snapshot round-trips the fact including the `None`-per-parameter entries.
- **Schema.** A pre-61 reader's rejection path is exercised by the existing version-history test pattern; a v61 document with the key absent decodes as UNKNOWN, not as no-defaults.
- **Classification gates.** The new kind is classified in `tests/canonical_identity_contract.py` (exactly one of `TYPE_BEARING`, `VALUE_INSENSITIVE`, `UNVERIFIED`), and `ADR-084` bucket membership is asserted in `policy/binary_gated_contract.py`'s tests.
- **Surface traceability.** `docs/contribute/adr/adr-surface-registry.yaml` lists this ADR; `scripts/check_adr_surfaces.py` passes; the baseline is rewritten only if a surface is deliberately downgraded.

## Implementation slices

Each slice is independently reviewable and is not started until this ADR is accepted.

1. **Model and schema.** `RecordType.template_param_defaults` as a `Fact`, `storage/fact_codec.py` encode/decode with availability family, `SCHEMA_VERSION` 61 with its history note, and the old-snapshot UNKNOWN load path. No detector, no behaviour change.
2. **Clang records defaults.** `dumper_clang.py` populates the fact from `TemplateTypeParmDecl.defaultArg` for class templates. castxml writes nothing, so its snapshots stay UNKNOWN.
3. **New kind and detector.** `TEMPLATE_PARAM_DEFAULT_CHANGED` in the registry and kind names, a detector in `compare/template_param_defaults.py` gated on both sides being recorded, the shape-A suppression, `HEADER_OR_SOURCE_GATED` membership with its non-binary reason, and the canonical-identity classification.
4. **Assurance gap for castxml.** The UNKNOWN-on-either-side coverage/assurance gap, its rendering in the report, and the header-backend capability reference (`docs/reference/header-backend-capabilities.md`, Known gaps section) updated to the new behaviour.
5. **Closure.** Update the known-gaps entry to closed, the G28 Phase 5 plan, the ADR index status, the surface registry, and a changelog fragment.

## Implementation status (2026-10-22)

Landed on `feat/adr-085-template-default-args`. Deviations from the text above
are recorded here rather than edited into the decision.

- **Schema version is 62, not 61.** `SCHEMA_VERSION` was already 61: the
  `RecordType.specialization_declared_fact` change (ADR-063 follow-up, #1684)
  took it. The new field is v62, with its history note in
  `storage/snapshot_schema_versions.py` and its availability constant in
  `storage/fact_schema_versions.py`.
- **Slice 1 (model and schema): landed.** `RecordType.template_param_defaults_fact`,
  codec in `storage/fact_codec.py` with its validator in
  `storage/fact_payload_validation.py`, encode-drop in `storage/snapshot_encode.py`,
  registry entry in `model/fact_registry_entries_types.py`.
- **Slice 2 (clang records defaults): landed, for type-parameter templates only.**
  The clang walk reads `TemplateTypeParmDecl.defaultArg` through the merged
  defaults index. A template with any non-type or template-template parameter
  is not recorded (UNKNOWN), because its default spelling is not read. This is
  a conservative limit, not a decision, and it is listed under *Remaining*.
- **Slice 3 (kind and detector): landed.** `TEMPLATE_PARAM_DEFAULT_CHANGED`
  (`API_BREAK`, `HEADER_OR_SOURCE_GATED`, `UNVERIFIED` identity bucket beside its
  shape-A sibling). Detector in `compare/template_param_defaults.py`; the
  pipeline entry in `policy/template_default_changes.py` records each shape-A
  relation as `DEDUPLICATED` in the disposition ledger.
- **Slice 4 (castxml assurance gap): landed (ADR-085 D3).** castxml cannot
  record the defaults, so its run scans the public headers it parsed:
  `extract/headers/template_default_scan.py` looks for a template parameter
  list with a top-level default argument, and the result is persisted as
  `AbiSnapshot.template_default_header_scan` (schema v63; `None` when no scan ran).
  The rollup in `policy/analysis_assurance/_template_defaults.py` reports the
  gap `template-parameter defaults (castxml cannot record them)` for each castxml
  side whose scan is `True`, and the run reads `partial`. The compare verdict
  stays `NO_CHANGE`: castxml still cannot see the change, so only the status
  reports the gap. The rollup also reports a gap for any class-template pattern
  whose defaults are not recorded, which covers pre-v62 snapshots and
  non-type-parameter templates on clang.
- **Slice 5 (closure): partial.** The known-gap entry is PARTIAL, not closed. The
  G28 Phase 5 section reflects the same state.

## Remaining

1. **Verdict decision (resolved 2026-10-23).** The castxml verdict stays
   `NO_CHANGE`, and the run reads `partial` with the gap named. D3 is amended
   above. Rationale: weaker evidence narrows the conclusion and is reported; it
   does not fabricate a finding, and this keeps same-evidence-same-verdict
   parity. Pinned by `tests/test_default_template_arg_l2_matrix.py`.
2. **Parity test.** Landed for the header-only shape: clang reports
   `template_param_default_changed`, castxml reports the gap and no kind
   (`tests/test_default_template_arg_l2_matrix.py`).
3. **Non-type and template-template parameters.** Record their defaults so such
   a template is not UNKNOWN.
4. **Function-template defaults.** Same D1 contract; a separate slice, as above.

## Not done

- Function-template default arguments (same D1 contract, separate slice).
- Source-replay (L4) wording for shape B beyond the existing `public_dependency_role_changed` risk.

Index: [ADR index](index.md)
