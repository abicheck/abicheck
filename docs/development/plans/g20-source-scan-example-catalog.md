# G20 — Source-Scan & Cross-Source Example Catalog

**ADR:** [ADR-035](../adr/035-pr-tier-source-intelligence-and-crosscheck.md)
(G19 shipped the engine; this plan grows the *demonstration corpus* for it)
**Type:** Catalog/test extension (phased) · **Effort:** L · **Risk:** low —
additive example cases + test scenarios; no detector or policy change.
**Status:** planned.

---

## 1. Problem

ADR-035 (G19) landed the engine for cheap PR source scans, intra-version
cross-source validation, single-release audit, and evidence-directed focusing.
The detection code exists and is unit-tested (`tests/test_crosscheck.py`,
`tests/test_pattern_scan.py`, `tests/test_poi.py`, `tests/test_cli_scan.py`).
What does **not** exist is a *demonstration corpus* — cases a maintainer can read
to understand what the multi-source machinery buys them.

The `examples/` catalog (143 cases) still tells exactly one story: a `v1`→`v2`
**binary diff**. Evidence-tier distribution proves the skew:

| min_evidence | cases | exercises |
|---|---|---|
| L0 (binary) | 50 | exports / sizes |
| L1 (+debug) | 65 | DWARF layout |
| L2 (+headers) | 23 | header AST |
| L3 (+build) | 4 | flag / toolchain drift |
| L4 (+source) | 1 | source replay |
| L5 (graph) | **0** | — |

**Zero** catalog cases produce any of the eight ADR-035 cross-check / audit
`ChangeKind`s, even though all eight are defined in `checker_policy.py`,
implemented in `buildsource/crosscheck.py`, and already mapped in
`scripts/evidence_tiers.py`:

| ChangeKind | partition | evidence tier (already mapped) |
|---|---|---|
| `exported_not_public` | RISK | L2 |
| `public_not_exported` | RISK | L2 |
| `private_header_leak` | RISK | L2 |
| `public_to_internal_dependency` | RISK | L4 |
| `unversioned_exported_symbol` | RISK | L0 |
| `rtti_for_internal_type` | RISK | L2 |
| `header_build_context_mismatch` | API_BREAK | L3 |
| `odr_type_variant` | API_BREAK | L4 |

So the catalog showcases *detection depth on a binary delta* and **nothing**
about ADR-035's three theses:

1. **Value from one build, no baseline** (D8 single-release audit).
2. **Multi-source corroboration** — a finding invisible or ambiguous to any one
   source, resolved by crosschecking two (D4), with confidence driven by *how
   many* providers agree (§6.8).
3. **Sources steer sources** — cheap L0/L1/L2 deltas focus the expensive L4/L5
   scan (D7 POI), and an L4 run that fails to link is reported as degraded, not
   clean (D4 integrity gate).

This plan closes the demonstration gap along those three axes.

---

## 2. Goal & acceptance criteria

Per the maintainer decision (both locations): **flagship demos** land as
first-class `examples/caseNN` entries (visible in the encyclopedia);
**edge/integrity/plan** cases land as test-only scenario suites (compiler-free,
fast lane) modelled on `tests/test_pattern_audit_scenarios.py` and the
`_snap(**kw)` synthetic-snapshot pattern in `tests/test_crosscheck.py`.

- **G20.1 — Single-release audit corpus (D8).** ≥4 catalog cases reach a verdict
  from **one artifact, no baseline**, covering `exported_not_public`,
  `private_header_leak`, `unversioned_exported_symbol`, `rtti_for_internal_type`;
  plus one S3→S5 "depth ladder" case showing the same input answered at three
  depths with an honest coverage block.
