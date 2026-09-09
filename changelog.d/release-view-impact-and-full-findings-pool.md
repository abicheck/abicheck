<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`compare --view impact` and `--view show=compatible`/`show=added` now
  work on a directory/package input** — a directory/package `compare` used
  to silently rewrite `--view impact` to `full` (dropping the requested
  impact-summary table) and built its per-library findings/`--view show=`
  display pool from only the categories that gate the exit code
  (breaking/api_break/risk, or the severity scheme's own blocking
  categories), so a compatible finding like a `func_added` addition could
  never appear — even though the identical single-pair `compare` on the
  same library pair displays it. `--view impact` now computes and renders a
  real per-library impact-summary table (JSON `impact_table`, a Markdown
  "Impact" section), respecting `--view show=...`/`--write`'s existing
  full-vs-filtered contract; the findings/`--view show=` display pool is
  now built from the library's full diff (every category), while the
  narrower gate-bucket subset stays reserved for the exit-code computation.
