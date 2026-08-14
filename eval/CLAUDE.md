# CLAUDE.md — `eval/`

Reproducible **field-evaluation suite**: benchmarks abicheck against real
conda-forge libraries. Runnable harness, *not* documentation — `runner.py` is
exercised by `.github/workflows/eval-suite.yml`. Don't move this tree under
`docs/`; the workflow references it by path and the `.py` files are code.

## Module map

| Path | Role |
|------|------|
| `manifest.yaml` | **Source of truth** — curated libraries, version pairs, expected verdicts, `.so` stems, optional source repo/tags. Edit here; everything else is generated. |
| `runner.py` | Fetch → `abicheck dump`/`compare` → schema'd `results/` + generated `REPORT.md`. Flags any library whose verdict drifts from its manifest `expect` (`--fail-on-drift`), so it doubles as a real-world regression guard for the binary tier. The source tier's own gate, `--fail-on-empty-source` (`source_tier_broken()`), fails only when the *whole* L3/L4/L5 tier is systemically broken — zero libraries scanned, or every "successful" scan captured zero real L3 evidence — while still tolerating one library's own build/network failure; see `docs/contribute/performance.md` "Coverage gaps this workflow does not close" for the incident (a retired `--depth full` value) this closes. |
| `condafetch.py` | conda-forge fetch/extract helper (no `conda` needed). |
| `scaling.py` | `ABICHECK_L4_JOBS` parallel-L4 scaling sweep on one real tree, behind `SCALING.md`. |
| `scan_level_scaling.py` | Scan-*level* scalability sweep (`--depth binary…source`, plus the `--source-method s4` graph rung and a seeded/unseeded `source` split — no separate `full` rung; it collapsed into `source` when `--depth full` was retired from the public CLI, ADR-043 D2) over a **self-contained synthetic** corpus of tunable complexity (TU count / template depth). No network/repo; gated on a C++ compiler + `clang++`. Records wall + peak child RSS per (size, level); surfaces where a level goes super-linear (see `docs/contribute/performance.md` § "Scan-level scalability sweep"). |
| `results/latest.json` | Latest schema'd results (`result_schema` 1) — committed. |
| `REPORT.md`, `SCALING.md` | **Generated — do not hand-edit.** Rebuild via `runner.py --report-only`. |
| `FINDINGS.md`, `FOLLOWUPS.md` | Human narrative: qualitative problem log (P01–P21) + follow-up plan. Hand-edited. |

## How to run

```bash
python eval/runner.py                  # scan all → results/<utc>.json + latest.json + REPORT.md
python eval/runner.py --only zlib,icu  # subset
python eval/runner.py --report-only    # rebuild REPORT.md from results/latest.json
```

Needs network (`conda.anaconda.org`) + `zstd` on PATH for `.conda` extraction.
Raw downloads cache to a gitignored dir (`$ABICHECK_EVAL_CACHE`, default
`/tmp/abicheck-eval`); only `results/latest.json` and the generated `REPORT.md`
are committed.

## Conventions

- `manifest.yaml` is the only place to add/change a library — never hand-edit
  the generated `REPORT.md`/`SCALING.md` or `results/latest.json`.
- `from __future__ import annotations`; runner deps are stdlib + `pyyaml`.
