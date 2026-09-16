### Added

- **Report-only publication Actions for fork pull requests (ADR-073).**
  `actions/report` publishes an already-produced canonical JSON report
  (`compare`, a directory/package release report, a `compare --no-baseline`
  audit, or an `aggregate` document) to a pull request as a sticky comment
  and/or a job summary, and `actions/verify-source-run` selects and unpacks
  the producer run a trusted `workflow_run` publisher reports on. Neither
  analyses anything — they install `abicheck` and nothing else, so a fork
  PR's results can be published without any privileged job ever running
  contributor-controlled build logic. The sticky comment carries a
  run-id/attempt ordering guard, so a late-finishing older producer run
  reports `skipped-reason=stale` instead of overwriting a newer result, and
  a result that no longer holds is cleared in place rather than left
  standing. A failed publication fails the step with `posted=false` and is
  never reported as a clean compatibility result; a non-clean verdict never
  fails the reporting Action. See
  [Reporting on fork pull requests](https://abicheck.github.io/abicheck/use/fork-pr-reporting/).

### Fixed

- **The PR-comment renderer understands `abicheck aggregate`'s fan-in
  document.** `abicheck.pr_comment.build_model` dispatched on payload keys
  and had no branch for the aggregate shape, so an aggregate report fell
  through to the `compare` adapter, was read for a `changes` array it does
  not carry, and rendered "✅ No ABI changes" — for a run that may have been
  failing on every target in it. It now folds the document through its
  per-target member reports, labels every finding with the target that
  reported it, and reads the document's own outcome vocabulary rather than
  recomputing a verdict. Unavailable targets, `not_comparable`/
  `operational_error` legs, missing required targets, unreadable or
  out-of-directory member reports, and every contract-coverage,
  analysis-assurance and scope-completeness shortfall now render as explicit
  limitations and post under `--on=changes`.
