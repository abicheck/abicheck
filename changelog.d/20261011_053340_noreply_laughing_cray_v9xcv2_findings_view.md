### Fixed

- **HTML and Markdown now agree with JSON on added/removed/changed** — HTML
  bucketed findings by two hand-maintained kind subsets and titled its
  summary rows by kind-name prefix, and Markdown's `--show-impact` table
  counted removals by a `*_removed` name suffix. Both disagreed with the
  registry-declared `operation`/`entity` the JSON report and the PR comment
  use. For example, `func_deleted` and `needed_added` were listed as "Changed"
  in HTML, and `func_noexcept_removed` was counted as a Markdown removal.
  `ReportFinding` now carries the effective `bucket`, `entity` and
  `reclassified_by`, and both renderers read them. The HTML summary table's
  rows now use the PR comment's entity labels (`Binary/container` replaces
  `ELF / DWARF`, and `Unclassified` replaces `Other`). The
  `report_classifications.REMOVED_KINDS`/`ADDED_KINDS` sets were removed.
