# Build/Source Redesign — Checks, Tests & Examples Plan (internal)

> Companion to `buildsource-redesign-plan.md`. That file tracked the **feature**
> work (phases 1–6, all ✅ Done). This file tracks the **coverage** work the
> redesign still needs: new detection checks for the risks the source-tree-centric
> model introduces, the test-gap backlog, and the (currently empty) L3/L4/L5
> example catalog.

**Date:** 2026-06-12
**Status:** Proposed — planning only, no code yet.

---

## Why this exists

The redesign (`buildsource-redesign-plan.md`) shipped the *capabilities* —
`dump --sources`, decoupled `--build-info`, `.abicheck.yml` build query,
`merge`, embedded single-artifact storage. But the surrounding coverage did not
keep pace:

1. **Checks** — the detection engine gained **zero** new `ChangeKind`s for the
   new model. The headline scenario (prebuilt binary + source checkout at a tag)
   has **nothing that verifies the source corresponds to the binary**, and
   `merge` silently first-wins on layer conflicts.
2. **Tests** — the six buildsource test files cover happy paths well but miss
   error / conflict / interaction paths.
3. **Examples** — **0** examples exercise L3/L4/L5 or the new workflow, and the
   CMake example harness (`abicheck_add_case`) cannot emit a compile DB or drive
   `--sources`/`--build-info`.

The authority rule still governs everything: every new finding below lands in
`API_BREAK_KINDS` or `RISK_KINDS`, **never** `BREAKING` (ADR-028 D3).

---

## Workstream A — New checks (detections)

Each follows the root `CLAUDE.md` four-step procedure: add to `ChangeKind`,
place in exactly one partition set, implement detection, add unit test, mention
in `docs/`.

### A1. `source_binary_provenance_mismatch` (RISK)

**Scenario (D1/D2):** `--sources` tree does not correspond to the shipped
binary (wrong tag/commit). Today all L4/L5 findings are trusted blindly; a
mismatched checkout produces a flood of bogus source findings.

**Detection (hard-provenance with heuristic fallback — decided 2026-06-12):**

- **Primary (hard):** cross-check a binary provenance signal — GNU build-id,
  DWARF `DW_AT_producer`, or an embedded version string — against the source
  tree's git metadata (HEAD commit / nearest tag). Fires on a definite mismatch.
- **Fallback (heuristic):** when no hard provenance signal is available, fire
  when the source-decl → exported-symbol mapping-miss ratio exceeds a threshold
  (reuses the data already computed for `source_decl_binary_symbol_mismatch`,
  aggregated to a per-library signal).

**Where:** aggregate in `buildsource/source_link.py` during `link_source_abi()`;
surface the finding from `buildsource/source_diff.py`. Partition: `RISK_KINDS`.
Evidence tier: L4.

### A2. `merge_layer_conflict` (RISK)

**Scenario (D5):** two `merge` inputs both supply the same layer (L3/L4/L5) with
**differing `content_hash`** — a parallel-baseline prep mistake (e.g. two
different source trees). Today `_combine_packs` first-wins silently.

**Where:** `cli_buildsource.py::_combine_packs` — when >1 non-`None` contributor
exists for a managed layer and their content hashes differ, emit a diagnostic +
the finding. Partition: `RISK_KINDS`. Evidence tier: L3.

### A3. `build_query_unavailable` (coverage status / capability report)

**Scenario (D4):** `--allow-build-query` ran but `build.query` failed, or config
requested a query that the action ceiling did not permit. Today only a buried
`ExtractorRecord`.

**Where:** `inline.py::_run_build_query` → propagate to the L3 `LayerCoverage`
row and the capability report (`partial: build query failed` instead of silent
`not_collected`). Optional risk finding under `--collection-mode strict`. May
not need a new `ChangeKind` — primarily a coverage/reporting fix.

### A4. `build_info_source_tree_mismatch` (RISK, optional)

**Scenario (D1/D3):** decoupled `--build-info` compile DB references TUs absent
from the `--sources` tree (facts assembled from different trees).

**Where:** `inline.py::collect_inline_pack` — compare compile-DB file entries vs
files present under the source tree; high non-overlap → risk. Partition:
`RISK_KINDS`. Evidence tier: L4.

**Priority:** A1 + A2 are the high-value closures (the two genuinely-new silent
failure modes). A3 is a cheap reporting win. A4 is nice-to-have.

### Gating ripple per new kind (CI-enforced)

- `changekind-partition` (ERROR): in exactly one partition set in `checker_policy.py`.
- `changekind-detector` (WARN): emitted somewhere.
- `changekind-docs` (WARN): mentioned in `docs/`.
- `doc-count-sync` (ERROR): bump `len(ChangeKind)` headline counts (currently **238**).
- `scripts/evidence_tiers.py`: map the new kind to its tier (L3 for A2/A3, L4 for A1/A4).
- `docs/concepts/build-source-data.md`: add to the L3/L4/L5 findings tables.

