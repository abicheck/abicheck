# G2 — Build-config matrix into `compare`, and bundle completion

**Registry:** `UC-WF-probe-matrix` (`partial`), `UC-WF-bundle` (`partial`), `UC-TC-cxx-standard-floor` (`partial`)
**Effort:** M · **Risk:** medium (verdict-composition semantics)

## Problem

Two capabilities exist but are not reachable from the mainline gate:

1. **Build-config matrix** — `abicheck/probe_harness.py` + `diff_build_config.py`
   detect `API_DEPENDS_ON_CONSUMER_ENV`, `CXX_STANDARD_FLOOR_RAISED`, and
   `BEHAVIOURAL_DEFAULT_CHANGED`, but only via the separate `abicheck probe`
   command. A user running `compare`/`compare-release` never sees them — so
   cases 97/98 come out `NO_CHANGE`/quality on a per-binary diff.
2. **Bundle analysis** — `abicheck/bundle.py` detects cross-DSO breakage, but
   `compare-release` wiring is incomplete (case84 `bundle_soname_skew` is
   `skip: true` in `ground_truth.json`) and the layer is Linux-only.

## Goal & acceptance criteria

- [ ] `compare`/`compare-release` accept `--probe-spec <spec.yaml>`; when given,
      the matrix findings are merged into the change list and folded into the
      verdict (worst-of), with the matrix ChangeKinds appearing in JSON/SARIF.
- [ ] Case 98 (`CXX_STANDARD_FLOOR_RAISED`) and case 97
      (`API_DEPENDS_ON_CONSUMER_ENV`) reach their intended verdict through the
      mainline command with `--probe-spec`, not only through `probe compare`.
- [ ] `compare-release` emits `bundle_soname_skew`; case84 loses `skip: true`
      and is validated against `ground_truth.json`.
- [ ] ≥2 additional probe specs under `examples/probes/` (a feature-macro C
      library; a compiler/standard matrix) with an end-to-end test.

## Design

1. **Matrix-into-compare:** add a `--probe-spec` option to `compare_cmd`
   (`abicheck/cli.py`) and `compare-release`. When present, run
   `run_probe_matrix()` for each side, `diff_matrix()` the pair, and append the
   resulting `Change`s to the `DiffResult` before `compute_verdict`. Verdict
   composition is already worst-of, so no policy change is required; matrix
   kinds are already classified in `change_registry.py`.
2. **Confidence:** when probes are partial, set `DiffResult.confidence=low` and
   add a `coverage_warning` (mirror `probe compare --allow-failures`).
3. **Bundle wiring:** finish the `compare-release` → `bundle.py` path so
   `detect_bundle_soname_skew()` and the other bundle detectors run on the
   per-library cohort; surface bundle findings in the summary report. Remove
   `skip: true` from case84 and add the `gen_bundle.sh` build to CI.

## Files & surfaces

- `abicheck/cli.py`, `abicheck/cli_compare_release.py` (`--probe-spec`, bundle wiring).
- `abicheck/service.py` (`run_compare` accepts an optional matrix).
- `abicheck/bundle.py` (cohort entry point from `compare-release`).
- `examples/probes/*.yaml` (new specs); `examples/case84_bundle_soname_skew/`.

## Tests

- Unit: matrix merge into `DiffResult`; verdict worst-of with a matrix kind.
- `@pytest.mark.integration`: probe build + `compare --probe-spec`; bundle skew
  via `gen_bundle.sh`.
- Update `ground_truth.json` for case84/97/98 and the autodiscovery harness.

## Out of scope

Non-Linux bundle analysis (no DT_NEEDED/`.gnu.version_*` equivalent — track
under G1). Auto-deriving a matrix without a spec.
