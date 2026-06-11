# validation/ — real-world validation runs

Evidence-based validation of abicheck against real upstream C/C++ shared
libraries (not synthetic fixtures), used to drive planning and improvement.

- `REPORT.md` — latest validation report (start here)
- `DESIGN_ANALYSIS.md` — code-level root cause + architectural fix per false
  positive. FP-1/FP-2 are fixed in `abicheck/model.py` + `abicheck/diff_types.py`;
  FP-3/FP-4 are guarded by strict-xfail regression tests in
  `tests/test_real_world_false_positives.py`.
- `data/manifest.json` — the curated version-pair matrix (exact upstream files)
- `data/results.json` — raw per-`.so` comparison results
- `data/false_positive_evidence.json` — false-positive exemplars
- `suppress_internal.yaml` — internal-namespace suppression used in the report
- `scripts/run_matrix.py` — reproducible harness

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

### End-to-end loop (`run_tracker_parity.py`)

`run_tracker_parity.py` closes the loop: given a harvested oracle, it resolves
each version pair to a **conda-forge** package (via the anaconda.org API),
downloads + extracts the shared objects, runs `abicheck compare`, and scores the
verdict against the tracker — no manual results file needed. Pairs whose
versions aren't on conda-forge are skipped (left UNCOMPARABLE); binaries are
fetched on demand and never committed. Reports land in
`data/tracker_parity/<lib>.json` (gitignored).

```bash
python validation/scripts/fetch_tracker_oracle.py libxml2     # harvest first
python validation/scripts/run_tracker_parity.py libxml2 --max-pairs 4
#   libxml2_2.9.3_to_2.9.4: abicheck=COMPATIBLE oracle=COMPATIBLE (libxml2)
#   libxml2_2.9.7_to_2.9.8: abicheck=BREAKING   oracle=BREAKING   (libxml2)
#   [libxml2] ran 4 pairs | comparable=4 agreement=100.0% match=4 stricter=0 weaker=0
```

`--pkg` overrides the conda package name when it differs from the tracker slug;
`--subdir` selects the conda platform (default `linux-64`). Per pair, the
most-breaking verdict across shared objects is taken (conservative). `.tar.bz2`
packages extract via the stdlib; `.conda` packages need a system `tar --zstd`.

Status semantics from `--compare`:

| Status | Meaning |
|--------|---------|
| `MATCH` | abicheck and the tracker agree |
| `ABICHECK_STRICTER` | abicheck flags BREAKING where the tracker says COMPATIBLE — often legitimate (ABICC has documented blind spots), worth spot-checking |
| `ABICHECK_WEAKER` | abicheck says COMPATIBLE where the tracker found a break — a likely **false negative**, the high-value signal to investigate |
| `UNCOMPARABLE` | no clear verdict on one side, or no abicheck result for the pair (excluded from the agreement rate) |

Parsing is pure and unit-tested offline against a synthetic timeline fixture in
`tests/test_tracker_oracle.py` (CI never hits the network).
