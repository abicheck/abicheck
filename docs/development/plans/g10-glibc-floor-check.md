# G10 — manylinux glibc-floor (platform-baseline) check

**Registry:** `UC-TC-glibc-floor` (`planned`)
**Effort:** S · **Risk:** low

## Problem

A manylinux wheel's tag (`manylinux_2_27`, `manylinux_2_28`, …) is a *promise*
about the **maximum glibc symbol version** its binaries may require. abicheck
already captures `elf.versions_required` (e.g. `GLIBC_2.x`) per binary, but no
check compares the required floor against a declared platform baseline. The
result is the classic "works on my box, `ImportError`/`GLIBC_2.x not found` on
the user's older system" failure going undetected.

## Goal & acceptance criteria

- [ ] A `--glibc-floor 2.27` option (or derivation from a wheel's manylinux tag)
      against which the max `GLIBC_2.x` in `versions_required` is checked.
- [ ] Exceeding the floor emits a deployment-`RISK` finding ("minimum glibc
      requirement raised / exceeds declared baseline") that reaches the verdict
      and JSON/SARIF output.
- [ ] Within-floor binaries stay clean.

## Goal note on taxonomy

This is a new deployment-`RISK` `ChangeKind` (e.g. `platform_baseline_floor_raised`)
added per the four-step procedure in the root `CLAUDE.md`; it composes with the
existing `diff_versioning.py` symbol-version reasoning rather than replacing it.

## Files & surfaces

- `abicheck/diff_versioning.py` (floor comparison), `abicheck/checker_policy.py`
  (new kind + partition), the relevant CLI module for the flag, and the wheel
  tag parser in `abicheck/package.py` for auto-derivation.

## Tests

- Unit: a binary requiring `GLIBC_2.34` checked against floor `2.27` → RISK;
  against `2.38` → clean.
- An example pair under `examples/` with `ground_truth.json` entry.

## Out of scope

Non-glibc platform floors (musl, Windows API set, macOS deployment target) —
follow-ups once the mechanism exists.