---

## Workstream B — New tests

Fill the gaps from the coverage map. Pure-Python tests in the fast lane; cases
needing a real compiler get `@pytest.mark.integration`. Hold the 95% Linux
coverage floor.

| Capability | Gaps to cover |
|---|---|
| **D2** `dump --sources` | `--sources` + `--build-info` together; embedded **L5** graph round-trips (only L4 tested today); missing/invalid tree path error |
| **D3** `--build-info` | non-existent path; file-vs-dir; nested auto-discovery; build dir whose `compile_commands.json` is invalid JSON |
| **D4** config/query | explicit `--build-config <path>` flag (only discovery tested); `sources:` `public_headers`/`exclude` filters apply; malformed `.abicheck.yml` (missing `build:`); `--build-config` + `--sources` interaction |
| **D5** `merge` | 3+ inputs; **both inputs supply same layer** (drives A2); mismatched library names; corrupted-JSON input; L4 *and* L5 preserved separately through merge |
| **D6** `collect` | no-input noop; `--source-abi` + `--source-graph` together; output-dir collision |
| **D7** embedding | embedded-L5 round-trip; mixed embedded-vs-pack-ref compare; payload-size sanity |
| **A1–A4** | provenance match (pass) + mismatch (fires); merge-conflict fires on divergent hashes, silent on identical; build-query-failed coverage row; build-info/source-tree mismatch |

New file `tests/test_buildsource_provenance.py` for A1/A2/A4; extend
`tests/test_build_source_cli.py` and `tests/test_build_source_pack.py` for the
rest.

---

## Workstream C — New examples

Largest piece: the harness itself needs extending first.

### C0. Harness extension (prerequisite)

`abicheck_add_case` (CMake) only builds v1/v2 `.so`+headers — it cannot emit a
compile DB or drive `--sources`/`--build-info`. Add either:

- build-flag + `CMAKE_EXPORT_COMPILE_COMMANDS=ON` knobs on `abicheck_add_case`, or
- a sibling `abicheck_add_buildsource_case` macro that builds each version with a
  compile DB and runs `compare` with `--build-info`/`--sources`.

Teach `tests/test_abi_examples.py` to pass the L3/L4 inputs for these cases and
skip cleanly when clang/castxml is absent.

### C1. New example cases

Each: `v1/v2` source+headers, `README.md`, `ground_truth.json` entry,
regenerated `examples/README.md` + `docs/examples/*.md`.

| Case | Layer / `min_evidence` | Encodes | Expected kind(s) |
|---|---|---|---|
| L3 build-flag drift | **L3** (first ever) | identical source/headers, `_GLIBCXX_USE_CXX11_ABI` (or `-fvisibility`) flipped | `abi_relevant_build_flag_changed` |
| L4 macro value | **L4** | public-header macro constant changed | `public_macro_value_changed` |
| L4 default argument | **L4** | default arg changed, signature identical | `default_argument_changed` |
| L4 constexpr value | **L4** | public `constexpr` value changed | `constexpr_value_changed` |
| L4 uninstantiated template | **L4** | gives existing **case122** residual its first real detection | `uninstantiated_template_removed` / `template_body_changed` |
| Provenance mismatch | **L4**, risk | source tree from the *wrong* tag vs binary | `source_binary_provenance_mismatch` (A1) |
| Merge baseline | workflow | two independently-produced dumps merged (no-conflict path) | workflow smoke + A2 negative |

### C2. Gate sync (all ERROR-level)

- `examples-ground-truth`: README + `ground_truth.json` entry per case.
- `examples-readme-sync`: regenerate via `scripts/gen_examples_docs.py` so
  headline count, verdict distribution, and case-index rows match.
- `doc-count-sync`: bump case-count anchors.
- `scripts/evidence_tiers.py`: give the new L3/L4 kinds a defined tier (today
  **no kind maps to L3/L4** — these examples make that real).

---

## Suggested sequencing

1. **A2 + merge tests** — self-contained, no compiler. Closes silent merge-conflict hole.
2. **A1 provenance check + tests** — headline gap; provenance signal + mapping-miss fallback.
3. **Test gaps D2–D7** — pure-Python, raises confidence before touching examples.
4. **A3/A4 + coverage reporting.**
5. **C0 harness extension**, then **C1 examples** + **C2 gate sync** + docs / `evidence_tiers.py`.

Steps 1–4 are pure-Python (fast lane). Step 5 needs clang/castxml (behind
`integration`).

---

## Relationship to other docs

- Extends `buildsource-redesign-plan.md` (the feature plan it complements).
- Implements coverage for ADR-028 D3 (authority rule), ADR-029 D9 (L3 findings),
  ADR-030 D6 (L4 findings), ADR-031 D6 (L5 findings), ADR-032 D5 (action ceiling).
