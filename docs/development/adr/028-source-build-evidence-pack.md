# ADR-028: Optional Source and Build Evidence Pack Architecture

**Date:** 2026-06-09
**Status:** Accepted — Phase 0 + Phase 5 implemented (the pack model — renamed
`EvidencePack`→`BuildSourcePack` in PR #356 — manifest, coverage, snapshot
reference, CLI surface, coverage reporting, and baseline-registry pack storage).
**Amended 2026-06-12** for the source-tree-centric input model and embedded
single-artifact storage (see the Amendment at the end of this ADR).
**Decision maker:** Nikolay Petrov

---

This ADR is the umbrella for a six-ADR set (028–033) that adds optional
source, build-system, compiler, and graph evidence to abicheck. The set is
split deliberately: a low-risk build-context MVP (ADR-029) can be accepted
and shipped without committing to heavyweight whole-program graph analysis
(ADR-031). The core design rule across all six ADRs is:

> Artifact-backed ABI evidence remains authoritative for shipped ABI
> verdicts. Source and build evidence explain, scope, localize, increase
> confidence, reduce false positives, and detect source/API risks that
> artifact comparison cannot see.

| ADR | Title | Role |
|---|---|---|
| 028 (this) | Optional Source and Build Evidence Pack Architecture | Umbrella: evidence layers, snapshot augmentation, provenance, CLI shape, non-authoritative source/build policy |
| [029](029-build-graph-toolchain-context-capture.md) | Build Graph and Toolchain Context Capture | Adapters for `compile_commands.json`, CMake, Ninja, Bazel, Make, compiler-recorded metadata |
| [030](030-source-abi-replay-and-linked-source-surface.md) | Source ABI Replay and Linked Source Surface | Per-TU source ABI dumps linked against exported-symbol/public-header roots |
| [031](031-source-implementation-graph-augmentation.md) | Source and Implementation Graph Augmentation | Optional include/type/call/build graph summaries; Clang first, Kythe/CodeQL as external backends |
| [032](032-evidence-extractor-plugin-interface.md) | Evidence Extractor Plugin Interface and Security Model | Adapter contract, raw/normalized artifact layout, versioning, redaction, failure policy |
| [033](033-ci-rollout-performance-and-validation.md) | CI Rollout, Performance, Caching, and Validation Strategy | Incremental rollout ladder, fast modes, PR behavior, baseline storage, validation plan |

---

## Context

abicheck currently compares built artifacts: binaries, debug information,
and public headers. ADR-003 defines the layered model:

- **L0 — binary metadata**: exported symbols, versions, SONAME, dependencies, binding, visibility.
- **L1 — debug info**: DWARF/PDB/BTF/CTF-derived type and layout evidence.
- **L2 — public header AST**: declarations, signatures, types, constants, qualifiers, and API-surface facts.

This architecture is intentionally artifact-centric. It is strong for
shipped ABI because it reasons over what was actually built, not merely what
source text appears to say (ADR-025 records why diff-as-primary-input is the
wrong model; ADR-026 records the evidence-tier boundary).

This ADR introduces optional source and build evidence for projects that
can provide sources, build files, generated manifests, compiler options, or
build-system query output. The goal is not to turn abicheck into a general
static analyzer. The goal is to give the existing ABI/API decision engine
more facts:

1. reduce false positives caused by mismatched header parsing, build flag
   drift, private/internal exported symbols, generated code, or ambiguous
   toolchain context;
2. detect source/API compatibility risks that do not necessarily appear in
   binary/debug data — macros, inline/constexpr/template source changes,
   default arguments, public header provenance, and source-only declaration
   changes (the residual space ADR-026 documents as undetectable from
   artifacts alone);
3. correlate source files, build targets, compiler flags, generated files,
   and binary symbols;
4. compare not only snapshot-to-snapshot but also
   build-context-to-build-context and graph-summary-to-graph-summary;
5. keep the default path fast, post-build, and usable in CI without
   mandatory instrumented rebuilds.