- **G20.2 — Cross-source corroboration corpus (D4).** ≥3 catalog cases whose
  finding is invisible/ambiguous to any single source and resolves only by
  crosschecking two: `header_build_context_mismatch` (L2 macros ↔ L3 flags),
  `odr_type_variant` (L4 layout ↔ layout), and the `exported_not_public` /
  `public_not_exported` bidirectional pair (L0 exports ↔ L2 decls). One case
  asserts the §6.8 provider-agreement matrix drives the **confidence tag**
  differently for 3-provider vs 1-provider corroboration.
- **G20.3 — Evidence-directed focusing corpus (D7).** Test scenarios asserting on
  the **POI set / `ScanResult` counters** (not just verdict): export delta
  targeting one TU's replay, macro-conditional layout scoping macro capture, the
  D7 changed-path floor (mis-weighted `risk_rules` cannot drop a changed TU), and
  the D4 "unlinked source evidence" integrity guard (the oneDAL failure shape).
- **Acceptance gate (every phase):** each new catalog case has a `README.md`, a
  `ground_truth.json` entry, and a regenerated `docs/examples/` page; the
  AI-readiness `examples-ground-truth`, `examples-readme-sync`, `doc-count-sync`,
  and `changekind-detector`/`changekind-docs` checks stay green; the FP-rate gate
  keeps its **0/0** baseline (new cases enter the corpus only if the current,
  correct implementation already passes them).

---

## 3. Enabling work (Phase 0) — the harness assumes `v1`/`v2` binary diff

The catalog harness (`tests/test_example_autodiscovery.py`,
`tests/test_abi_examples.py`, `scripts/evidence_tiers.py`,
`scripts/gen_examples_docs.py`) hard-assumes a `v1`/`v2` compilable pair plus a
binary diff. Buckets 1–3 need three of these shapes it cannot host today:
**baseline-less** cases (audit), **multi-source single-build** cases (crosscheck),
and **scan-plan-assertion** cases (POI). Phase 0 lands the minimum harness work;
no new cases yet, all 143 existing cases stay green.

### 3.1 `ground_truth.json` schema v4

Bump `version` `"3"`→`"4"`. Add to each verdict entry (all optional, defaulted):

| field | type | meaning |
|---|---|---|
| `mode` | `"compare"` (default) \| `"audit"` | `"audit"` = single-build, no `v2`/baseline |
| `expected_crosscheck_kinds` | `list[str]` | subset-checked against `run_crosschecks(snapshot).findings[].kind` (distinct from `expected_kinds`, which is the `compare` diff) |
| `expected_providers` | `dict[str, list[str]]` | per check name → expected `ScanResult.confidence[check]` provider list (the §6.8 matrix) |
| `expected_scan` | `dict[str, int\|str]` | scan-plan counters the case asserts (`selected_tus`, `parsed_tus`, `skipped_tus`, `matched_symbols`, `unmatched_exports`, `cache_hits`); used by POI/integrity cases |
| `fixtures` | `list[str]` | declares non-`v1`/`v2` fixture files the case ships (`compile_commands.json`, `install_manifest.txt`, `abicheck_inputs/`, `.abicheck.yml`) |

`min_evidence` already accepts `L0`–`L4`; add `L5` to the accepted set. The eight
kinds are already in `EVIDENCE_TIER_BY_KIND`, so `min_evidence` for a crosscheck
case is derived, not hand-set. Update the `cross_references` block and the
`description` string. Keep `tests/test_example_autodiscovery.py::EXPECTED`,
`tests/test_evidence_tiers.py`, and `tests/test_abi_examples.py` (hardcoded
01–18) in sync — the new fields are additive, so existing rows are untouched.

### 3.2 New per-case fixture types

Extend `examples/CLAUDE.md` "Per-case layout" to document, alongside
`v1`/`v2`/`app`:

```
caseNN_<name>/
├── (audit cases) v1.* + v1.h        # ONE build only, no v2
├── compile_commands.json            # L3 build context (header_build_context_mismatch)
├── install_manifest.txt             # installed-header set (private_header_leak, unversioned)
├── abicheck_inputs/                 # Flow-2 build-emitted facts (preferred — no live compiler)
│   ├── manifest.json
│   └── source_facts/*.jsonl
├── .abicheck.yml                    # risk_rules / crosschecks config (focusing cases)
└── README.md
```

