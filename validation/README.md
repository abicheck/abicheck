# validation/ — real-world validation runs

Evidence-based validation of abicheck against real upstream C/C++ shared
libraries (not synthetic fixtures), used to drive planning and improvement.

- `realworld-tracker-parity-2026-06.md` — **latest** run: abicheck scored live
  against the ABICC abi-laboratory oracle across 60 libraries / 185 comparable
  pairs (94.1 % agreement, 0 confirmed defects). Start here for the parity results.
- `REPORT.md` — earlier curated-matrix validation report (false-positive catalog)
- `DESIGN_ANALYSIS.md` — code-level root cause + architectural fix per false
  positive. FP-1/FP-2 are fixed in `abicheck/model.py` + `abicheck/diff_types.py`;
  FP-3/FP-4 are guarded by strict-xfail regression tests in
  `tests/test_real_world_false_positives.py`.
- `data/manifest.json` — the curated version-pair matrix (exact upstream files)
- `data/results.json` — raw per-`.so` comparison results (`run_matrix.v2`
  records include mode, platform, source layers, evidence asymmetry, runtime,
  expected verdict, actual verdict, normalized compatibility-axis verdicts, and
  comparison status)
- `data/results.meta.json` — run-level metadata emitted by `scripts/run_matrix.py`
- `data/component_suites.json` — component-suite run metadata emitted by
  `scripts/run_component_suites.py`
- `data/remeasurement_summary.json` — combined summary emitted by
  `scripts/summarize_remeasurement.py`
- `data/false_positive_evidence.json` — false-positive exemplars
- `suppress_internal.yaml` — internal-namespace suppression used in the report
- `scripts/run_matrix.py` — reproducible harness
- `scripts/run_component_suites.py` — pytest suite harness for source-family
  component remeasurement artifacts
- `scripts/summarize_remeasurement.py` — combines example, component-suite, and
  real-world artifacts into the release-gate summary

For `run_matrix.v2`, non-zero `abicheck compare` exit codes are not failures by
themselves: expected `BREAKING` / `API_BREAK` outcomes legitimately exit
non-zero. Release-gate summaries score the normalized expected vs actual verdict
instead, with `ABICHECK_STRICTER`, `ABICHECK_WEAKER`, and missing-verdict run
errors counted as blocking real-world remeasurement failures.

Binaries are intentionally not committed; reproduce them from `data/manifest.json`
(conda-forge, `https://conda.anaconda.org/conda-forge/linux-64/<file>`).

## abi-laboratory.pro parity oracle

