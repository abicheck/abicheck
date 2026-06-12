# Build/Source Redesign — Checks, Tests & Examples Plan (internal)

> Companion to `buildsource-redesign-plan.md`. That file tracked the **feature**
> work (phases 1–6, all ✅ Done). This file tracks the **coverage** work the
> redesign still needs: new detection checks for the risks the source-tree-centric
> model introduces, the test-gap backlog, and the (currently empty) L3/L4/L5
> example catalog.

**Date:** 2026-06-12
**Status:** In progress.

**Implementation status (PR #363):**
- ✅ **A1** `source_binary_provenance_mismatch` — new RISK ChangeKind (245) +
  L0-export plumbing into `dump <binary> --sources`.
- ✅ **A2** `merge_layer_conflict` — `merge --on-conflict=warn|error`,
  order-independent per-layer digest, accurate winner, persisted to the ledger.
- ✅ **A4** `build_info_source_tree_mismatch` — collection-time diagnostic (not a
  ChangeKind; basename-match, redaction/OS-separator safe).
- ✅ **A3** `build_query_unavailable` — failed/blocked build query now surfaces as
  a `partial` L3 coverage row with the reason (no new ChangeKind).
- ✅ **Workstream B** — D2–D7 pure-Python gaps landed (merge 3+/conflict/order/
  winner/corrupted-input, embedded-L5 round-trip, invalid compile-DB graceful,
  malformed `.abicheck.yml`, `--sources`+`--build-info`, `collect` no-input).
- ✅ **A1 `merge`-path L0 plumbing** — `relink_surface_exports` re-derives the
  decl→symbol mapping from the binary base's exports at merge time (identical
  rule to `link_source_abi`, no new behaviour/FP risk).
- ✅ **C0 example-harness `--sources` opt-in** — `validate_examples._sources_path`
  + `--sources` wiring (opt-in via ground-truth `sources: true` + per-side
  `v1.sources/`/`v2.sources/`), clang-skipped; unit-tested. Enables L4/L5 cases.
- ⏳ **C1 L4/L5 example cases — not added.** Authoring a *verified* case needs a
  working source-replay front-end: `castxml` is absent here and a local clang
  probe (`dump --sources` on a public-header macro change) surfaced **0 macros /
  0 decls** even with `sources.public_headers` configured, so the public-header
  reachability the macro/decl findings need isn't exercised in this environment.
  Adding a case blind would red the integration lane + churn the ERROR-level
  example gates. C1 should be authored where clang-L4 (or castxml) replay
  actually surfaces the public surface, reusing the C0 harness opt-in.

---

## Why this exists

The redesign (`buildsource-redesign-plan.md`) shipped the *capabilities* —
`dump --sources`, decoupled `--build-info`, `.abicheck.yml` build query,
`merge`, embedded single-artifact storage. But the surrounding coverage did not
keep pace:

1. **Checks** — the engine already has a full set of L3/L4/L5 `ChangeKind`s from
   ADR-029/030/031 (e.g. `abi_relevant_build_flag_changed`,
   `public_macro_value_changed`, `public_reachability_changed`), emitted from
   `build_diff.py` / `source_diff.py` / `source_graph.py`. What it gained **zero**
   of is detections for the *new failure modes the source-tree-centric redesign
   itself introduces*: the headline scenario (prebuilt binary + source checkout
   at a tag) has **nothing that verifies the source corresponds to the binary**,
   and `merge` silently first-wins on layer conflicts. Workstream A adds only
   those genuinely-missing checks (A1–A4) — it does **not** re-add the existing
   L3/L4/L5 kinds.
2. **Tests** — the six buildsource test files cover happy paths well but miss
   error / conflict / interaction paths.
