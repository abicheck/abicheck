# G17 — Real-world upstream-library validation corpus

**Registry:** `UC-WORKFLOW-real-world-corpus` (`partial`)
**Effort:** M · **Risk:** medium (network to a package index; toolchain for the source tier)

## Problem

abicheck's correctness is exercised almost entirely by the **synthetic**
`examples/case*` fixtures (141 minimal C/C++ cases) and unit tests. Those are
precise but small and author-controlled. The 2026-06 field evaluation showed that
**real upstream libraries** exercise shapes the fixtures don't: split packaging,
multi-`.so` bundles, versioned-symbol schemes (ICU/OpenSSL/LLVM), DWARF-bearing
release builds, and scale (LLVM ~150 MB / ~31k exported funcs). Nothing in-repo
continuously validates abicheck against that real surface, so a regression in a
real-world verdict would not be caught.

The evaluation produced a **reproducible corpus** under `eval/`: a curated
`manifest.yaml` (library, version pair, **expected verdict**, `.so` stem, optional
source repo/tags), a `runner.py` that fetches from conda-forge and runs
`abicheck dump`/`compare`, and a generated `REPORT.md` + schema'd
`results/latest.json`. The **binary (L0/L1) tier** is validated today — 22/22
verdicts match the manifest's `expect`. Two pieces remain.

## Pointers

- `eval/manifest.yaml` — curated corpus (source of truth; `source:` repo/tags
  already present for zlib/zstd/snappy).
- `eval/runner.py` — `run()`/`scan_one()` (binary tier); `--report-only`.
- `eval/condafetch.py` — conda-forge fetch/extract (handles split packages, `.conda`).
- `eval/results/latest.json` — schema'd results (`result_schema` 1).
- Retired source-tier prototype lives in git history at
  `eval/field-eval/scripts/bsdrive.py` (clone → configure → `dump --sources`).
- CI lane pattern to mirror: `.github/workflows/mutation.yml` /
  `performance.yml` (scheduled / label-triggered).

## Approach

1. **Source tier (D1).** Add `runner.py --tier source`: for manifest entries with
   a `source:` block, clone at the tag, generate a compile DB (cmake configure or
   `--build-query`), run `dump --sources --collect-mode source-target`, and record
   L3/L4/L5 coverage + timings into `results/`. Gate on `clang`/`cmake`; skip
   gracefully when absent.
2. **CI lane (D2).** A scheduled / `eval`-label workflow runs the binary tier,
   fails on any `verdict_matches_expected` drift, and uploads `results/`. Network
   to the package index is the only external dependency.
3. **Corpus growth.** Extend the manifest to new ecosystems (Rust `cdylib`, Go
   `cgo`, Qt, Boost) and platforms (win-64 / osx-64 — `condafetch.py` already
   parameterizes `subdir`).

## Acceptance

- `runner.py --tier source` emits an L3/L4/L5 table in `REPORT.md` for ≥3 libs.
- A scheduled CI lane is green and turns red on an injected verdict drift.
- Manifest carries ≥1 non-conda-C ecosystem and ≥1 non-Linux platform.

## Status

`partial` — binary tier validated (22/22); source tier + CI lane planned (this gap).
