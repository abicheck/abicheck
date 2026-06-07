# G2 — Build-config matrix into `compare`, and bundle completion

**Registry:** `UC-WF-probe-matrix` (`complete`), `UC-WF-bundle` (`complete`), `UC-TC-cxx-standard-floor` (`complete`)
**Effort:** M · **Risk:** medium (verdict-composition semantics)

## Problem

This plan is now complete. It originally tracked two capabilities that existed
but were not reachable from the mainline gate:

1. **Build-config matrix** — `abicheck/probe_harness.py` + `diff_build_config.py`
   detect `API_DEPENDS_ON_CONSUMER_ENV`, `CXX_STANDARD_FLOOR_RAISED`, and
   `BEHAVIOURAL_DEFAULT_CHANGED`, but only via the separate `abicheck probe`
   command. They now feed `compare`/`compare-release` via `--probe-matrix-old/new`.
2. **Bundle analysis** — `abicheck/bundle.py` detects cross-DSO breakage, but
   `compare-release` wiring is now complete for explicit cohorts, including case84.

## Goal & acceptance criteria

- [x] `compare`/`compare-release` merge matrix findings into the verdict
      (worst-of), with the matrix ChangeKinds appearing in the report.
      **Shipped as `--probe-matrix-old` / `--probe-matrix-new`** (pre-built
      matrix snapshots from `abicheck probe run`) rather than an inline
      `--probe-spec`: running a matrix needs compilers, so it stays a separate
      `probe run` step that feeds the comparison, keeping the compare commands
      hermetic. On `compare` the findings join the change list (JSON + SARIF);
      on `compare-release` they are release-global, so they run through the
      same `checker.compare` pipeline (over empty snapshots) — `--suppress`
      rules and `--policy-file` overrides apply identically to both commands —
      then fold into the worst-of release verdict and surface as a
      `matrix_findings` section in the JSON/markdown summary and as a
      dedicated testsuite in JUnit output. Verified end-to-end for both
      commands in `tests/test_probe_examples.py` and at the unit level in
      `tests/test_cli_split_modules.py`.
- [x] Case 98 (`CXX_STANDARD_FLOOR_RAISED`) reaches its intended verdict through
      the mainline command (JSON + SARIF), not only `probe compare`. Case 97
      (`API_DEPENDS_ON_CONSUMER_ENV`) now also fires end-to-end: the harness gap
      is closed — `parse_elf_metadata` falls back to `.symtab` when a relocatable
      probe `.o` has no `.dynsym`, so the object's defined global symbols are
      captured and the detector fires over the real compiled surface, reaching
      the mainline `compare` output (`tests/test_probe_examples.py`,
      `tests/test_elf_object_surface.py`).
- [x] `compare-release` emits `bundle_soname_skew`; case84 lost `skip: true`
      and is validated end-to-end (`tests/test_bundle.py::TestCompareReleaseBundleE2E`).
      The check is **opt-in** via `--bundle-cohort PREFIX` (repeatable): cohorts
      are declared, never inferred from filenames, so an ordinary release that
      bumps one independent library while a sibling lags is not a false positive.
- [x] Two additional self-contained probe specs under `examples/probes/`
      (`feature_macro.yaml`, `cxx_standard.yaml`) with an end-to-end test
      (stock `cc`/`c++`, no external toolchain).

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