**Prefer the Flow-2 `abicheck_inputs/` pack** (`buildsource/inputs_pack.py`,
`inputs_emit.py`) so L4/L5 fixtures ingest via the existing `merge` path
**without a live compiler** — keeps most new cases in the fast lane. Reserve
castxml-backed live replay (`integration` marker) for the ODR and depth-ladder
cases that genuinely need a second frontend pass.

### 3.3 Scan-plan assertion surface

Bucket 3 asserts on `ScanResult`/`LayerResult`/`CostEstimate` counters already
defined in `service.py` (`LayerResult.facts/elapsed_s/status`,
`CostEstimate.tus/cache_hit_rate`, `ScanResult.confidence/estimate`). Add a thin
test helper (`tests/_scan_fixtures.py`) that runs `service.run_scan(ScanRequest)`
against a case dir and returns the counters named in `expected_scan`, plus a
`service.estimate_scan` path for the `--estimate` selection-vs-parse split
(ADR-035 D7). No engine change — this is a harness/accessor.

### 3.4 Docs generation

Teach `scripts/gen_examples_docs.py` to render the three new shapes: an audit
case (no v1/v2 diff table — instead a "single-build findings" block), a
crosscheck case (a "sources combined" two-column table + provider/confidence
row), and a focusing case (a "scan plan" counter table). Regenerate
`examples/README.md` headline/distribution/case-index regions from
`ground_truth.json` as today.

---

## 4. Phase 1 — single-release audit (G20.1)

Compiler-free / smallest fixtures first. All four hygiene cases are buildable as
a single binary + headers + manifest (or an `abicheck_inputs/` pack), run through
`scan --audit`, asserting `expected_crosscheck_kinds`.

| Case | Kind | Sources combined | Fixture | Lane |
|---|---|---|---|---|
| `case143_audit_accidental_export` | `exported_not_public` | binary exports ↔ L2 header decls | `.so` (or pack) + `include/` | fast (pack) |
| `case144_audit_private_header_leak` | `private_header_leak` | L5 include graph ↔ install manifest | public hdr `#include`s `detail/cfg.h` + `install_manifest.txt` | fast (pack) |
| `case145_audit_unversioned_export` | `unversioned_exported_symbol` | export table ↔ `.gnu.version_d` | versioned `.so` + 1 bare new export | fast (L0) |
| `case146_audit_rtti_for_internal` | `rtti_for_internal_type` | `_ZTI`/`_ZTV` ↔ private-header type | internal class w/ RTTI emitted | fast |
| `case147_scan_depth_ladder` | pattern → semantic | S3 lexical vs S5 replay, same input | header w/ `#pragma pack` + one TU | integration (castxml) |

`case147` is the legibility anchor: identical input scanned at S3 (pattern only,
no compiler), S2 (preprocessor, if compile DB present), and S5 (replay); the
README and the coverage block show **exactly what each depth proved** and what it
could not — the honest-coverage promise of ADR-035 D3, never a bare "scan
failed".

**Example `ground_truth.json` entry (case143):**

```json
"case143_audit_accidental_export": {
  "expected": "RISK", "category": "risk", "mode": "audit",
  "min_evidence": "L2", "platforms": ["linux"],
  "abi_break": false, "api_break": false, "bad_practice": true,
  "expected_kinds": [],
  "expected_crosscheck_kinds": ["exported_not_public"],
  "expected_providers": {"exported_not_public": ["binary_exports", "public_header_ast"]},
  "fixtures": ["abicheck_inputs/"]
}
```

**Acceptance:** `pytest tests/test_abi_examples.py -k case143` (and 144–146) green
in the fast lane; `case147` green under `-m integration`; audit catalog renders
in `docs/examples/`.

---

## 5. Phase 2 — cross-source corroboration (G20.2)