The key architectural tension is **evidence authority**. Binary/debug/header
evidence must continue to decide shipped ABI compatibility. Source, build,
and graph evidence add confidence, provenance, scoping, and source/API risk
signals — but they must not silently suppress artifact-backed breaking
changes. That is the same "demotion + disclosure, never deletion" rule that
ADR-024 established for surface scoping, extended to new evidence layers.

---

## Decision

### D1. Introduce an optional `EvidencePack`

Add a new top-level artifact: an **EvidencePack**. It is collected alongside
or after an ordinary `AbiSnapshot` and can be stored in the baseline
registry (ADR-022).

```text
AbiSnapshot
  ├── existing ABI/API facts from L0/L1/L2
  └── optional evidence_pack_ref

EvidencePack
  ├── manifest.json
  ├── build/build_evidence.json
  ├── source/source_abi.json              # optional
  ├── graph/source_graph_summary.json     # optional
  ├── toolchain/toolchain_fingerprints.json
  ├── raw/<extractor>/<content-addressed artifacts>
  └── normalized/<extractor>/<normalized JSON artifacts>
```

The pack is content-addressed and schema-versioned independently from the
ABI snapshot (ADR-015 governs the snapshot schema; the pack gets its own
`evidence_pack_version`). This avoids forcing the primary snapshot schema to
carry large source graphs or raw build-system dumps.

### D2. Add new evidence layers without renumbering L0/L1/L2

ADR-003's core source model is not rewritten. It is extended with optional
evidence categories:

| Layer | Name | Source | Purpose | Verdict authority |
|---|---|---|---|---|
| L0 | Binary metadata | ELF/PE/Mach-O | Exported binary ABI facts | Authoritative for exported ABI symbols |
| L1 | Debug info | DWARF/PDB/BTF/CTF | Layout/type/calling-convention evidence | Authoritative when matched to binary |
| L2 | Header AST | castxml/public headers | Public API declarations and source-level API facts | Authoritative for header-visible API |
| L3 | Build context | compile DB, CMake, Ninja, Bazel, Make, compiler metadata | Toolchain, flags, target graph, generated-file provenance | Context/confidence; can affect API interpretation |
| L4 | Source ABI replay | per-TU source/header parsing with real flags | Source-visible ABI/API facts not in binary/debug alone | API/source-risk evidence; never sole shipped-ABI authority |
| L5 | Source/implementation graph | Clang/Kythe/CodeQL/custom graph summaries | Include/type/call/build/source-to-binary graph reasoning | Explanation, localization, impact, optional risk evidence |

### D3. Artifact evidence remains the shipped-ABI source of truth

The comparison engine must preserve this rule:

```text
If L0/L1/L2 prove a breaking ABI/API change, L3/L4/L5 may:
  - explain it,
  - localize it,
  - add confidence or provenance,
  - show it is outside the declared public surface via the ADR-024
    surface ledger (demotion + disclosure),
  - connect it to build options or source changes,
  - group it with related findings,
  - or mark it as intentionally suppressed with an auditable reason.

But L3/L4/L5 must not silently delete it.
```

Findings produced *only* by L3/L4/L5 evidence are new `ChangeKind` entries
that follow the existing classification rules (ADR-011): each new kind is
placed in exactly one of `BREAKING_KINDS`, `API_BREAK_KINDS`, `RISK_KINDS`,
or `COMPATIBLE_KINDS`, and the existing five-tier `Verdict`
(`NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` / `API_BREAK` /
`BREAKING`, ADR-009) is computed from them with worst-verdict-wins as
today. The default placement for source/build-only kinds is
`API_BREAK_KINDS` (source-level breaks such as a public macro value change)
or `RISK_KINDS` (deployment/context risks such as build-flag drift) — never
`BREAKING_KINDS`, unless artifact-backed evidence also supports an ABI
break or the active policy profile (ADR-010) explicitly escalates that
class.

### D4. Store evidence as normalized facts plus raw provenance

Every extractor writes two forms:

1. **Raw artifact**: the external tool output or command result, stored
   content-addressed under `raw/`.
