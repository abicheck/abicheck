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

- **A pre-existing cross-source hygiene finding is no longer reported as a
  change the pull request made.** ADR-068 D3 already re-runs each one-sided
  hygiene check against the baseline and stamps every finding
  ``introduced``/``resolved``/``persistent``/``not_evaluated``, and the JSON
  report has always carried the value — the PR comment never read it. A
  self-compare of one PVXS snapshot against itself (byte-identical operands,
  verdict ``NO_CHANGE``) reported 339 changes, 336 of them
  ``exported_not_public`` on C++ template-instantiation guard variables that
  both sides carry identically, and posted all of them on every run under the
  default ``--on=changes``. ``persistent``/``resolved`` findings now go to a
  background bucket that is summarised in one line, excluded from the
  breaking/needs-review/safe counts, from the "What changed" rollup and from
  ``should_post``; ``not_evaluated`` goes to the analysis-incomplete bucket,
  since the comparison layer explicitly declined to say whether it was new.
  The renderer reclassifies nothing — it reads the state the comparison
  established (ADR-072 D1).

- **A value handed from Python to either Action's shell keeps exactly its own
  bytes.** Both publishers read values out of JSON documents through inline
  `python - <<PYEOF` heredocs whose `print` goes through a `TextIOWrapper`,
  which rewrites `\n` to the host's `os.linesep` — so on a Windows runner
  every record arrived with a trailing `\r` that `read -r` does not strip.
  `[[ "$PLAN_ACTION" == "skip" ]]` was false for a plan whose action was
  exactly `skip`, and `artifact_id`/`pr_number`/`comment_id` carried a stray
  byte into the GitHub API paths they are interpolated into. All five
  emitters now route through one `emit-fields` command that writes through
  the binary layer, refuses a value containing a line break (the framing is
  positional, so one would forge a record and shift every later field), and
  always emits one record per requested field so an absent value cannot
  silently shorten the read.

- **Review round fixes on the above.** `on: never` no longer writes to the
  pull request at all — it short-circuits before the report is read, so the
  "resolved" notice it used to PATCH over an existing comment was an
  all-clear the run never established. The sticky comment's ordering guard
  now records the **producing** run (`source-run-id`/`source-run-attempt`)
  rather than the publisher's own, which in a `workflow_run` split ordered
  by trigger time and so let a re-run of an older commit overwrite a newer
  result. The job summary is bounded from the full render instead of the
  already-cut comment body, so its larger budget is real. A
  `not_evaluated` finding no longer appears in the "What changed" rollup
  while being excluded from every bucket. An aggregate leg whose comparison
  never ran no longer renders a compatibility category in its row, and no
  longer counts as evidence that some comparison completed. An artifact
  entry stating no owning run is refused like one stating the wrong owner.
  `allowed-conclusions: ''` now reaches its documented "any conclusion"
  meaning, the run's artifact listing is paginated, and an abbreviated
  `tested-sha` is refused up front instead of failing verification later.

- **A refused artifact leaves the destination empty.** `inspect_archive`
  validated sizes, entry counts and entry types up front, but each entry's
  *path* was resolved only inside the write loop — so an archive whose
  second entry escaped had its first entry written before the refusal,
  leaving a partial, attacker-chosen tree and making the documented "the
  whole central directory is validated before a byte is written" guarantee
  false for exactly the refusals that matter most. Nothing ever escaped the
  destination; what changed is that a failed extraction no longer leaves
  content behind for whatever reads that directory next.
