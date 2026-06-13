# abicheck field-evaluation suite

Reproducible benchmark of abicheck against real conda-forge libraries. The
**source of truth** is [`manifest.yaml`](manifest.yaml); everything else is
generated, so the report can never drift from the data.

```bash
pip install pyyaml                      # runner dep
python eval/runner.py                   # scan all → results/<utc>.json + latest.json + REPORT.md
python eval/runner.py --only zlib,icu   # subset
python eval/runner.py --report-only     # rebuild REPORT.md from results/latest.json
```

Needs network (conda.anaconda.org) + `zstd` on PATH (for `.conda` extraction).
Raw downloads/snapshots go to a gitignored cache (`$ABICHECK_EVAL_CACHE`,
default `/tmp/abicheck-eval`); only the schema'd `results/latest.json` and the
generated `REPORT.md` are committed.

## Files
| File | Role |
|---|---|
| `manifest.yaml` | curated libraries, version pairs, **expected verdicts**, `.so` stems, optional source repo/tags |
| `runner.py` | fetch → `abicheck dump`/`compare` → schema'd `results/` + generated `REPORT.md` |
| `condafetch.py` | conda-forge fetch/extract helper (no conda needed) |
| `REPORT.md` | **generated** — do not hand-edit |
| `results/latest.json` | latest schema'd results (`result_schema` 1) |
| `FINDINGS.md` | qualitative problem log (P01–P21) + analysis — the human narrative |

`runner.py` flags any library whose verdict drifts from its manifest `expect`,
so the suite doubles as a real-world regression guard.
