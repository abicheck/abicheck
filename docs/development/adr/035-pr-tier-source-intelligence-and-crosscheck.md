# ADR-035: PR-Tier Source Intelligence and Cross-Source Validation

**Date:** 2026-06-14
**Status:** Proposed
**Decision maker:** (pending review)

---

## Context

A field proposal ("Source scans in abicheck: depth levels, tooling,
cross-checks, and build integration") argues that source-aware analysis should
not be an all-or-nothing "dump every translation unit's AST" mode run only at
release/nightly. Instead it proposes a configurable ladder of source scans
(`S0..S6`) with per-level cost, a risk-scored escalation policy, cross-source
validation, and build-integrated fact extraction — so that compatibility risk
is caught **on the PR**, cheaply, and deeper analysis runs only when triggered.

Most of that architecture already exists in abicheck. ADR-028…033 define an
optional build/source evidence stack:

- **L0/L1/L2** — artifact-authoritative binary, debug-info, and header-AST scan
  (`dumper.py`, `dumper_castxml.py`, `elf/pe/macho_metadata.py`, `dwarf_*.py`).
- **L3** — build/toolchain context from compile DB, CMake, Ninja, Bazel, Make
  (`buildsource/adapters/*`, `build_evidence.py`, `build_diff.py`).
- **L4** — scoped per-TU source-ABI replay via clang/castxml/android backends
  with per-TU caching (`source_replay.py`, `source_extractors/*`).
- **L5** — source/implementation graph (include/call/type/build edges, Kythe /
  CodeQL ingest) (`source_graph.py`, `call_graph.py`, `include_graph.py`).
- A CI complexity ladder, replay scopes (`off`/`headers-only`/`changed`/
  `target`/`full`), per-TU + content-addressed caches, evidence policy, and
  coverage/metrics reporting (ADR-033 D1–D10).

Mapped onto that stack, the proposal's `S0..S6` scale is **not a new axis** —
it largely re-derives the existing L-layers:

| Proposal level | abicheck today | Gap |
|---|---|---|
| `S0` diff classifier + risk score | `recommend-collect-mode` (path → mode) | no numeric risk score / escalation thresholds / `risk_rules` config |
| `S1` compile-DB / build-flag scan | **L3** (5 adapters, flag-drift diff) | covered |
| `S2` preprocessor + include graph | **L5** include edges (`clang -MM`) | no per-TU macro-value capture, no private-header-leak / generated-config detection |
| `S3` lexical/pattern scan (no compiler) | — | **missing entirely** (no compiler-free tier) |
| `S4` symbol/reference index | **L5** call edges + Kythe/CodeQL ingest | no clangd-index integration (acceptable) |
| `S5` targeted semantic AST | **L4** scoped replay | covered |
| `S6` full AST | **L4** `--source-abi-scope full` | covered |
| cross-checks (§6) | partial (`dumper` logs some leaks) | **no cross-check findings** as `ChangeKind`s |
| build-plugin / wrapper extraction (§8) | ADR-032 extractor plugin + ADR-033 D1 "Phase 6" | **not implemented**; no dump/facts artifact protocol |
| normalized facts, caches, coverage report | covered (ADR-030/033) | extend naming for new tiers |

So three genuine gaps remain, and they are exactly where the proposal's value
concentrates:

1. **No cheap, always-on PR tier.** Every existing evidence layer (L3–L5) needs
   a compile DB or a compiler. There is nothing that runs in `<5%` of build
   cost on *every* PR with no toolchain (the proposal's `S0`/`S2`-macro/`S3`).
2. **No cross-source validation or evidence-directed focusing.** abicheck holds
   binary exports, header AST, build flags, and source facts in one snapshot but
   (a) never diffs them *against each other within a single version*
   (exported-but-not-public, header built with different macros than the shipped
   TU, private-header leaks, ODR type variants), and (b) never feeds the cheap
   evidence *forward* to **target** the expensive source scan. The
   information-sharing is one-directional today; binary/header deltas should
   point the source scan at specific entities/TUs, not just be checked against
   them. The cross-source checks are also valuable on a **single release** (no
   baseline compare) — a whole class of "bad ABI hygiene" lint that justifies a
   broader scan on its own.
3. **No build-integrated extraction or single `scan` UX.** Capability is spread
   across `dump`/`compare`/`collect`/`compare-graph`; there is no risk → budget
   → escalation orchestrator, no per-project cost estimate to pick a depth, no
   stable programmatic API for the level ladder, and no way to ingest facts
   emitted *by the
   product build* instead of re-running a frontend.

---

## Decision

### D1. Two orthogonal axes: L = evidence layer, S = source-analysis method

There are **two different things** the proposal and abicheck talk about, and they
must not be conflated:

- **L0–L5 — evidence layers (the *what* + authority).** Where a fact comes from
  (binary / debug / header / build / source-ABI / graph) and how much it is
  trusted. L0–L2 stay authoritative for `BREAKING`; L3–L5 only `RISK`/`API_BREAK`
  (ADR-028 D3). This is the user-facing depth axis and the authority axis.
- **S0–S6 — source-analysis methods (the *how*).** Six distinct, cost-ordered
  *techniques* for analyzing source, from a diff classifier up to a full AST.
  These are **not** evidence layers and **not** a competing authority/selector
  axis — they are the **pipeline of methods that produce the L3–L5 evidence**,
  and the granularity at which coverage is reported.

So the S-scale is a real, named concept (source analysis is genuinely six
graduated methods, not one "parse the AST" step), *orthogonal* to L: an S-method
runs and its output lands in an L-layer. **Both axes are user-selectable** —
`--source-method sN` pins the source-analysis method, `--depth` sets the L-layer
ceiling; users pick whichever axis they think in. S being selectable does not
fork the *evidence/authority* model: authority stays on the L-axis (an L3–L5 fact
never alone gates `BREAKING`, whatever S produced it). Mapping:

| S-method | What it does | Tool | Produces (L) | Cost |
|---|---|---|---|---|
| **S0** diff classifier | changed-file → risk tags/score | git diff | — (drives D3/D7) | `<1%` |
| **S1** compile-DB / flags | ABI-affecting build context | parse `compile_commands.json` etc. | **L3** | `<1%` |
| **S2** preprocessor / includes | macro values, include/leak graph | `clang -E`/`-MM` | **L3→L5** | 1–10% |
| **S3** lexical pattern scan | ABI-risk constructs, no semantics | regex / Tree-sitter | pre-scan facts (→L2/L5) | `<1–5%` |
| **S4** symbol/reference index | decls/refs/inheritance/call graph | clangd-index / Kythe / CodeQL | **L5** | 5–30% |
| **S5** targeted semantic AST | layouts, instantiations, body hashes | clang/castxml on selected TUs | **L4** (+L5 edges) | 5–40% |
| **S6** full AST | whole-project semantic facts | clang/castxml all TUs | **L4** full-scope | 20–80% |

The **authority rule (ADR-028 D3) is preserved**: whatever S-method produced a
fact, an L3–L5 fact never alone decides a shipped `BREAKING` verdict — it
explains, localizes, and adds confidence.

### D2. Add an always-on, compiler-free PR pre-scan tier (extends L2/L5)

A new cheap tier runs on every PR over **changed + public** files, needing no
compile DB and no compiler:

- **Pattern pre-scan** (new `buildsource/pattern_scan.py`): regex/lexical scan
  for ABI-risk constructs — `#pragma pack`, `alignas`, `__attribute__((packed|
  visibility))`, `__declspec(dllexport|dllimport)`, `extern "C"`, calling-
  convention macros, explicit / `extern` template instantiation, `inline
  namespace`, public virtual methods, `operator new`/`delete`. Emits advisory
  facts and escalation triggers, never a final verdict. Starts as a stdlib
  regex scanner (no new dependency); Tree-sitter is an optional later backend.
- **Preprocessor pre-scan** (extends `buildsource/include_graph.py`): when a
  compile DB is present, capture per-TU macro values for ABI-affecting macros
  and detect *public-header-includes-private/generated-header* leaks and
  per-TU macro-value divergence for the same public type.

These feed D3 (escalation) and D4 (cross-checks). Output is normalized facts in
the existing `buildsource` schema, with coverage reported (ADR-033 D6/D9).

### D3. Explicit deterministic level; risk-scored `auto` and budget are opt-in

The level is **chosen explicitly and is deterministic** so a CI gate produces the
same scan for the same inputs:

- The user picks the level on either axis (`--source-method sN` or `--depth`,
  D1). Each `--mode` (`pr`/`pr-deep`/`baseline`) is a **fixed preset** of (L,S),
  not risk-varying.
- A **numeric risk score** (from a `risk_rules` config block: public header `+50`,
  export map `+50`, ABI-affecting flag `+40`, exported-symbol definition `+30`,
  reachable-from-public `+25`, template-instantiation change `+35`,
  docs/tests-only `-100`) drives **two opt-in** things only: the `auto` level
  (`--source-method auto`, local/dev convenience), and POI focusing within the
  chosen level (D7, deterministic for a fixed diff). Risk **never** silently
  changes the level of a CI run that pinned one.
- A **time budget** is optional and, on overflow, **fails** (nonzero exit) — it
  never silently shrinks scope. For gating CI, pin a level; do not use a budget.
- Add a **`scan`** subcommand (`abicheck/cli_scan.py`, registered per the
  CLAUDE.md sibling-module pattern) that orchestrates: classify → always-on tier
  (D2) → run the chosen L/S level (POI-focused) → emit one coverage- and
  confidence-annotated report stating exactly which S-method/L-layer ran, never a
  bare "source scan failed". `scan` is a front-end over existing
  `dump`/`compare`/`collect`; it adds no new authority.

### D4. Cross-source validation engine (new findings, RISK/API_BREAK tier)

New `buildsource/crosscheck.py` consumes one merged snapshot and diffs its
evidence sources against each other *within a single version*. Each check is a
named `ChangeKind` (added per the four-step procedure in `/CLAUDE.md`,
partitioned into `RISK_KINDS` or `API_BREAK_KINDS` — never `BREAKING_KINDS`,
per D1). Initial set, cheapest first:

| Check | Inputs | Tier |
|---|---|---|
| `exported_not_public` | binary exports ↔ L2 header decls | RISK |
| `public_not_exported` | L2 header decls ↔ binary exports | RISK |
| `header_build_context_mismatch` | L2 header macros/flags ↔ L3 build flags | API_BREAK |
| `private_header_leak` | L5 include graph ↔ install manifest | RISK |
| `odr_type_variant` | L4 per-TU layouts of one type | API_BREAK |
| `public_to_internal_dependency` / behavioral risk | L5 reachability ↔ PR changed files | RISK |

The §6.8 provider-agreement matrix maps directly onto the existing
`LayerConfidence`; each finding records which providers (`public_header_ast`,
`source_index`, `binary_exports`, `debug_info`, `build_config`) corroborate the
entity, driving the confidence tag.

### D5. Build-integrated extraction: dump/facts artifact protocol + providers

Implement the proposal's §8 as concrete ADR-032 extractor providers, with the
extended-variant §8.0 distinction made explicit:

- **Flow 1 (abicheck-runs-replay)** — already supported: `dump --sources` /
  `collect` replays selected compile commands. Stays the portable default.
- **Flow 2 (build-emits-facts)** — new: a standardized **dump/facts artifact
  protocol** the product build drops next to its binary, which abicheck then
  ingests without re-running a frontend:

  ```text
  abicheck_inputs/
    manifest.json
    binary/…  headers/…  build/compile_commands.json
    source_facts/*.jsonl   # preferred — normalized facts
    raw_ast/*.json.zst     # optional, debug/forensic only
    pp/*.macros.jsonl  deps/*.d   # optional
  ```

  This rides the existing `merge` flow (artifact-side + source-side dumps merged
  into one baseline, ADR-028 D8 / ADR-033 amendment).

- **Providers** (ADR-032 manifest extractors): a Clang plugin
  (`-fplugin=…`, normalized facts during normal compile, no raw AST by default)
  and a compiler wrapper (`abicheck-cc clang++ …`, companion extraction action).
  GCC `-fdump-lang-class`/`-tinst` and MSVC remain documented fallbacks.

**Canonical rule:** normalized source facts are the comparison format; raw AST
dumps are an MVP-ingest/forensic fallback only. The Clang plugin is positioned
as a *performance optimization* (removes the second frontend pass), never a
requirement — it is compiler-version-sensitive, so `compile_commands.json`
replay + LibTooling/CastXML stays the supported portable path.

### D6. Additive `.abicheck.yml` schema for levels, budgets, and cross-checks

Extend the config loaded by `buildsource/inline.py` with optional `source:
{ budgets, layers }` (`layers` matching the existing collect API key — not a
new `levels` spelling), `risk_rules`, and `crosschecks: { <check>:
info|warning|error }` blocks. All additive and defaulted-off where they imply new cost; they
map onto the existing collect-mode / evidence-policy machinery rather than
replacing it.

### D7. Evidence-directed focusing: cheap facts steer the expensive scan

Cross-source links are used in **two directions**, not one. D4 reads them to
emit findings; D7 reads the *same* links *before* the expensive scan to shrink
its scope to a **points-of-interest (POI) set**. The cheap, already-computed
L0/L1/L2 facts (and the L0↔L2 deltas vs. baseline) decide *what* L4/L5 looks at:

- exported symbol changed but the header did not → resolve its source
  declaration and replay **only** that TU/entity;
- header type whose layout is macro-conditional → capture macro values **only**
  for the TUs that materialize it;
- new/removed export with no public declaration → point the scan at the source
  decl that emits the symbol;
- demangled exported template symbol → seed which instantiations/TUs to replay.

Mechanically this is the **reverse** of the existing `explain-finding`
localization walk (export → decl → header → build option): instead of explaining
a finding after the fact, the POI set is computed up front and handed to
`source_replay` scope selection and the cross-check engine as a work-list. Net
effect: a large project pays L4/L5 cost only on the handful of entities the
binary/header evidence already flagged.

### D8. Single-release audit mode (no baseline required)

The D2 pattern facts and the D4 cross-checks are **intra-version** — they need
exactly one build, not a compare. Expose them as a first-class **audit** that
runs without a baseline and emits a catalog of single-release "bad ABI hygiene"
findings: accidental ABI surface (`exported_not_public`), non-self-contained or
private-header-leaking public headers, `header_build_context_mismatch`, ODR type
variants across TUs, inconsistent/missing symbol visibility, unversioned exported
symbols where a version script exists, and RTTI/typeinfo emitted for internal
types. This is wired into the existing single-binary surface tooling
(`surface-report`, the G11 single-binary audit) and reported as a lint with its
own severity mapping. Because it delivers value from a single artifact, it
justifies running a **deeper** one-time scan than a two-build PR diff would.

### D9. Asymmetric defaults: full at baseline, scoped on PR

The default depth differs by *when* the scan runs:

- **Baseline / release publish** → **full**: full dump + full source analysis
  (`--source-abi-scope full`), all cross-checks, single-release audit, and raw
  facts archived for cache warmup. Computed once, amortized, authoritative, and
  cached in the baseline registry (ADR-022).
- **PR / CI** → **scoped**: always-on tier (D2) every time, plus a **fixed,
  pinned** L/S level (the `pr` preset, or whatever the user pins), POI-focused
  (D7). Deterministic — same inputs, same scan. `auto`/budget stay opt-in (D3).

The asymmetry is the point: the baseline is produced once so it can afford full
depth and gives the PR a rich, cached fact set to diff and focus against; the PR
runs on every push so it must stay cheap. Project size shifts the PR default (see
*Sizing guidance*) — small projects can simply run full on PRs too.

### D10. Programmatic API: where each level plugs in

The level ladder is exposed as a typed Python API so the CLI, the MCP server,
and CI wrappers all drive the same engine. Layering, top-down:

| Layer | Where | Responsibility |
|---|---|---|
| CLI | `cli_scan.py`, existing `cli*.py` | argv → `ScanRequest`; render report/exit code |
| MCP | `mcp_server.py` | expose `scan`/`audit`/`estimate` as agent tools |
| Service | `service.py` (extend) | `run_scan(ScanRequest) -> ScanResult`; orchestrates classify → POI → escalate → collect → cross-check → report |
| Levels | `buildsource/` providers | one provider per level, uniform protocol |
| Facts | `buildsource/model.py`, `source_abi.py`, `build_evidence.py` | normalized fact schema (canonical I/O of every provider) |

- **Provider protocol** — each level (the D2 pattern/preprocessor pre-scan, L3
  build, L4 replay, L5 graph, the D4 cross-checks) implements a small uniform
  interface modelled on the existing ADR-032 `DataExtractor`:
  `capabilities()`, `estimate(ctx) -> CostEstimate`, `run(ctx, poi) -> Facts`,
  consuming a shared `ScanContext` (paths, compile DB, changed files, budget,
  cache) and the POI set, and returning **normalized facts only** (never raw
  AST as primary output). This is what makes levels independently runnable
  (ADR-033 D1) and lets external/build-emitted providers (D5) drop in via the
  ADR-032 manifest.
- **Request/result objects** — `ScanRequest` (binary, headers, compile DB,
  mode, budget, risk rules, level overrides) and `ScanResult` (findings,
  per-level `LayerCoverage`, `CostEstimate` vs. actual, confidence/provider
  matrix). `ScanResult` is what the reporter, PR comment, SARIF, and MCP all
  consume — one object, many renderings.
- **`estimate` is a first-class entry point**, not a side effect: a dry-run that
  probes the project (TU count from compile DB, header fan-out, cache state) and
  returns the projected cost of each level for *this* project so a maintainer
  (or CI) can choose a budget/depth. Implemented as `service.estimate_scan()`,
  surfaced as `abicheck scan --estimate` and an MCP tool.

---

## Sizing guidance (defaults by project scale)

Cost anchors are from §11 of the proposal (full `-fsyntax-only` ≈ 20–80% of a
clean build; pattern/compile-DB scans `<1–5%`). These are starting defaults;
`scan --estimate` (D10) gives the real per-project number.

| Project scale | PR default | Baseline default |
|---|---|---|
| **Small** (≲50 TUs, builds in seconds) | full source analysis every PR is fine | full |
| **Medium** (~50–500 TUs) | always-on tier + risk/POI-targeted L4; budget cap | full (nightly or on release) |
| **Large** (≳500 TUs, or template/header-heavy) | always-on tier + cross-checks + POI-targeted L4 within budget; lean on cache | full on release only; warm cache from it |
| **Header-only / template-heavy** | AST cost is header-dominated — prefer full L4 if small, else POI-targeted | full |

Rule of thumb: if `scan --estimate` puts full L4 under the PR time budget, run it;
otherwise run the always-on tier always and let risk score + POI escalate.

---

## Maintainer UX / adoption path

Designed as a progressive ramp — value at step 1 with zero build integration,
deeper signal as the maintainer opts in:

1. **Zero-config** — `abicheck scan --binary libfoo.so --headers include/` runs
   L0–L2 + the always-on tier and the single-release audit (D8). No compile DB,
   no compiler, immediate hygiene findings.
2. **Add a compile DB** — `--compile-db build/compile_commands.json` unlocks L3
   and POI-targeted L4/L5 (D7). Still one command.
3. **Pick a depth** — `abicheck scan --estimate` prints projected per-level cost
   for *this* repo; the maintainer sets `source.budgets` / mode in `.abicheck.yml`
   accordingly (D6, D10).
4. **CI** — the GitHub Action (ADR-017) gains `mode:` + budget; PRs get a sticky
   comment (`pr-comment`) showing findings **plus** the coverage/confidence block
   so a partial scan is legible, never a bare "failed".
5. **Baselines** — `baseline push` stores a full-depth baseline (D9); PRs pull and
   diff against it, getting both the cached facts and the focusing seed for free.
6. **Standalone audit** — `abicheck scan --audit` (or `surface-report`) as a lint
   on a tag/release or pre-merge, independent of any baseline.
7. **Promote to gating** — findings start advisory; once the FP-rate gate is
   trusted for a check, the maintainer raises it to error via `crosschecks:` /
   severity config. Adoption never starts by blocking merges.

---

## Consequences

**Positive**

- A real always-on PR signal (D2/D3) catches ABI-affecting flag/macro changes,
  private-header leaks, and risky source patterns without a build or a compiler.
- The cross-check engine (D4) turns abicheck from single-source-of-truth into a
  multi-evidence system with corroboration-based confidence and better
  explanations — at low cost, since the inputs already sit in the snapshot.
- Flow 2 (D5) lets vendor/closed-source consumers contribute exact-build-context
  facts without shipping sources or letting abicheck rebuild the project.
- Evidence-directed focusing (D7) makes deep analysis affordable on large
  projects by spending L4/L5 cost only on binary/header-flagged entities.
- The single-release audit (D8) delivers value from one artifact — adoption
  needs no baseline and no second build.
- One typed `ScanRequest`/`ScanResult` API (D10) means CLI, MCP server, and CI
  wrappers share one engine, and `--estimate` lets each project pick a depth on
  measured cost instead of guesswork.

**Negative / costs**

- The Clang plugin and compiler wrapper are compiler-version-sensitive and add
  maintenance surface; mitigated by keeping them strictly optional optimizations
  behind the portable replay path (D5) and the ADR-032 action ceiling.
- New `ChangeKind`s (D4) must stay correctly partitioned (import-time assertion)
  and earn corpus/FP-rate-gate coverage before they can gate, to avoid false
  positives on legitimate internal noise.
- `scan` (D3) adds a CLI surface; it must remain a thin orchestrator so the
  authority rule (D1) is not diluted.

**Out of scope**

- Replacing or re-numbering the L0–L5 model.
- Making any source/cross-check finding authoritative for a `BREAKING` verdict.
- clangd-index integration (the existing call/include graph is sufficient).

---

## Relationship to existing ADRs

- **ADR-028** — preserves the evidence-pack authority rule (D1); adds Flow 2
  artifact protocol on the merge path (D5).
- **ADR-029/030/031** — D2/D4 consume L3/L4/L5 outputs; no model change.
- **ADR-032** — D5 plugin/wrapper are extractor providers under its security
  model and action ceiling.
- **ADR-033** — D2/D3 extend the CI ladder, replay scopes, caches, and coverage
  reporting; D5 realizes its "Phase 6: instrumented compiler/build plugins".
- **ADR-025** — D3 generalizes PR-diff-as-trigger into a scored escalation; D7
  reverses its localization walk to *target* the scan up front.
- **ADR-022** — D9 stores the full-depth baseline; PRs pull facts + focusing seed.
- **ADR-017** — D2/D3/D9 surface through the Action's `mode`/budget; UX step 4.
- **ADR-024 / `surface-report` / G11** — D8 single-release audit extends the
  single-binary surface tooling.
- **ADR-021b (MCP)** — D10 exposes `scan`/`audit`/`estimate` as agent tools.

See the phased work breakdown in
[plans/g19-pr-source-intelligence.md](../plans/g19-pr-source-intelligence.md).

---

## References

- Source-scan strategy proposal (uploaded field document, 2026-06).
- ADR-028 — Source and Build Evidence Pack
  ([028-source-build-evidence-pack.md](028-source-build-evidence-pack.md))
- ADR-032 — Evidence Extractor Plugin Interface
  ([032-evidence-extractor-plugin-interface.md](032-evidence-extractor-plugin-interface.md))
- ADR-033 — CI Rollout, Performance, Caching, and Validation
  ([033-ci-rollout-performance-and-validation.md](033-ci-rollout-performance-and-validation.md))
- Clang plugins: https://clang.llvm.org/docs/ClangPlugins.html
- LibTooling: https://clang.llvm.org/docs/LibTooling.html
- Tree-sitter: https://tree-sitter.github.io/tree-sitter/
