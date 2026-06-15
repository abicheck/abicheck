# G20 — Source-Scan & Cross-Source Example Catalog

**ADR:** [ADR-035](../adr/035-pr-tier-source-intelligence-and-crosscheck.md)
(G19 shipped the engine; this plan grows the *demonstration corpus* for it)
**Type:** Catalog/test extension (phased) · **Effort:** L · **Risk:** low —
additive example cases + test scenarios; no detector or policy change.

## Problem

ADR-035 (G19) landed the engine for cheap PR source scans, intra-version
cross-source validation, single-release audit, and evidence-directed focusing.
The detection code exists; the **demonstration corpus does not**. The
`examples/` catalog still tells exactly one story — a `v1`→`v2` **binary diff**:

| min_evidence | cases | exercises |
|---|---|---|
| L0 (binary) | 50 | exports / sizes |
| L1 (+debug) | 65 | DWARF layout |
| L2 (+headers) | 23 | header AST |
| L3 (+build) | 4 | flag / toolchain drift |
| L4 (+source) | 1 | source replay |
| L5 (graph) | **0** | — |

Zero cases produce any of the eight ADR-035 cross-check / audit `ChangeKind`s
(`exported_not_public`, `public_not_exported`, `header_build_context_mismatch`,
`private_header_leak`, `odr_type_variant`, `public_to_internal_dependency`,
`unversioned_exported_symbol`, `rtti_for_internal_type`) even though all are
defined in `checker_policy.py` and implemented in `buildsource/crosscheck.py`.

The catalog therefore showcases *detection depth on a binary delta* and
**nothing** about ADR-035's actual thesis: (a) value from one build with no
baseline, (b) multi-source corroboration where the combination beats any single
source, and (c) one source steering another's collection.

## Goal & acceptance criteria

Grow the corpus along the three ADR-035 capability axes. Per the maintainer
decision, flagship demos land as first-class `examples/caseNN` entries (visible
in the encyclopedia), edge/integrity/plan cases land as test-only scenario
suites (compiler-free, fast lane) modelled on `tests/test_pattern_audit_scenarios.py`.

- **G20.1 — Source-scan / single-release audit corpus.** At least four catalog
  cases reach a verdict from **one artifact, no baseline** (D8 audit), covering
  `exported_not_public`, `private_header_leak`, `unversioned_exported_symbol`,
  `rtti_for_internal_type`; plus one S3→S5 "depth ladder" case showing the same
  input answered at three depths with an honest coverage block.
- **G20.2 — Cross-source corroboration corpus.** At least three catalog cases
  whose finding is invisible/ambiguous to any single source and resolves only by
  crosschecking two: `header_build_context_mismatch` (L2 macros ↔ L3 flags),
  `odr_type_variant` (L4 per-TU layout ↔ layout), and the
  `exported_not_public`/`public_not_exported` bidirectional pair (L0 exports ↔ L2
  decls). One case asserts the §6.8 provider-agreement matrix drives the
  **confidence tag** differently for 3-provider vs 1-provider corroboration.
- **G20.3 — Evidence-directed focusing corpus.** Test scenarios asserting on the
  **POI set / scan plan** (not just verdict): an export delta targeting a single
  TU's replay, a macro-conditional layout scoping macro capture, the D7
  changed-path floor (mis-weighted `risk_rules` cannot drop a changed TU), and
  the D4 "unlinked source evidence" integrity guard (the oneDAL failure shape).
- **Acceptance gate:** every new catalog case has a `README.md` + a
  `ground_truth.json` entry + a regenerated `docs/examples/` page; the
  AI-readiness `examples-*` and `doc-count-sync` checks stay green; the FP-rate
  gate keeps its 0/0 baseline (new cross-check cases enter the corpus only if the
  correct implementation already passes them).

## Enabling work — the harness assumes `v1`/`v2` binary diff

Buckets 1–3 cannot be hosted as-is. Required schema/harness extensions, smallest
first:

1. **`ground_truth.json` schema (v4).** Add optional `expected_crosscheck_kinds`
   (subset check against `run_crosschecks` output), an optional `providers` /
   confidence expectation (for the §6.8 matrix case), `min_evidence: L5`, and
   support for **baseline-less** cases (D8 audit cases have no `v2`). Update the
   `cross_references` block and `scripts/evidence_tiers.py` mapping. Keep
   `tests/test_example_autodiscovery.py::EXPECTED` and
   `tests/test_evidence_tiers.py` in sync.
2. **New per-case fixture types.** Allow a case dir to ship
   `compile_commands.json`, an `install_manifest` (leak / unversioned cases),
   multi-TU source sets (ODR), and a `.abicheck.yml` (risk-rule cases). Prefer
   the existing **Flow-2 `abicheck_inputs/`** pack format
   (`buildsource/inputs_pack.py`) so fixtures ingest **without a live compiler**
   wherever possible.
3. **Scan-plan assertion surface.** Bucket 3 asserts on `ScanResult` /
   `scan --estimate` counters (selected/parsed/skipped TU, cache hit/miss,
   matched/unmatched exports). Expose these from the test harness; most Bucket 3
   cases run compiler-free against synthetic `AbiSnapshot` pairs (the
   `tests/test_crosscheck.py` `_snap(**kw)` pattern already does this).
