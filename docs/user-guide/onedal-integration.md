# Integrating abicheck into oneDAL

A ready-to-copy integration for [uxlfoundation/oneDAL](https://github.com/uxlfoundation/oneDAL),
baselined against the **2026.0.0** release, lives in the repo at
`contrib/integrations/onedal/`
([browse on GitHub](https://github.com/abicheck/abicheck/tree/main/contrib/integrations/onedal)).
This page is the map; that package is the source of truth (workflows, config,
and a full validation write-up in its `README.md`).

## The three layers

| Layer | What it does | Build cost | When |
|-------|--------------|-----------|------|
| **PR source scan** | `mode: scan` over the PR's changed public headers, projected against the committed 2026.0.0 snapshot | **none** (buildless) | every PR, advisory |
| **Build + collect facts** | Builds oneDAL with the [compiler plugin](build-evidence-setup.md) loaded during the compile; uploads `__release` + `abicheck_inputs/` | oneDAL build | PR + dispatch |
| **Analysis (via Action)** | Downloads the build + facts and runs the Action: `dump` → `merge` facts → `compare` vs baseline; SARIF + PR comment | none (reuses artifacts) | after the build |
| **Nightly binary compare** | Builds current oneDAL (icx+MKL, `--debug symbols`) and `compare`s each `.so` vs its baseline snapshot; SARIF | full build | nightly, advisory |

The baseline is a set of per-library **`.abi.json` snapshots** built once from tag
`2026.0.0` (symbol + DWARF, because oneDAL builds with `--debug symbols`) and
committed under `.abicheck/baseline-2026.0.0/`. Because the PR scan reasons about
the *source* delta against that snapshot, it needs **no oneDAL build** — the key
to per-PR feasibility on a library whose full build needs Intel MKL/TBB.

## Why a snapshot baseline (not headers-on-both-sides)

The most important correctness rule, validated for this integration: never parse
an old binary and a new binary with the **same** (current) header tree — a
removed public method vanishes from the new header, so header-scoped diffing
reports `COMPATIBLE`. A `.abi.json` snapshot bakes the 2026.0.0 headers/symbols
in, so `compare`/`scan` re-parse only the new side and removals surface correctly
(`BREAKING`, exit 4). See [Baseline management](baseline-management.md) and the
package's `README.md` for the full findings list (including two action/config
bugs found and fixed while wiring this up).

## Get started

1. Copy `contrib/integrations/onedal/config/onedal.abicheck.yml` → oneDAL
   `.abicheck.yml`, and `onedal.suppress.yml` → oneDAL root.
2. Copy `contrib/integrations/onedal/workflows/*.yml` → oneDAL
   `.github/workflows/`.
3. Run the **baseline** workflow once (it opens a PR with the snapshots); merge it.
4. Open a PR touching `cpp/daal/include/**` — the source scan writes an ABI
   report to the run's job summary (scan mode reports via the summary + artifact,
   not a PR comment).

Full step-by-step, the oneDAL-specific caveats (SYCL headers, icx, internal
namespaces), and the path to enforcement are in
[`contrib/integrations/onedal/README.md`](https://github.com/abicheck/abicheck/blob/main/contrib/integrations/onedal/README.md).

## Related

- [GitHub Action](github-action.md) · [Source scans](github-action-source-scans.md)
- [`.abicheck.yml` config](../reference/config-file.md) · [Suppressions](suppressions.md)
- [Build evidence & the Clang plugin](build-evidence-setup.md)