The "1 + 1 > 2" flagship. Each case ships **two evidence sources that disagree or
jointly confirm**; the README's job is to show neither source alone reaches the
finding. Buildable as synthetic `AbiSnapshot` pairs (`_snap(**kw)`) plus a packed
fixture for the catalog rendering.

| Case | Kind | Why no single source sees it | Fixture |
|---|---|---|---|
| `case148_xcheck_header_build_mismatch` | `header_build_context_mismatch` (API_BREAK) | binary-only blind; header parsed without `-DBIG_BUFFERS` reports the *wrong* layout; only L2 macros ↔ L3 flags expose the divergence | `compile_commands.json` (`-DBIG_BUFFERS=1`) + macro-conditional header |
| `case149_xcheck_odr_variant` | `odr_type_variant` (API_BREAK) | two TUs materialize one public type with different layouts; only L4 per-TU layout ↔ layout | 2-TU source set or `abicheck_inputs/` w/ divergent per-TU records |
| `case150_xcheck_export_public_pair` | `exported_not_public` + `public_not_exported` | bidirectional L0 exports ↔ L2 decls: one symbol exported w/ no decl, one decl w/ visibility promise but `static` definition | `.so` + `include/` |
| `case151_xcheck_confidence_matrix` | (reuses `exported_not_public`) | same finding, 3 corroborating providers vs 1 → different `Confidence` tag (§6.8) | two packs: full-provider vs binary-only |

`case148` is the flagship — the clearest demonstration that combining L2 + L3
exposes a divergence neither shows alone. `case151` is the one that demonstrates
"better results from the *combination*" as an **output property**: it asserts
`ScanResult.confidence["exported_not_public"]` lists three providers in the rich
fixture and one in the thin fixture, and that the rendered confidence tag differs.

**Example test assertion (case148, synthetic, fast lane):**

```python
snap = _snap(...)                      # header type w/ macro-conditional layout
snap.build_source = _build(macros={"BIG_BUFFERS": "1"})   # L3 says built WITH it
res = run_crosschecks(snap)
hits = _findings_of(res, ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH)
assert hits and hits[0].confidence == Confidence.HIGH
assert _coverage(res, CHECK_HEADER_BUILD_CONTEXT_MISMATCH)["status"] == "present"
```

**Acceptance:** synthetic assertions in `tests/test_xcheck_scenarios.py` (fast);
catalog cases 148–151 green; FP-rate gate stays 0/0 (add the matching clean
"no-divergence" counterpart for each so the corpus has both a positive and a
negative — proving no false positive on the healthy build).

---

## 6. Phase 3 — evidence-directed focusing (G20.3)

Test-only scenario suites — the *interesting artifact is the scan plan*, so these
assert on `ScanResult`/POI counters, not the verdict. Two new files:
`tests/test_poi_scenarios.py` and `tests/test_source_evidence_integrity.py`.

| Scenario | Asserts | ADR-035 |
|---|---|---|
| `poi_export_delta_targets_replay` | changed export + unchanged header → POI resolves symbol → source decl → `expected_scan.selected_tus == 1`; unrelated body in `skipped_tus` | D7 |
| `poi_macro_conditional_layout` | macro capture runs only for TUs materializing the type; others skipped | D7 |
| `poi_template_instantiation_seed` | demangled exported template symbol seeds which instantiations replay (`selected_tus` matches the instantiation set) | D7 |
| `poi_changed_path_floor` | a deliberately mis-weighted `risk_rules` profile; assert the changed TU is **still** in `build_points_of_interest(...)` output (floor: risk adds, never drops) | D7 floor |
| `integrity_unlinked_source_evidence` | oneDAL shape: many exports, TUs parsed, **zero matched symbols** → `LayerResult.status == "partial"`/degraded with boundary counters; **not** counted as clean L4 coverage; exit code unaffected | D4 integrity |

