---
doc_type: contributor
level: advanced
lifecycle: active
---

# Design hardening: make the recurring defect families unrepresentable

**Origin:** A review (2026-10-01) of the 102 fix PRs merged to `main` between
2026-08-01 and 2026-10-01 (`git log --first-parent`, subjects starting
`fix`/`perf+fix`/`perf/fix`), read against the families in
[Defect-family harnesses](defect-family-harnesses.md).

**ADR:** none new. Every phase below finishes a decision an existing ADR or
plan already owns ([ADR-063](../adr/063-one-semantic-pipeline.md),
[ADR-061](../adr/061-responsibility-package-architecture.md),
[ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md)).
A phase that changes a snapshot or report schema needs an amendment to the
owning ADR first, as those plans already require.

**Type:** Sequencing plan. For Phases 1–3 it owns *order and exit
criteria* only: each names the plan that owns the design, and this document
must not grow a second copy of it. Phases 4–6 have no existing design owner
for their production-code half, so this plan **introduces** those designs
(each phase says which part is new). Once a dedicated plan or ADR adopts one
of them, the phase here shrinks to a pointer.

## Problem

The [defect-family harnesses](defect-family-harnesses.md) now *detect* the
seven recurring families (F1–F7). Detection is not the same as robustness:
each harness finds the next site, and the fix is still made one site at a
time. The fix history shows why. In every large family the implementation
still *allows* the wrong state to be written:

| Family | What the code still permits | Representative fixes (Aug–Sep 2026) |
|---|---|---|
| F1 Unknown ≠ value | A producer can return a plain `bool`/`set`/`[]`/`None`; 61 sites still read the legacy `.visibility` field; `facts` is `authority: mixed` in `docs/_meta/one-semantic-pipeline-status.yaml` | #1384, #1385, #1398, #1399, #1324, #1340, #1316 |
| F2 Route parity | Requests for release members and for each entry point are rebuilt field by field; `AbiSnapshot(...)` is constructed in 20 modules | #1391, #1422, #1393, #1347, #812, #802, #795, #796 |
| F3 Identity is semantic | Platform decoration and checkout paths are stripped at the join site, not at extraction | #1355, #1383, #1367, #1370, #761, #1330, #1392, #1369 |
| F4 Structure over spelling | 205 name/spelling decision sites (H4 inventory), 193 without a structural confirmation | #1411, #1308, #1344 |
| F5 Optimization ≡ reference | 39 cache decorators and 42 pool sites, each with its own key and no shared reference switch | #1336, #1361, #1340, #1331, #1306, #1371 |
| Subprocess lifecycle | Several bounded-run implementations under `buildsource/`, plus the Action parsing stderr | #1395, #1427, #1415 |

## Review of the plans that already own this work

