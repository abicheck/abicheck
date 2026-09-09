<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`docs/use/scan-levels.md`'s `--dry-run` section overstated `compare`'s
  evidence-contract preview.** It claimed an unsatisfiable requested depth
  exits nonzero the same way the real run would; verified live that
  `compare --dry-run --depth source` with no build/source evidence still
  exits 0 and reports `0 TU(s)` for L3/L4/L5 — only `scan` has a fail-loud
  evidence-contract floor. Qualified the claim as `scan`-only.
- **Two more hardcoded cross-source-check counts** (`docs/integration/
  scenarios/single-build-audit.md`'s "eleven ... and five more") replaced
  with a reference to `CROSS_SOURCE_EVOLUTION_CHECKS`, the actual fact
  owner, matching the fix already applied elsewhere in this PR.
- **`validation/scripts/fp_depth_demo.py` labeled its internal, synthetic
  fifth evidence column as `full`, alongside the four real `--depth`
  values** — `compare --depth full`/`scan --depth full` both reject with
  exit 64; there is no such depth. Renamed the table-header label (only)
  to `source+L5` and documented the column as an internal projection, not
  a real `--depth` value.