`poi_changed_path_floor` and `integrity_unlinked_source_evidence` are the two
highest-value guards: the first proves focusing **cannot hide a real change**, the
second proves a failed L4 link is **never silently green** — both are invariants
ADR-035 calls out explicitly (D7 floor; the oneDAL field-failure shape in D4).

**Example assertion (poi_changed_path_floor, fast lane):**

```python
poi = build_points_of_interest(
    changed_paths={"src/widget.cpp"},
    risk=RiskRules.from_dict({"src/**": 0}),   # mis-weighted: zero weight
    pattern_triggers=[], baseline=None, candidate=snap,
)
assert any(p.path.endswith("widget.cpp") for p in poi.items)   # floor holds
```

**Acceptance:** both suites green in the fast lane (Python only, synthetic
snapshots — no compiler); `integrity_unlinked_source_evidence` additionally
asserts the rendered report names the failed boundary class.

---

## 7. Sequencing rationale

Land compiler-free, high-payload cases first so the catalog grows without an
external-tool dependency while exercising genuinely-new code paths:

1. **Phase 0** (enabling) — unblocks everything; no behavior change.
2. **Phase 2 crosschecks** — highest ADR-035-thesis payload, buildable as
   synthetic `AbiSnapshot` pairs today.
3. **Phase 1 audit** — single-artifact, smallest fixtures, immediate value.
4. **Phase 3 POI/integrity** — strongest "sources guide sources" narrative but
   depends on the Phase 0 scan-plan assertion surface.

Each phase is independently shippable behind its own PR; Phase 0 is the only hard
dependency.

---

## 8. Risks & mitigations

- **Catalog count churn.** Adding 9 catalog cases moves the `doc-count-sync`
  headline and `examples/README.md` distribution. *Mitigation:* regenerate via
  `scripts/gen_examples_docs.py` in the same commit; never hand-edit the
  generated regions.
- **FP-rate creep.** New crosscheck kinds firing on healthy libraries.
  *Mitigation:* every positive case ships a clean negative counterpart; corpus
  baseline stays 0/0; nothing gates until the FP-rate gate trusts the check.
- **castxml flakiness** on `case147`/`case149` live-replay. *Mitigation:* prefer
  the Flow-2 `abicheck_inputs/` pack (no live frontend) for everything except the
  depth-ladder case that must demonstrate a real compiler pass; mark live cases
  `integration` and wire `ABICHECK_MIN_EXECUTED`.
- **Schema drift.** v4 fields out of sync across the three test readers.
  *Mitigation:* additive-only fields, defaulted; one shared loader; CI runs
  `test_example_autodiscovery` + `test_evidence_tiers` + `test_abi_examples`.

---

## 9. Use-case tracking

Add `planned` entries to `docs/development/usecase-registry.yaml` under a new gap
**G20**, one per phase (G20.1 / G20.2 / G20.3), each cross-referencing the G19
engine entry and the ADR-035 decision it demonstrates (D2/D4/D7/D8).

## 10. Relationship to existing work

- **G19 / ADR-035** — consumes the engine G19 shipped; no engine or policy
  change. `buildsource/crosscheck.py`, `poi.py`, `risk.py`, `service.run_scan`
  are used as-is.
- **`tests/test_crosscheck.py`** — the `_snap(**kw)` synthetic-snapshot + `_coverage`/`_findings_of`
  helpers Phase 2/3 reuse directly.
- **`tests/test_pattern_audit_scenarios.py`** — the model for the test-only
  scenario suites.
- **G11 single-binary audit** — Phase 1 audit cases extend its surface tooling
  (`surface-report`, `scan --audit`).
- **`scripts/evidence_tiers.py`** — already maps all eight kinds; Phase 0 only
  adds `L5` to the accepted `min_evidence` set.
- **`scripts/check_ai_readiness.py`** — `examples-ground-truth`,
  `examples-readme-sync`, `doc-count-sync`, `changekind-detector`,
  `changekind-docs` gate every new catalog case.