3. **Examples** — **L3 is now covered** (PR #362, on `main`): `case130`–`case133`
   are build-mode flips with `min_evidence: L3` and a new `build_info: true`
   ground-truth field, plus `case129_struct_return_convention`. Their pattern is
   **checked-in per-side `v1/v2.compile_commands.json` + `dump --build-info` +
   compare** (no CMake-macro compile-DB generation). What is **still empty is
   L4/L5**: **0** examples *detect at* L4/L5 or exercise the `--sources` source-tree
   replay / `merge` workflow. (`case122` is `min_evidence: L4` but is the
   uninstantiated-template residual with no detection mechanism — `NO_CHANGE`
   today; see C1.) No example harness yet drives `--sources` (the L4/L5 replay).

The authority rule still governs everything: every new finding below lands in
`API_BREAK_KINDS` or `RISK_KINDS`, **never** `BREAKING` (ADR-028 D3).

---

## Workstream A — New checks (detections)

**Only the genuine new `ChangeKind`s — A1 and A4 (and an A3 compare finding if
one is added) — follow the root `CLAUDE.md` four-step procedure:** add to
`ChangeKind`, place in exactly one partition set, implement detection, add unit
test, mention in `docs/`. **A2 is a merge-time diagnostic and A3 is a
coverage-status fix — they are not enum members**: they get detection + a
unit/doc test for their own channel (manifest `ExtractorRecord`/`BuildEvidence.diagnostics`,
stderr, `merge --on-conflict`, L3 coverage row) and **no** ChangeKind/partition/
tier/doc-count work. See each item and the gating section below.

### A1. `source_binary_provenance_mismatch` (RISK)

**Scenario (D1/D2):** `--sources` tree does not correspond to the shipped
binary (wrong tag/commit). Today all L4/L5 findings are trusted blindly; a
mismatched checkout produces a flood of bogus source findings.

**Detection (hard-provenance with heuristic fallback — decided 2026-06-12):**

- **Primary (hard):** only fire on a signal that **explicitly encodes the
  repository revision/tag** — an embedded `git describe` / version string, a
  `.note` VCS revision, or a recorded build-id↔commit manifest from the build —
  cross-checked against the source tree's git metadata (HEAD commit / nearest
  tag). Note GNU build-id is a hash of the *linked binary* and `DW_AT_producer`
  identifies the *compiler/flags*, **not** the source commit; neither maps to a
  checkout on its own, so they are at most auxiliary corroboration (e.g. a
  build-id only as the key into a recorded build-id↔commit map), never the
  revision source. If no VCS-encoding signal exists, the hard path simply does
  not fire — fall through to the heuristic rather than comparing unrelated values.
- **Fallback (heuristic):** when no explicit VCS provenance is available, fire
  when the source-decl → exported-symbol mapping-miss ratio exceeds a threshold
  (reuses the data computed for `source_decl_binary_symbol_mismatch`, aggregated
  to a per-library signal).

  **Prerequisite — plumb L0 exports into the inline/merge flow first.** Today
  `embed_build_source → collect_inline_pack` receives only source/build inputs;
  the binary's exported symbols are **never** passed in, so `run_source_replay`
  links against an empty `exported_symbols` set and the source-decl→symbol
  mappings come out empty — the mapping-miss heuristic would be **inert** in
  exactly the wrong-tag scenario it targets. So step 2 of A1 must first add L0
  export plumbing: pass the binary's exported-symbol set into
  `collect_inline_pack` for the `binary + --sources` case, and into `merge` for
  the parallel-baseline case (the binary-bearing input supplies L0). Without that
  the heuristic has no signal; the hard path still works when a VCS signal is
  embedded.

**Where:** aggregate in `buildsource/source_link.py` during `link_source_abi()`
(once L0 exports are available there); surface the finding from
`buildsource/source_diff.py`. Partition: `RISK_KINDS`. Evidence tier: L4.

### A2. `merge_layer_conflict` (merge-time warning/error, not a compare ChangeKind)

**Scenario (D5):** two `merge` inputs both supply the same layer (L3/L4/L5) with
**differing facts** — a parallel-baseline prep mistake (e.g. two different
source trees). Today `_combine_packs` first-wins silently.