2. **Normalized fact model**: abicheck-owned JSON under `normalized/` or a
   canonical file such as `build_evidence.json`.

Raw artifacts serve debugging and reproducibility. Normalized facts are the
only stable input to comparison and reporting. External formats such as
Android `.sdump`, Bazel proto output, CodeQL databases, or Kythe `.kzip`
files must not become abicheck's stable public schema.

### D5. Use stable cross-layer entity identifiers

Add a canonical identity model that can join facts across build systems,
source files, debug info, and binary symbols:

```json
{
  "entity_id": "sha256:...",
  "kind": "function|variable|record|enum|typedef|macro|file|target|compile_unit|binary_symbol|build_option",
  "names": {
    "source_qualified": "foo::Bar::baz(int)",
    "mangled": "_ZN3foo3Bar3bazEi",
    "demangled": "foo::Bar::baz(int)",
    "usr": "c:@N@foo@S@Bar@F@baz#I#"
  },
  "locations": [
    {"path": "include/foo/bar.h", "line": 42, "column": 3, "origin": "PUBLIC_HEADER"}
  ],
  "binary_refs": ["elf:symbol:_ZN3foo3Bar3bazEi"],
  "build_refs": ["target://libfoo", "compile-unit://src/bar.cpp"],
  "confidence": "high|reduced|unknown"
}
```

Preferred join keys, in order:

1. exact exported symbol or debug linkage name;
2. fully qualified source declaration identity (Clang USR or
   castxml-equivalent model);
3. declaration source path plus qualified name plus signature hash;
4. fuzzy fallback only for diagnostics, never for hard verdicts.

### D6. CLI entry points for separate collection and combined dump

Support both post-build and integrated workflows:

```bash
# Existing path remains valid and unchanged.
abicheck dump libfoo.so -H include/ -o libfoo.abi.json

# Collect optional evidence from an existing build tree without rebuilding.
abicheck collect \
  --binary build/libfoo.so \
  --headers include/ \
  --build-dir build/ \
  --cmake \
  --ninja \
  --output libfoo.evidence/

# Attach an evidence pack to a snapshot.
abicheck dump build/libfoo.so -H include/ \
  --build-info libfoo.evidence/ \
  -o libfoo.abi.json

# Compare artifact snapshots and evidence packs together.
abicheck compare old.abi.json new.abi.json \
  --old-build-info old.evidence/ \
  --new-build-info new.evidence/ \
  --format sarif

# One-shot CI convenience.
abicheck compare old.so new.so \
  --headers include/ \
  --collect-mode build \
  --build-dir build/
```

`--collect-mode` is the **single** compare-side knob for inline evidence
collection; its values are the CI modes defined in ADR-033 D2
(`off | build | source-changed | source-target | graph-summary | graph-full`).
The standalone `collect` command is the only other entry point —
no additional `--collect` style flags are introduced.

`collect` must never run arbitrary build commands by default. It
may inspect existing build outputs, generated metadata, and build-system
query interfaces. Any action that can build, execute project code, or
invoke a compiler wrapper requires an explicit opt-in flag (the capability
model is specified in ADR-032 D5).

### D7. Report evidence coverage explicitly

Every compare report includes an evidence coverage table:

```text
Evidence coverage:
  L0 binary metadata:        present, high confidence
  L1 debug info:             present, DWARF 5, reduced confidence: split-dwarf file missing for 2 CUs
  L2 public header AST:      present, header-scoped
  L3 build context:          present, CMake+Ninja, 142 compile units, 1 target graph
  L4 source ABI replay:      partial, changed public headers only
  L5 source graph summary:   not collected
```

Coverage and confidence become first-class reporting fields across all
output formats (ADR-014). Users must be able to tell which findings are
artifact-proven, source-only, build-context-only, or graph-assisted.

### D8. Schema versioning

The pack manifest carries:

