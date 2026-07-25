# G10 — manylinux glibc-floor (platform-baseline) check

**Registry:** `UC-TC-glibc-floor` (`complete`)
**Effort:** S · **Risk:** low

## Problem

A manylinux wheel's tag (`manylinux_2_27`, `manylinux_2_28`, …) is a *promise*
about the **maximum glibc symbol version** its binaries may require. abicheck
already captures `elf.versions_required` (e.g. `GLIBC_2.x`) per binary, but no
check compares the required floor against a declared platform baseline. The
result is the classic "works on my box, `ImportError`/`GLIBC_2.x not found` on
the user's older system" failure going undetected.

## Goal & acceptance criteria

- [x] A declared floor (`--env-matrix`'s existing `runtime_floors: {GLIBC:
      "2.27"}`, ADR-020b — no new flag) against which the max `GLIBC_2.x` in
      `versions_required` (plus the implied floor from `DT_RELR`, glibc >=
      2.36) is checked, and a wheel-tag-derivation helper
      (`package.parse_manylinux_glibc_floor`) for programmatic use.
- [x] Exceeding the floor emits a deployment-`RISK` finding
      (`platform_baseline_floor_raised`) that reaches the verdict and
      JSON/SARIF output.
- [x] Within-floor binaries stay clean.

## Goal note on taxonomy

This is a new deployment-`RISK` `ChangeKind` (e.g. `platform_baseline_floor_raised`)
added per the four-step procedure in the root `CLAUDE.md`; it composes with the
existing `diff_versioning.py` symbol-version reasoning rather than replacing it.

## Files & surfaces

- `abicheck/diff_versioning.py` (`check_platform_baseline_floor`, floor
  comparison, wired into `checker.compare()` via the existing
  `EnvironmentMatrix.runtime_floors` contract), `abicheck/checker_policy.py` +
  `abicheck/change_registry_coverage.py` (new kind + partition), and the wheel
  tag parser in `abicheck/package.py` for auto-derivation.

## Tests

- Unit: a binary requiring `GLIBC_2.34` checked against floor `2.27` → RISK;
  against `2.38` → clean; a `DT_RELR` binary implies glibc >= 2.36 even absent
  a matching version tag; case-insensitive floor keys. See
  `tests/test_environment_drift.py`.
- CLI end-to-end via `--env-matrix`: `tests/test_environment_drift.py::TestPlatformBaselineFloorCliEndToEnd`.
- No dedicated `examples/` fixture was added (binary fixtures are heavier to
  maintain than the unit + CLI-integration coverage above, which already
  proves the acceptance criteria end-to-end); a future contributor could add
  one for parity with the rest of the catalog.

## Out of scope

Non-glibc platform floors (musl, Windows API set, macOS deployment target) —
follow-ups once the mechanism exists.
