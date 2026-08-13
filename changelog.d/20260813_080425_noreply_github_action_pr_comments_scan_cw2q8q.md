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
  an always-on `additions` array and its complement `quality` array (schema
  `1.13`, `cli_scan_baseline.py`) so the comment can itemize every compatible
  finding the same way `compare`'s own report already does via its full
  `changes` list; `NOT_COMPARABLE` (a scope/profile mismatch) is now its own
  Action verdict — and unconditionally fails the step, since no `fail-on-*`
  input governs a run in which no comparison happened — and header counts
  stay exact even when a large diff's `findings`/`additions`/`quality` were
  truncated below the report cap.
- **`scan` gained `--secondary-format`/`--secondary-output`**, mirroring
  `compare`'s own flags: render a second output format (typically JSON)
  from the same scan run without re-running it. The GitHub Action's own
  PR-comment renderer uses this to avoid a second, potentially
  `--depth build/source`-expensive scan when the primary step output stays
  the documented default `--format text`, and now also reuses the
  already-materialized JSON for `--format json` with no `--output` (the
  CLI's stdout mode) instead of falling through to a rerun there too, and
  skips entirely (rather than rerunning) after a `BUDGET_OVERFLOW`, which
  a rerun could only reproduce. `compare` and `scan` now share one
  `--secondary-format`/`--secondary-output` decorator and coherence
  validator (`cli_secondary_output.py`) instead of two independently
  drifting inline copies.
- **`scan --against`'s JSON gained `diff.policy`**, the resolved
  compatibility policy name, so the comment footer reports the policy that
  actually classified the run instead of always the `strict_abi` fallback.

### Fixed

- **The scan sticky comment's compatible-finding accounting was wrong in
  several ways**, all now corrected: a compatible finding promoted to
  blocking by severity policy (e.g. `--severity-addition error`) no longer
  renders in both the Breaking and green "Public API additions" sections at
  once, and the exact promoted/addition totals now come from the severity
  gate's own per-category counts rather than a possibly report-capped
  finding list; the safe-finding count is derived from the diff's full
  `compatible` scalar (with a matching `diff.quality` itemization) rather
  than only the addition-shaped subset, so a scan whose only findings are
  compatible-but-non-addition (a quality-category change, or a
  policy-demoted removal) no longer reports zero changes and gets its
  sticky comment silently skipped.
- **Cross-check findings** (a separate evidence axis from the baseline diff)
  now surface in the comment for both an audit-only run and a baseline
  comparison, instead of silently rendering (or deleting) a green "no
  changes" comment next to a red `fail-on-api-break` check; an un-promoted
  cross-check on a baseline comparison stays advisory-only, matching
  `scan`'s own exit-code contract, and the summary count reflects the exact
  summed occurrence count per check rather than one row per distinct kind.
  A promoted RISK-tier cross-check now renders a "Compatibility risk blocks
  this PR" headline instead of misreporting a real API break.
- **A crafted scanned-artifact filename could inject Markdown into the
  sticky comment**: the comment header now escapes the subject (an
  untrusted filename passed through the Action's `--subject`) before
  rendering it, closing an injection vector via a backtick/newline in the
  artifact name.
- **The Action's PR-comment renderer now uses the resolved Python
  interpreter** (`$_PY_BIN`, already used by every other Python invocation
  in `run.sh`) instead of a hard-coded `python3`, which could silently fail
  on a Windows Git Bash runner where only `python`/`python.exe` is on PATH.
- **The temporary JSON report the Action writes for the sticky comment is
  now removed by the script's cleanup trap**, alongside the other temp
  files it already cleaned up — previously leaked on every non-JSON,
  single-artifact `compare`/`scan` run, accumulating indefinitely on a
  persistent self-hosted runner.