4. **Test lane wiring.** Synthetic-snapshot crosscheck + POI cases run in the
   **fast lane** (Python only). L4-bearing catalog cases (ODR, depth ladder) are
   `integration` (castxml) — mark them, do not skip silently. Wire
   `ABICHECK_MIN_EXECUTED` for any new external-tool lane.

## Phases

### Phase 0 — schema + harness (enabling)
Land the `ground_truth.json` v4 schema, fixture-type support, and scan-plan
assertion surface above. No new cases yet; existing 143 stay green. Update
`examples/CLAUDE.md` per-case-layout section to document the new fixture types.

### Phase 1 — single-release audit (G20.1)
Compiler-free / smallest fixtures first. New catalog cases:

| Case | Kind | Sources combined | Fixture |
|---|---|---|---|
| `case143_audit_accidental_export` | `exported_not_public` | binary exports ↔ L2 header decls | `.so` + `include/` + manifest |
| `case144_audit_private_header_leak` | `private_header_leak` | L5 include graph ↔ install manifest | public hdr → `detail/` hdr + manifest |
| `case145_audit_unversioned_export` | `unversioned_exported_symbol` | export ↔ `.gnu.version_d` | versioned `.so` + 1 new bare export |
| `case146_audit_rtti_for_internal` | `rtti_for_internal_type` | `_ZTI`/`_ZTV` ↔ private-header type | internal type w/ RTTI emitted |
| `case147_scan_depth_ladder` | pattern → semantic | S3 lexical vs S5 replay, same input | header w/ `#pragma pack` + TU |

`case147` is the legibility anchor: same input, three depths, coverage block
shows what each depth proved.

### Phase 2 — cross-source corroboration (G20.2)
The "1+1 > 2" flagship. Compiler-free synthetic snapshots where possible.

| Case | Kind | Why no single source sees it |
|---|---|---|
| `case148_xcheck_header_build_mismatch` | `header_build_context_mismatch` | binary-only blind; header parsed w/o `-DBIG_BUFFERS` is wrong; needs L2 macros ↔ L3 flags |
| `case149_xcheck_odr_variant` | `odr_type_variant` | two TUs, divergent layout of one public type; only L4 layout ↔ layout |
| `case150_xcheck_export_public_pair` | `exported_not_public` + `public_not_exported` | bidirectional L0 exports ↔ L2 decls |
| `case151_xcheck_confidence_matrix` | (reuses one kind) | 3-provider vs 1-provider corroboration → different confidence tag (§6.8) |

`case148` is the flagship — the clearest demonstration that combining L2 + L3
exposes a divergence neither shows alone.

### Phase 3 — evidence-directed focusing (G20.3)
Test-only scenario suites (`tests/test_poi_scenarios.py`,
`tests/test_source_evidence_integrity.py`) asserting on the **scan plan**:

- `poi_export_delta_targets_replay` — changed export, unchanged header → POI
  resolves symbol → source decl → replays **only that TU**; assert
  `selected_tu == 1`, unrelated body skipped.
- `poi_macro_conditional_layout` — macro capture scoped to materializing TUs only.
- `poi_template_instantiation_seed` — demangled exported template symbol seeds
  which instantiations replay.
- `poi_changed_path_floor` — mis-weighted `risk_rules`; assert the changed TU is
  **still** in the POI set (D7 floor: risk adds, never drops).
- `integrity_unlinked_source_evidence` — oneDAL shape: many exports, TUs parsed,
  **zero matched symbols**; assert reported as *degraded/unlinked source
  evidence* with boundary counters, **not** counted as clean L4 coverage.

## Sequencing rationale

Land compiler-free, high-payload cases first so the catalog grows without an
external-tool dependency while exercising genuinely-new code paths:

1. **Phase 2 crosschecks** — highest ADR-035-thesis payload, buildable as
   synthetic `AbiSnapshot` pairs today.
2. **Phase 1 audit** — single-artifact, smallest fixtures, immediate value.
3. **Phase 3 POI/integrity** — strongest "sources guide sources" narrative but
   needs the Phase 0 scan-plan assertion surface.

## Use-case tracking

Add `planned` entries to `docs/development/usecase-registry.yaml` under a new
gap **G20**, one per phase (G20.1/G20.2/G20.3), cross-referencing the G19
engine entries and the ADR-035 decisions each case demonstrates (D2/D4/D7/D8).

## Relationship to existing work

- **G19 / ADR-035** — this consumes the engine G19 shipped; no engine change.
- **`tests/test_pattern_audit_scenarios.py`** — the model for the test-only
  scenario suites (Phase 3 + the synthetic Phase 2 cases).
- **`tests/test_crosscheck.py`** — the `_snap(**kw)` synthetic-snapshot pattern
  Phase 2 reuses.
- **G11 single-binary audit** — Phase 1 audit cases extend its surface tooling.
- **`scripts/check_ai_readiness.py`** — `examples-ground-truth`,
  `examples-readme-sync`, `doc-count-sync` gate every new catalog case.
