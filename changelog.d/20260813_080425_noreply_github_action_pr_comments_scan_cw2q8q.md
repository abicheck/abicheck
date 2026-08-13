<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **The GitHub Action's sticky PR comment now supports `mode: scan`** (single
  artifact, not `new-library-set`) — `pr-comment`/`pr-comment-on`/
  `pr-comment-mode`/`pr-comment-detail` all work the same way they already do
  for `compare`, rendering `scan`'s own verdict, breaking/needs-review
  findings, a green "Public API additions" section, and a short risk/coverage
  summary line, without a second `compare` run. `scan --against`'s JSON gained
  an always-on `additions` array (schema `1.13`, `cli_scan_baseline.py`) so
  the comment can render new public-API surface the same way `compare`'s own
  report already does; `NOT_COMPARABLE` (a scope/profile mismatch) is now its
  own Action verdict, and header counts stay exact even when a large diff's
  `findings`/`additions` were truncated below the report cap.
- **`scan` gained `--secondary-format`/`--secondary-output`**, mirroring
  `compare`'s own flags: render a second output format (typically JSON)
  from the same scan run without re-running it. The GitHub Action's own
  PR-comment renderer uses this to avoid a second, potentially
  `--depth build/source`-expensive scan when the primary step output stays
  the documented default `--format text`, and now also reuses the
  already-materialized JSON for `--format json` with no `--output` (the
  CLI's stdout mode) instead of falling through to a rerun there too.
  A compatible finding promoted to blocking by severity policy (e.g.
  `--severity-addition error`) no longer renders in both the Breaking
  and green "Public API additions" sections at once, and the exact
  promoted/addition totals now come from the severity gate's own
  per-category counts rather than a possibly report-capped finding list.
  A scan whose cross-check results alone produced `API_BREAK` (a promoted
  `--crosscheck KEY=error` on any run, or any `API_BREAK_KINDS` finding on
  an audit-only run) now surfaces those findings in the comment — for an
  audit-only run (no `--against`) *and* for a baseline comparison alike,
  since cross-check is a separate evidence axis from the diff itself —
  instead of silently rendering (or deleting) a green "no changes" comment
  next to a red `fail-on-api-break` check; an un-promoted cross-check on a
  baseline comparison stays advisory-only, matching `scan`'s own exit-code
  contract. The summary count for these now reflects the exact summed
  occurrence count per check, not one row per distinct kind. An
  `addition: error` severity promotion (which raises every addition-shaped
  finding to blocking, never a subset) now empties the green "Public API
  additions" section wholesale instead of excluding only the promoted
  entries a truncated `diff.findings` list happened to still carry.