| Plan / item | Current status (as recorded) | Review verdict |
|---|---|---|
| [Defect-family harnesses](defect-family-harnesses.md) H1–H7 | H1–H7 landed; merge-quiescence gate not landed | **Keep.** The harnesses are the acceptance oracle for every phase here. Gap: the real bugs they found are parked as strict xfails with no owner or order — Phase 0 below adopts them. |
| H6 real-library corpus | First run: 5 of 9 known-compatible pairs report BREAKING (oneTBB ×2, protobuf, zstd, libxml2), "open for triage" | **Promote to P0.** These are user-visible false positives on real libraries — the most expensive class in the review. Nothing else should take priority over them. |
| H5 reference mode | Harness only; deliberately no production `ABICHECK_REFERENCE_MODE` (`tests/test_family_f5_optimization_reference.py`) | **Change.** Two H7 mutants survive *because* there is no central switch (the disk-cache cell never varies one key input; `ABICHECK_MAX_THREADS=1` still takes the pooled release path). Phase 4 adds the switch in production code. |
| Merge-quiescence gate | Not landed (repository setting) | **Do now** (Phase 0). About 25 follow-up chains in the window were merges ahead of the last review round. No code needed. |
| [One Semantic Pipeline](one-semantic-pipeline.md) Phase 5B (fact consumption) | `facts`: `authority: mixed`, `lifecycle: wired`; 18 facts `CONSUMED` | **Reprioritize up** — it is the structural fix for F1, the largest family. Phase 1 below sets its exit criterion as "legacy fields deleted", not "more facts consumed". |
| [One Semantic Pipeline](one-semantic-pipeline.md) Phase 2B (identity consumers) | `identity`: `authority: mixed`, `lifecycle: wired` | **Keep, narrow.** Phase 3 below takes only the decoration/path normalization at extraction, which is the part every F3 fix touched. |
| [Evidence entity model](evidence-entity-model.md) | Phases 1–4 landed with documented remaining gaps | **Keep.** Its "readers that treated no edge as absence" inventory is an F1 input to Phase 1. No new work here. |
| [Duplication & convergence](duplication-and-convergence-assessment.md) Phases 1–2 (artifact resolution, effective configuration) | Proposed; Phase 0 item 1 only | **Reprioritize up** for the F2 slice only (Phase 2 below). Phases 3–5 of that plan keep their own order. |
| [L2/L4/L5 extraction convergence](l4-l2-extraction-convergence.md) Phase 3 (one compile-invocation model) | Proposed | **Keep after Phase 2.** Several F2 fixes (#796, #802, #812, #1422) were invocation drift; this is their structural owner. |
| [Target ownership and extraction scope](target-ownership-and-extraction-scope.md) | Proposed; not started | **Defer.** Not implicated in the review's fix history beyond #1347; do not start before Phases 1–2 here. |
| Performance work (`perf:` PRs, ~15 in September) | Ongoing | **Gate behind Phase 4.** Each new cache or pool adds an F5 site; land the central cache wrapper first, then resume. |

## Phases

Each phase ends when (a) the named legacy path is **deleted**, not only
wrapped, and (b) the family harness has no strict xfail and no `UNCOVERED`
entry for that phase's scope. "Lower risk because both paths remain" is not
an exit state; that is the `mixed`/`wired` state the families come from.

### Phase 0 — Close the known open bugs and stop the merge pattern (first)

1. Triage and fix the H6 corpus false positives (oneTBB ×2, protobuf, zstd,
   libxml2). For each: root-cause into one of F1–F5, fix at the family level,
   and flip the corpus baseline. Exit: all 9 known-compatible pairs report no
   BREAKING/API_BREAK.
2. Fix every bug the harnesses found and parked as a strict xfail:
   - H1: unknown `source_header_fact` on both sides drops a real break to
     NO_CHANGE.
   - H2: `pattern_verdicts` default differs between front ends;
     `*_evidence_depth` and `suppression_audit` set only by the CLI;
     `effective_config_digest` tier depends on the route.
   - H3: a checkout path containing a space leaks into canonical identity;
     PE vectorcall decoding maps two names to one identity.
   - H4: name-only enum-sentinel rule; `detail::`/`impl::` vs `priv::`
     inconsistency.
3. Enable the merge-quiescence rule (review bots' latest run on the head SHA,
   no unresolved red findings; Windows/macOS smoke before merge for
   `touches:paths|shell|platform`).

#### Phase 0 status (2026-10-01)

Re-checked against `main` before starting; several items had already been
fixed by the time this plan landed.

| Item | Status |
|---|---|
| H1 unknown `source_header_fact` | Already fixed on `main` (no strict xfail left in H1). |
| H2 `*_evidence_depth`, `suppression_audit`, digest tier | Already fixed (`KNOWN_DIVERGENCES` is empty). |
| H2 `pattern_verdicts` default (CLI `True`, typed API `False`) | **Fixed** (decision 1A): the typed API defaults to `True` like the CLI; ADR-027's deferral now points at ADR-068, which settled it. |
| H3 path-with-space, PE vectorcall | Already fixed. |
| H4 name-only enum sentinel | **Fixed** on this branch: the name only nominates; `compare.enum_sentinel.holds_enum_maximum` confirms on both sides (width-forcing and other sentinel-named members are not peers). |
| H4 `detail::`/`impl::` vs `priv::` | **Fixed** (decision 2A, option A′): `internal_leak.detect_internal_leaks` keeps its pointer-only leniency only when `policy.layout_visibility.layout_proven_invisible` holds -- every record the two sides carry for the type is opaque or defined in a private header. Unknown visibility keeps the finding, in every namespace; H4's two strict xfails are gone and an exhaustive origin × opaque × side matrix pins the rule. |
| H7 `release_dispatch_drops_member` | **Fixed**: `ABICHECK_MAX_THREADS` now bounds the release worker plan, so a budget of 1 takes the sequential path and the H5 reference arm differs from the pooled one. |
| H7 `cache_key_drops_binary_content` / `cache_key_drops_version` | **Fixed**: two H5 cells each vary exactly one disk-cache key input. No H7 harness gap remains. |
| H6 corpus false positives | In progress. Root causes found: (a) the corpus compared binaries without headers, so removed accidental exports read as breaks -- the harness now supplies each package's headers with `--contract public`; (b) `--contract public` could never say "searched completely, no commitment" -- closed by the header-text identifier index (ADR-063 2026-10-01 amendment, schema v55), which keeps catalog case97 (`#ifdef`-guarded declaration) unresolved; (c) `param_renamed` was an API break although neither C nor C++ has named arguments -- now a risk natively, still an API break in `abicheck compat` (ABICC parity); (d) zstd's removed `ZSTD_c_experimentalParam6` is a real removal outside zstd's stability promise -- a cited corpus suppression. |
| Found while triaging: `--contract public` exits 0 when an undeclared export's removal stays `UNKNOWN_UNRESOLVED` (catalog case97 with `-H v1.h --contract public`) | **Fixed**: when every required provider closed, each `UNKNOWN_UNRESOLVED` finding is a `finding_relevance` entry in `contract_coverage_failures` and floors the exit to 1 (case97 now exits 1; `contract.unresolved=warn` accepts it). The whole-surface metric kinds, which were wrongly unresolved, are now `NOT_APPLICABLE`. |
| 3B: `public` as the default contract when headers are given | **Kept gated** on the public-contract-default plan's Phase 7 (it would flip catalog case182 to non-breaking). |
| Merge quiescence | Repository setting — for a maintainer. |

### Phase 1 — Unknown cannot be written as a value (F1)

Owner of the design: ADR-063 Phase 5B.

- Every evidence producer returns `Fact[T]` (or a typed "unread" result for a
  collection). A bare `[]`, `set()` or `False` from a producer that failed or
  was not consulted becomes unconstructible.
- One merge rule for evidence, in `model/`: unknown ⊕ absent = unknown; only a
  completed read yields absent. Every current hand-written merge calls it.
- Migrate the 61 legacy `.visibility` readers to `model/surface_facts.py`'s
  accessors, then delete the field's decision role. Shrink
  `fact-field-readers`'s `KNOWN_UNMIGRATED_READERS` to empty.
- Exit: `facts` reaches `authority: self` in the status ledger; H1 inventory
  has no `UNCOVERED` entries.

### Phase 2 — One construction path per request and per snapshot (F2)

Owner of the design: duplication-and-convergence Phases 1–2.

- Release members receive the parent's resolved request plus an explicit
  per-member delta type. Any field not in the delta is inherited by
  construction, so a new request field cannot be dropped.
- One `AbiSnapshot` factory in `workflows/` applies ownership, dependency
  scoping and provenance. The other constructors either call it or are
  storage decoders (which are allowed to construct directly and are listed).
  An AST gate rejects a new direct constructor outside that list.
- Exit: H2 has no strict xfail; the snapshot-constructor allowlist contains
  only decoders and the factory.

#### Phase 2 status (2026-10-02)

| Item | Status |
|---|---|
| Release members inherit the parent request | **Landed.** `workflows/release_member_request.py`: the release resolves one `ReleaseMemberCompareRequest`; each member gets it plus a frozen `MemberDelta` (operands and per-member debug files only) via `dataclasses.replace`, and `run_compare_kwargs` forwards every field by iterating the dataclass. The 34-slot `_CompareReleaseCommonArgs` tuple and the keyword-by-keyword `service.run_compare` call are deleted. `tests/test_release_member_request.py` adds a synthetic field to the request type and shows every member's `run_compare` call receives it. The F2/F5 mutant patches were rebased and are still killed. |
| One `AbiSnapshot` factory | **Landed.** `workflows/snapshot_factory.py`: `new_snapshot` (construct, optionally finish), `finish_snapshot` (provenance → dependency scope → ownership, each only when given), `finish_provenance`, `absent_baseline`. All ~25 production constructors now call it. The dump/native/header-only/release-surface finishing calls go through `finish_snapshot`. Inner-layer sites were moved out rather than allowlisted: `build_snapshot_from_dwarf` moved to `workflows/dwarf_snapshot_assembly.py` (the DIE walk stays in `extract` as `extract_dwarf_declarations`); `header_only_dump.py` and `dumper_elf_fallback.py` were reclassified `extract` → `workflows` (each assembles a snapshot; only `workflows`/root modules import them); the post-processing pipeline receives its absent-baseline stand-in from `checker` instead of building one; the ELF symbol diff lost its placeholder-snapshot calling convention; the dead `python_ext.detect_python_extension_from_binary` was deleted. |
| AST gate | **Landed.** `tests/test_snapshot_factory_gate.py` (`repo_scan`) rejects any `AbiSnapshot(...)` call -- by name, import alias, or attribute -- outside the allowlist, and fails on a stale entry. Allowlist: the factory, `storage/snapshot_codec.py` (JSON decoder), `compat/abicc_dump_import.py` (ABICC dump decoder). |
| Exit: H2 has no strict xfail | **Met** (`KNOWN_DIVERGENCES` was already empty after Phase 0; still empty). |
| Finishing passes only through the factory | **Landed.** Every direct call of `apply_provenance`, `resolve_dependency_scope` and `classify_extracted` now goes through `finish_snapshot` or its one-pass helpers (`finish_provenance`, `finish_dependency_scope`, `finish_ownership`): `cli_resolve`, `cli_buildsource`, `appcompat`, `compat/run_inputs`, and `service.run_dump`'s scoping wrapper, which moved from `dumper_scoping` (`extract`) to `workflows/run_dump_scope.py`. A second `repo_scan` gate in `tests/test_snapshot_factory_gate.py` rejects a direct pass call outside the factory (plus `classify_extracted`'s own call of `stamp_ownership`) and fails on a stale entry. |
| Remaining, by design | The passes still run at the point a route has their inputs (dump wrapper, `resolve_input`, the CLI/compat/appcompat front ends), not all inside the one `new_snapshot` call: the scoping roots and ownership request are only known there. They can no longer be applied out of order or bypass the factory. Request-side convergence beyond the release fan-out (one resolved configuration across `compare`/`dump`/typed API/Action) is the duplication plan's own Phase 2 and is not part of this phase. |

