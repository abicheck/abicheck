# G18 — Bazel build-evidence, validated end-to-end

**Registry:** `UC-TC-bazel-build-evidence` (`modeled`)
**Effort:** M · **Risk:** medium (Bazel toolchain is heavy; jsonproto schema drift)

## Problem

abicheck ships a Bazel L3 adapter (`adapters/bazel.py`) that turns
`bazel cquery`/`aquery` jsonproto into `BuildEvidence` targets / compile+link
units — but it has **never been validated end-to-end on a real Bazel C++
project**. The 2026-06 field evaluation hit this directly (P21): oneDAL
(`uxlfoundation/oneDAL`) builds with **Bazel + a legacy makefile, no CMake**, so
the L3/L4/L5 source path was unreachable without the project's full Intel
toolchain (DPC++/oneMKL/TBB). The artifact (L0/L1) scan worked fine; the
build/source tier could not be exercised. Status is therefore **modeled**: the
parser exists, the coverage claim is not backed by a real run.

## Pointers

- `abicheck/buildsource/adapters/bazel.py` — the adapter (`cquery`/`aquery`
  jsonproto → compile/link units; live or pre-captured).
- `abicheck/buildsource/extractor.py` — the action model; `query_build_system`
  is the opt-in tier a live `aquery` would fall under (ADR-032 D5).
- `abicheck/buildsource/inline.py` `_run_build_query` — the `--allow-build-query`
  subprocess path (could drive `bazel aquery`).
- Field-eval evidence: `eval/FINDINGS.md` P21; `eval/FOLLOWUPS.md` §E2.

## Approach

1. Capture a real `bazel aquery --output=jsonproto 'mnemonic("CppCompile", //...)'`
   (+ `cquery`) from a small, self-contained Bazel C++ project — **pre-captured**,
   so the test is non-executing and Bazel-free in CI (ADR-028 D6).
2. Add a fixture + unit/integration test feeding that export through
   `adapters/bazel.py` and asserting non-empty compile/link units + a usable L3.
3. Optionally wire a live `--build-query 'bazel aquery …'` recipe behind
   `--allow-build-query` for users who have Bazel.

## Acceptance

- A committed jsonproto fixture round-trips through the adapter to non-empty
  `BuildEvidence` (targets + compile units), asserted by a test.
- `dump --build-info <captured-pack>` embeds the Bazel-derived L3.
- Registry entry flips `modeled` → `partial`/`complete` with the test as evidence.

## Status

`modeled` — adapter code exists; no real-project validation yet (this gap).