`scripts/fetch_tracker_oracle.py` turns the published verdicts on
[abi-laboratory.pro/tracker](https://abi-laboratory.pro/index.php?view=tracker)
into a labelled ground-truth oracle. The tracker is run by the author of
`abi-compliance-checker` (the tool abicheck replaces) and reports a
backward-compatibility verdict for every consecutive release of ~800 libraries —
an independent reference label for real-world ABI outcomes.

It only reads the public timeline page (no ABI dumps are downloaded or
redistributed), so the licensing surface stays at "reading a web page". Harvested
oracles land in `data/tracker_oracle/<lib>.json` and are **gitignored**
(regenerable, third-party-derived).

```bash
# 1. Harvest oracles (expected verdicts) for one or more libraries
python validation/scripts/fetch_tracker_oracle.py zstd libxml2 openssl
#   -> data/tracker_oracle/zstd.json : consecutive (old -> new) pairs, each
#      labelled COMPATIBLE / BREAKING from the tracker's backward-compat figure.

# 2. Run abicheck on binaries for those versions (e.g. via run_matrix.py),
#    producing a results list/object that maps pair ids to abicheck verdicts.

# 3. Score abicheck against the oracle
python validation/scripts/fetch_tracker_oracle.py zstd --compare results.json
#   [zstd] comparable=37 agreement=97.3% match=36 stricter=0 weaker=1 ...
#     WEAKER (likely FN): zstd_0.7.3_to_0.7.4 oracle=BREAKING abicheck=COMPATIBLE
```

### Unified end-to-end loop (`validate.py`)

`validate.py` is the single entrypoint that scores `abicheck` against **any**
expectation source through one shared engine (`conda_harness.py`): it resolves
each version pair to a **conda-forge** package (via the anaconda.org API),
downloads + extracts the shared objects, runs `abicheck compare`, and scores the
verdict against the source — no manual results file needed. The only difference
between "validate against curated examples" and "validate against an automated
oracle" is one flag:

```bash
# Automated oracle (harvest first; whole version histories)
python validation/scripts/fetch_tracker_oracle.py libxml2
python validation/scripts/validate.py --source tracker --lib libxml2 --max-pairs 4
#   libxml2_2.9.3_to_2.9.4: abicheck=COMPATIBLE expected=COMPATIBLE (libxml2)
#   [libxml2] ran 4 pairs | comparable=4 agreement=100.0% match=4 stricter=0 weaker=0

# Curated, human-labelled manifest (data/manifest.json) — now fetches/extracts too
python validation/scripts/validate.py --source manifest            # all entries
python validation/scripts/validate.py --source manifest --lib oneTBB
```

`run_tracker_parity.py <lib>` remains as a thin alias for
`validate.py --source tracker --lib <lib>`.

Sources (pluggable — add one adapter to cover a new ground truth):

| `--source` | Expected verdict from | Notes |
|-----------|----------------------|-------|
| `tracker` | `data/tracker_oracle/<lib>.json` | automated abi-laboratory harvest, broad coverage |
| `manifest` | `data/manifest.json` `expectation` | hand-curated edge cases with notes; pins exact builds |

Pairs whose versions aren't on conda-forge are skipped (left UNCOMPARABLE);
binaries are fetched on demand and never committed. Reports land in
`data/tracker_parity/<label>.json` (gitignored).

`--pkg` overrides the conda package name when it differs from the slug;
`--subdir` selects the conda platform (default `linux-64`). Per pair, the
most-breaking verdict across shared objects is taken (conservative). `.tar.bz2`
packages extract via the stdlib; `.conda` packages prefer a pure-Python zstd
backend — `pip install zstandard` (or Python 3.14+'s stdlib `compression.zstd`)
— and fall back to a system `tar --zstd` if neither is importable. Without any
backend, `.conda` pairs are skipped (logged), not fatal.

Status semantics from `--compare`:

| Status | Meaning |
|--------|---------|
| `MATCH` | abicheck and the tracker agree |
| `ABICHECK_STRICTER` | abicheck flags BREAKING where the tracker says COMPATIBLE — often legitimate (ABICC has documented blind spots), worth spot-checking |
| `ABICHECK_WEAKER` | abicheck says COMPATIBLE where the tracker found a break — a likely **false negative**, the high-value signal to investigate |
| `UNCOMPARABLE` | no clear verdict on one side, or no abicheck result for the pair (excluded from the agreement rate) |

#### Excluded divergences (not scored as disagreements)

`validate.py` further sets aside two classes of divergence that are *expected*
artifacts of the comparison setup, not abicheck errors. Both are excluded from
the agreement rate (counted as `UNCOMPARABLE`) and reported on their own lines:

| Bucket | When | Why it isn't a disagreement |
|--------|------|------------------------------|
| `evidence_limited` | oracle break is type-level only (`removed_symbols == 0`) **and** the binary lacks usable DWARF on **both** sides of the compared shared object | abicheck can only see the symbol table (or only one side's layout), so it physically cannot observe the type change ABICC saw in its debug build — a non-breaking verdict is not a false negative. Both sides are required because diffing layouts needs debug info for the old *and* new build |
| `scope_divergent` | oracle says COMPATIBLE with no public symbol changed (`removed_symbols == 0`, 100% backward compat), abicheck says BREAKING, **and every** breaking finding is a symbol/toolchain-scope *hard fact* | A header-scoped oracle (ABICC / abi-laboratory) only counts the public-header surface; binary-only abicheck deliberately treats every exported symbol as ABI. So abicheck correctly flags a *real* binary change the oracle ignores — an exported-but-internal symbol removed (`_TIFF*`, `_nettle_*`, `__gmpn_*` CPU variants), an internal data table resized, a libstdc++ dual-ABI `std::string` shift from a cross-toolchain rebuild. Not a false positive |

The `scope_divergent` gate is deliberately conservative on three axes so it can
never hide a real problem:

1. **Oracle-corroborated** — gated on the oracle's *own* public-surface counts,
   so a divergence is never excused on a pair where the oracle actually saw a
   public symbol change.
2. **Hard facts only** — it covers symbol *removals*, data-symbol *size*
   changes, and *ABI-tag* changes: things abicheck reads directly from the
   symbol table / mangled names and cannot get wrong. It deliberately
   **excludes `func_params_changed`** — a signature change is *inferred* from
   DWARF on a still-present symbol, so it could be a genuine abicheck false
   positive on a public function and stays a scored disagreement.
3. **No type-level breaks** — type-level layout breaks always stay scored as
   genuine disagreements.

See `conda_harness._SCOPE_SENSITIVE_BREAKING_KINDS` and
`validate._is_scope_divergence`.

Parsing is pure and unit-tested offline against a synthetic timeline fixture in
`tests/test_tracker_oracle.py` (CI never hits the network); the exclusion gates
are unit-tested in `tests/test_conda_harness.py` and `tests/test_validate.py`.