**Detection:** in `cli_buildsource.py::_combine_packs`, when >1 non-`None`
contributor exists for a managed layer, compare a **per-layer payload digest**
(of just that layer's normalized facts), **not** the pack-wide
`BuildSourcePack.content_hash()`. The pack hash folds in every layer plus
coverage/extractor metadata, so two inputs with identical L4/L5 facts but a
differing unrelated layer or extractor row would false-positive. Digest only the
shared layer's payload; a conflict is when those digests differ.

**Reporting channel (important — merge has no findings list).** `_combine_packs`
returns only a `BuildSourcePack` and `merge_cmd` serializes that pack + stderr
status; there is **no `DiffResult`/`Change` list at merge time**, so this is
**not** a compare-pipeline `ChangeKind` and must not be modelled as one. Use the
channels that already exist:

- **Persist** the conflict into a field that is **actually serialized**.
  `BuildSourceManifest.to_dict()` (`model.py` ~227–257) has **no `diagnostics`
  key** — it serializes only version/source_root/inputs/**extractors**/artifacts/
  coverage/redaction, so a manifest-level diagnostic would be dropped on
  round-trip. Use the same persisted locations `_run_build_query` failures use
  (`inline.py` ~352–365): record an **`ExtractorRecord`** row (with `status` +
  `detail`) in `manifest.extractors` (which *is* in `to_dict()`), and/or append
  to **`BuildEvidence.diagnostics`** for the L3 case. Both ride inside the
  embedded `build_source` payload — no new schema/serialization path. (If a
  dedicated manifest `diagnostics` field is ever wanted, that's an explicit
  model/schema addition, not an existing channel.)
- **Warn** on stderr from `merge_cmd`.
- **Fail non-zero** under a merge failure policy. Note `merge_cmd` currently
  declares only `inputs` / `--output` / `--verbose` — `--collection-mode` lives
  on `collect`, **not** `merge` — so this requires **adding a new merge knob**,
  e.g. `merge --on-conflict=warn|error` (default `warn` = first-wins + recorded
  diagnostic; `error` = non-zero exit). Define that flag as part of A2; do not
  assume an existing one.
- *(Optional, only if a compare-time surfacing is wanted):* a later
  `compare` may read the recorded conflict marker from the baseline and emit a
  `RISK` finding then — that is the only place a `ChangeKind` could legitimately
  appear. Decide this when implementing; the merge-time warning/error is the
  primary, sufficient mechanism.

### A3. `build_query_unavailable` (coverage status / capability report)

**Scenario (D4):** `--allow-build-query` ran but `build.query` failed, or config
requested a query that the action ceiling did not permit. Today only a buried
`ExtractorRecord`.

**Where:** `inline.py::_run_build_query` → propagate to the L3 `LayerCoverage`
row and the capability report (`partial: build query failed` instead of silent
`not_collected`). Optional risk finding under `--collection-mode strict`. May
not need a new `ChangeKind` — primarily a coverage/reporting fix.

### A4. `build_info_source_tree_mismatch` (collection-time diagnostic — ✅ done)

**Scenario (D1/D3):** decoupled `--build-info` compile DB references TUs absent
from the `--sources` tree (facts assembled from different trees).

**Where (as implemented):** `inline.py::_check_build_info_source_mismatch`,
called from `collect_inline_pack`. Resolve each compile unit's `source` relative
to its compile-DB `directory` and test existence under the `--sources` tree;
when ≥90% of ≥3 units are absent, record an `ExtractorRecord`
(`build_info_source_tree_mismatch`) + a `BuildEvidence.diagnostics` entry.

**Not a `ChangeKind`** (revised from the original plan): like A2, this is a
*collection-time* single-side property with no `DiffResult` list at collection,
so modelling it as a compare ChangeKind would re-introduce the A2 anti-pattern.
It rides the existing ledger/coverage channels. Conservative thresholds keep it
off the FP-rate gate.

**Priority:** A1 + A2 are the high-value closures (the two genuinely-new silent
failure modes). A3 is a cheap reporting win. A4 is nice-to-have.

### Gating ripple — **only for the items that are actually new `ChangeKind`s**

In the end **only A1** became a new enum member. A2 and A4 are collection/merge
diagnostics and A3 is a coverage-status fix — none gets a partition /
evidence-tier / doc-count entry (modelling them as ChangeKinds would reintroduce
the very error this plan avoids). For A1 (done) the ripple was:

- `changekind-partition` (ERROR): in exactly one partition set in `checker_policy.py`.
- `changekind-detector` (WARN): emitted somewhere.
- `changekind-docs` (WARN): mentioned in `docs/`.
- `doc-count-sync` (ERROR): bump `len(ChangeKind)` headline counts (**245** after A1).
- `scripts/evidence_tiers.py`: map the kind to its tier (L4 for A1/A4).
- `docs/concepts/build-source-data.md`: add to the L3/L4/L5 findings tables.

A2's merge diagnostic and A3's coverage row instead need: a doc/test for the
new channel (manifest diagnostic, stderr, `merge --on-conflict`, L3 coverage
row) — **no** enum/partition/tier/doc-count work.

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

Largest piece: the **L4/L5** example path needs building. The **L3 build-info
path already exists** (PR #362) — reuse it.

### C0. Harness — L3 done, L4/L5 still needed

**L3 (done, reuse the pattern):** PR #362 established the build-info example
pattern without extending the CMake macro — each case (`case130`–`case133`)
ships checked-in per-side `v1/v2.compile_commands.json`, sets `build_info: true`
in `ground_truth.json`, and is validated via `dump --build-info` + `compare`
(`tests/test_abi_examples.py` already drives this). New L3 examples just follow
that template.

**L4/L5 (still missing):** no example yet drives `--sources` (source-tree L4
replay + L5 graph). This is the actual C0 work:

- add a `--sources <tree>` example path — either checked-in source-replay inputs
  per side (mirroring the `compile_commands.json` pattern), or a harness step
  that runs `dump --sources` — with a `sources: true` ground-truth field; and
- teach `tests/test_abi_examples.py` to drive it and **skip cleanly when
  clang/castxml is absent** (L4 needs a C++ front-end).

### C1. New example cases

Each: `v1/v2` source+headers, `README.md`, `ground_truth.json` entry,
regenerated `examples/README.md` + `docs/examples/*.md`. **L3 build-mode flips
already exist** (`case130`–`case133` for exceptions/rtti/threadsafe/tls,
`case129` struct-return) — do not re-add those; the remaining gaps are below.

| Case | Layer / `min_evidence` | Encodes | Expected kind(s) |
|---|---|---|---|
| L3 generic ABI-flag drift | **L3** | complements the existing mode flips: `_GLIBCXX_USE_CXX11_ABI` (or `-fvisibility`) flipped via per-side `compile_commands.json` | `abi_relevant_build_flag_changed` |
| L4 macro value | **L4** | public-header macro constant changed | `public_macro_value_changed` |
| L4 default argument | **L4** | default arg changed, signature identical | `default_argument_changed` |
| L4 constexpr value | **L4** | public `constexpr` value changed | `constexpr_value_changed` |
| L4 uninstantiated template | **L4** | gives existing **case122** residual its first real detection | `uninstantiated_template_removed` / `template_body_changed` |
| **L5 source-graph reachability** | **L5** | a public decl enters/leaves the target→header→decl→symbol closure (and/or a decl remaps to a different exported symbol) — built via `--sources` so the L5 graph is folded | `public_reachability_changed`, `source_to_binary_mapping_changed` |
| Provenance mismatch | **L4**, risk | source tree from the *wrong* tag vs binary | `source_binary_provenance_mismatch` (A1) |
| Merge baseline | workflow | two independently-produced dumps merged (no-conflict path) | workflow smoke + A2 negative |

### C2. Gate sync (all ERROR-level)

- `examples-ground-truth`: README + `ground_truth.json` entry per case (use the
  `build_info: true` / `sources: true` fields PR #362 introduced for L3, and the
  `sources` equivalent for L4/L5).
- `examples-readme-sync`: regenerate via `scripts/gen_examples_docs.py` so
  headline count, verdict distribution, and case-index rows match.
- `doc-count-sync`: bump case-count anchors.
- `scripts/evidence_tiers.py`: give the new L4 kinds a defined tier. (L3 is now
  populated — `case130`–`case133` map at L3; L4 is the open tier. Re-read the
  current kind→tier map before editing, since the cumulative per-case tier
  distribution is a separate metric from a kind's first-detection tier.)

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
- **`ChangeKind` count is a snapshot (244 on `main` after rebasing past PR #362,
  which added the runtime-mode L3 kinds + `struct_return_convention_changed`).**
  It still moves with other in-flight work, so re-read `len(ChangeKind)` and the
  `evidence_tiers.py` map before implementing rather than trusting frozen numbers.
- **PR #362 already landed the L3 build-mode examples** (`case129`–`case133`) and
  the checked-in-`compile_commands.json` + `dump --build-info` example pattern,
  so this plan's example work is now L4/L5-only (see C0/C1).