```json
{
  "evidence_pack_version": 1,
  "abicheck_version": "...",
  "created_at": "...",
  "source_root": {"path_redacted": true, "repo_hash": "sha256:..."},
  "inputs": {},
  "extractors": [],
  "artifacts": [],
  "coverage": {},
  "redaction": {}
}
```

The primary `AbiSnapshot` only stores a reference:

```json
{
  "evidence_pack": {
    "schema_version": 1,
    "content_hash": "sha256:...",
    "path_hint": "libfoo.evidence/",
    "coverage_summary": {}
  }
}
```

This keeps old snapshot readers functional (ADR-015 backward-compatibility
rules apply: unknown optional fields are ignored) and avoids bloating
normal ABI dumps.

---

## Options considered

| Option | Description | Decision |
|---|---|---|
| A. Build all source/build facts directly into `AbiSnapshot` | Simple single file, but potentially huge and unstable. | Rejected. Violates snapshot stability (ADR-015) and makes source-graph storage mandatory. |
| B. External evidence pack referenced by snapshot | Separate schema, optional, cacheable, diffable. | **Accepted.** |
| C. Replace castxml/header AST with a Clang LibTooling source frontend | More power, but conflicts with the lightweight-tool constraint (ADR-001) and duplicates current L2. | Rejected for core; allowed as optional extractor (ADR-030 D3). |
| D. Full graph database as required input | Powerful but too heavy for default CI. | Rejected for core; accepted as optional backend (ADR-031 D5). |

---

## Consequences

### Positive

- Preserves current abicheck behavior for users who only have binaries,
  debug info, and headers.
- Creates a migration path: build-context capture → source ABI replay →
  graph reasoning, each independently adoptable.
- Supports fast, post-build evidence collection when build metadata already
  exists.
- Makes false-positive reduction auditable through provenance, confidence,
  and the ADR-024 surface ledger.
- Allows projects to store richer baselines without forcing all users into
  heavyweight graph extraction.

### Negative / risks

- More schema surface to version and test.
- Source/build evidence can contain absolute paths, environment variables,
  and command-line secrets; redaction is mandatory (ADR-032 D7).
- Joining source declarations, debug types, and binary symbols is imperfect
  for templates, overloads, local classes, anonymous namespaces, LTO, and
  generated code.
- Users may over-trust source-only findings. Reports must label authority
  and confidence clearly (D7, ADR-030 D10).

---

## Implementation plan

| Phase | Scope | Output |
|---|---|---|
| 0 | `EvidencePack` manifest and coverage model | Empty pack can be attached to a snapshot and reported |
| 1 | Build evidence from compile DB/CMake/Ninja/Bazel adapters (ADR-029) | `build_evidence.json`, build-option diffs |
| 2 | Source ABI replay summary (ADR-030) | `source_abi.json`, source/API findings with provenance |
| 3 | Source graph summary (ADR-031) | `source_graph_summary.json`, graph-to-graph diff basics |
| 4 | External graph backend adapters (ADR-031) | Kythe/CodeQL adapters emit normalized summaries |
| 5 | Pack storage in baseline registry (ADR-022, ADR-033) | Baseline downloads can include optional evidence packs |

### Implementation status

**Phase 0** and **Phase 5** are implemented; Phases 1–4 are delivered by the
sub-ADRs (ADR-029/030/031) and tracked there.

- **Phase 0** — `abicheck/evidence/`: the `EvidencePack` model (`pack.py`),
  manifest/coverage/`EvidencePackRef` (`model.py`), the `collect-evidence` CLI
  surface, and the compare-side evidence-coverage report (D7). An empty,
  manifest-only pack can be attached to a snapshot and reported (D1, D8).