### Phase 3 — Identity is normalized at extraction (F3)

Owner of the design: ADR-063 Phase 2B.

- Each platform decoration (Mach-O leading `_`, x86 stdcall/fastcall/
  vectorcall, Itanium C1/C2/D0/D1/D2 variants, ELF version suffixes) is one
  codec module under `model/`, with round-trip and injectivity property tests.
  It is applied once, in `extract/`; matching and joining code receive
  undecorated names only.
- Paths that can reach identity are a root-relative path type at extraction.
  An absolute path cannot be passed where an identity component is expected.
- Exit: H3's 22 `UNCOVERED` identity functions are covered or deleted.

### Phase 4 — Caches and parallelism go through one wrapper (F5)

Owner of the design: **new, introduced here.** The test-side oracle is
[Defect-family harnesses](defect-family-harnesses.md) H5, which deliberately
added no production switch; this phase reverses that decision.

- One cache wrapper (memory and disk) whose key is derived from the request
  identity, records hits, and honours a production
  `ABICHECK_REFERENCE_MODE=1` that disables every cache and forces the
  sequential path (including the release fan-out at
  `ABICHECK_MAX_THREADS=1`).
- Worker functions given to a pool may read only immutable inputs and
  request-scoped caches; an AST gate rejects module-level mutable caches.
