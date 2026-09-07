### Added

- **`compare --since` / `compare --changed-path`** — changed-path
  localization moves from `scan` onto `compare` (ADR-068, plan Phase 2c).
  Scoping input only: it narrows the L4/L5 points of interest a
  `--depth source` run replays and produces no finding of its own, following
  ADR-043 D7's rule (changed-path scope when a seed is given, else the
  current library target — never a zero-TU no-op). Both commands now resolve
  the seed through one owner, `workflows/changed_paths.py`.
- **`compare --abi3 VERSION`** — the CPython stable-ABI audit moves onto
  `compare` as *candidate-side enrichment* (ADR-068 D3, plan Phase 2d): the
  check is meaningful only on NEW, so its `python_stable_abi_violation`
  findings ride the same result document marked
  `candidate_side_enrichment` (report schema 3.5) instead of a second
  result, and are never evaluated on OLD. They stay advisory (`RISK`), gated
  only through policy. `--abi3` against a candidate that is not a
  recognisable CPython extension module is an evidence-contract error —
  exit `7`, reusing the `ExitDecision` axis `compare` already had.
- **`.abicheck.yml` `python.abi3_floor`** — the abi3 floor is a stable
  project property (ADR-068 D5), so it is declared in the project config;
  `--abi3` is the per-run override on top of it. `--since`/`--changed-path`
  stay CLI-only, since a PR's diff is genuinely per-run.