- **Phase 5** — `abicheck/baseline.py`: `FilesystemRegistry.push(...,
  evidence=…)` copies a materialized `EvidencePack` into `<key>/evidence/`, and
  `pull_evidence(key)` loads it back, so a stored baseline (ADR-022) can carry
  its optional source/build evidence. Integrity is two-layered:
  `EvidencePack.verify_integrity()` recomputes the on-disk normalized-payload
  digests against the manifest (catching an edited normalized file that
  `content_hash` alone would trust), and the pack `content_hash` is checked
  against the value recorded in the baseline metadata at push time
  (`BaselineMetadata.evidence_content_hash`) — the same checksum discipline the
  snapshot already gets. Wired into the CLI as `baseline push --evidence <pack>`
  and `baseline pull --evidence-output <dir>`. A re-push without `--evidence`
  drops any stale pack; `delete` removes the pack with the baseline.

The remaining items are sub-ADR scope: build adapters (Phase 1, ADR-029),
source ABI replay (Phase 2, ADR-030), and the graph summary / external backends
(Phases 3–4, ADR-031).

---

## Relationship to other ADRs

- **ADR-003 (Data Source Architecture)** — defines L0/L1/L2; this ADR adds
  L3/L4/L5 as optional categories without renumbering.
- **ADR-009 (Verdict System)** / **ADR-011 (Change Classification
  Taxonomy)** — new evidence-layer findings are ordinary `ChangeKind`
  entries inside the existing partition and verdict computation.
- **ADR-015 (Snapshot Serialization)** — the snapshot carries only an
  optional `evidence_pack` reference; pack schema versions independently.
- **ADR-017 (GitHub Action)** / **ADR-022 (Baseline Registry)** — CI and
  storage integration points (detailed in ADR-033).
- **ADR-020a (Build-Context Aware Header Extraction)** — the accepted
  compile-DB ingestion this set generalizes (ADR-029).
- **ADR-024 (Public ABI Surface Resolution)** — the surface ledger
  (demotion + disclosure) is the template for evidence authority here.
- **ADR-025 (PR-Diff-Aware ABI Evaluation)** — diff as trigger/localizer;
  evidence layers give the localizer real provenance to point at.
- **ADR-026 (Source-Only Changes and the Evidence-Tier Boundary)** — the
  residual source-only space L4 targets; see ADR-030 for the boundary
  update.
- **ADR-027 (API Surface Intelligence)** — surface-level reasoning that
  graph evidence (L5) can feed.

## References

- [Clang JSON Compilation Database](https://clang.llvm.org/docs/JSONCompilationDatabase.html)
- [Clang LibTooling](https://clang.llvm.org/docs/LibTooling.html)
- Android VNDK header checker architecture (`header-abi-dumper`/`-linker`/`-diff`)


## Amendment (2026-06-12): source-tree-centric inputs

Using the feature in practice — a prebuilt package (e.g. a conda-forge `.so`)
checked against the **source checkout at its build tag** — showed the
build-tree-centric framing was too narrow. This amendment *refines* the decisions
above; the authority rule (D3) is unchanged. The temporary root-level
implementation checklist has been removed after the work landed. Per-layer
decisions live in the relevant ADRs (029 inputs, 030 scopes/flags, 032
build-tool config, 033 merge).

- **Rename (PR #356).** `EvidencePack`→`BuildSourcePack`; the opaque `--evidence`
  surface becomes concrete `--build-info` (L3) and `--sources` (L4/L5). The L0–L5
  *detectability* vocabulary (`min_evidence`) is a separate concept and keeps its
  name.
- **D6 (CLI) refined.** Three independent inputs, resolvable from different
  locations: binary/package (L0/L1), public headers (L2), and a **source tree**
  (`--sources <dir>` → auto L4 + L5; the `--source-abi`/`--source-graph` action
  flags are removed). Build metadata (`--build-info`) is optional and decoupled
  (029).
- **D8 (storage) refined.** Embedded single-artifact storage is the **default**:
  `dump --build-info/--sources` folds normalized facts into the `.abi.json` so
  `compare old.json new.json` needs no out-of-band directories. The `collect`
  pack directory remains an optional override / raw-provenance home.
- **New: `merge`.** `abicheck merge a.json b.json -o out.json` combines an
  independently-produced artifact-side and source-side dump into one baseline
  (033).
- **`collect` demoted** to an advanced command; the common path uses
  `dump --sources/--build-info`.