- Exit: the two surviving H7 mutants are killed; H5 runs in reference mode in
  a scheduled lane.

### Phase 5 — Name heuristics cannot raise severity alone (F4)

Owner of the design: **new, introduced here.** It promotes
[Defect-family harnesses](defect-family-harnesses.md) H4's test inventory to a
runtime registry; H4 stays the oracle.

- A spelling-based classifier may only lower confidence or route a finding to
  review. Raising severity requires a named structural fact. The H4 registry
  becomes the runtime registration point, not only a test inventory.
- Exit: H4's 193 uncategorized sites shrink below an agreed ceiling, and every
  site that can raise severity is registered.

### Phase 6 — One subprocess supervisor; structured output only

Owner of the design: the structured-output half finishes
[ADR-063](../adr/063-one-semantic-pipeline.md)'s 7B item (#1415). The
supervisor half is **new, introduced here**.

- One bounded-run implementation (process group, SIGTERM then SIGKILL,
  bounded reap) used by every child-process site in `buildsource/` and
  `extract/`.
- The composite Action and every script decide outcomes only from the JSON
  report and exit code, never from stderr text (finishes #1415).
- Exit: no other `start_new_session`/`killpg` implementation remains outside
  the supervisor.

#### Phase 6 status (2026-10-02)

| Item | Status |
|---|---|
| One bounded-run implementation | **Landed.** `abicheck.deadline.supervised_popen` owns spawn-in-group, SIGTERM-cleanup registration, and SIGTERM→SIGKILL teardown on any exception; `run_bounded` (now with `env`) is built on it. Migrated: `buildsource/adapters/{bazel,ninja}.py`, `buildsource/extractor_manifest.py`, `buildsource/source_extractors/{android,clang}.py` (probes), `buildsource/toolchain_probe.py`, and all four probes in `dumper_toolchain.py` (the capped `--version` reader was the last product-side `start_new_session`/`killpg` pair). `abicheck/extract/` had no direct child-process site. |
| Exit: no other `start_new_session`/`killpg` | **Met, with one reviewed exception.** `tests/test_subprocess_supervisor.py` (`repo_scan`) scans `abicheck/`, `scripts/` and `action/`. `scripts/perf_receipt.py` stays allowlisted: it is stdlib-only and measures an installed abicheck that may be a base revision without the supervisor. The same file forbids any direct `subprocess` call in `abicheck/buildsource/`, `abicheck/extract/` and `dumper_toolchain.py`, and states the teardown contract over three exit paths (error, interrupt, timeout) with a SIGTERM-ignoring grandchild. |
| Remaining direct sites outside Phase 6's scope | `package.py` (zstd/rpm2cpio streaming pipelines), `demangle.py`, `source_smoke.py`, `probe_harness.py`, `clang_layout_tool.py`, `workflows/changed_paths.py`, `frontends/cli/runtime.py`, `cc_wrapper.py` (an `exec`-style passthrough). None manages a process group; widening the gate to them is the next slice. |
| Structured output only (#1415) | Not re-audited in this slice; `action/run.sh` already documents its retired stderr greps. |

## Order and dependencies

Phase 0 first, alone. Then 1 and 2 in parallel (different owners, no shared
files beyond `model/`). Phase 3 after 1 (both touch identity of export
evidence). Phase 4 before any further `perf:` work. Phases 5 and 6 are
independent and may run whenever a contributor is free.

## Acceptance for the whole plan

Re-run the [defect-family harnesses](defect-family-harnesses.md) acceptance
rule — revert each family's historical fixes and require the harness to fail
— and additionally require that the *re-introduced* bug can no longer be
written without either a type error, an AST-gate failure, or deleting a
registry entry. That is the difference between "tracked by tests" and
"robust implementation".

## Out of scope

- New detection capability or new `ChangeKind`s.
- Storage format changes beyond what Phase 1's `Fact` persistence already
  requires.
- Re-opening decisions recorded as closed in the plans above.
