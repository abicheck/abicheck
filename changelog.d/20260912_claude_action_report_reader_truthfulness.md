### Fixed

- **The composite Action no longer reports `COMPATIBLE` for a requested JSON
  report it could not read.** `action/run.sh`'s exit-0 path opened with
  `VERDICT="COMPATIBLE"` and only ever *escalated* from a report it managed to
  parse, so a `format: json` run whose `output-file` was absent, truncated,
  unparseable, not a JSON object, or an empty-object placeholder published "No
  binary ABI break detected" for a comparison nothing had read. Such a run now
  reports the new `verdict: REPORT_UNREADABLE` and fails the step
  unconditionally; no `fail-on-*` input waives it, since none of those is a
  statement that an unread result should be reported as a pass. Scoped to a
  report the caller actually requested: when the primary format is not json,
  the absence of the Action's own internal `--write json=` sidecar still
  publishes `COMPATIBLE`, because exit 0 is genuine evidence that abicheck's
  own gate passed. That residual -- an exit-0 verdict can still understate a
  severity-demoted break -- is recorded in `action/AGENTS.md` and on the
  `report.unestablished_result_reads_as_success` bug class; closing it needs a
  verdict value distinguishing "accepted, tier unverified" from "accepted,
  compatible", which is an output-contract change.
- **Every requested JSON destination is validated on its own, and must have
  been written by the run that reports it.** The validation above resolved the
  report through `_json_report_src`, a *fallback chain* that returns the first
  destination to arrive -- so a `format: json` run whose `output-file` never
  arrived was covered by a valid `extra-args --write json=secondary.json` and
  still published a compatibility verdict, even though `--write` names an
  independent artifact that cannot satisfy the primary request. Each
  caller-named destination is now asked for its own validity. Separately, a
  destination is judged not only on whether it parses but on whether *this*
  invocation produced it: a readable report left behind by an earlier step, or
  committed into the checked-out tree, is rejected rather than read as the
  run's own result. The pre-run (mtime, size) bookkeeping that already guarded
  the verdict *source* now covers every requested destination.
- **The assurance-consistency check now runs on every requested destination.**
  It read whichever report the fallback chain settled on, so with several
  requested JSON artifacts a clean primary hid a self-contradictory secondary —
  one claiming a schema that carries `analysis_assurance_exit_contribution`,
  reporting an `analysis_assurance` block and omitting the contribution. Both
  passed the readability check and the step published `COMPATIBLE`.
- **An operational outcome no longer reads as a clean compatibility result.**
  A report whose verdict is `ERROR`, `unsupported`, `failed`, `UNKNOWN` or
  `not_comparable` — or whose `run_outcome.operational` names any failure —
  reports that nothing was compared, not that the new version is compatible.
  At exit 0 those published `COMPATIBLE`. They now take the same non-waivable
  `ERROR` path the exit-4 operational arm already takes, except
  `not_comparable`, which gets the established `NOT_COMPARABLE` verdict. The
  canonical `run_outcome.operational` axis is read ahead of the legacy verdict
  sentinel, so a release carrying one library's real break beside another's
  failed extraction reports the failure rather than laundering it into a break.
- **Three more ways a report could be accepted without supplying a result.** A
  structural fallback keyed on a *present* `verdict` key re-admitted the
  arbitrary strings the vocabulary check rejects, so `{"verdict": "write
  interrupted", "reason": {}}` passed on the strength of its `reason` object; a
  `libraries` array was accepted as a verdict source although nothing reads it,
  letting a release whose members reported `BREAKING` publish `COMPATIBLE`; and
  the stdout-mode check was gated on the destination inventory being empty, so
  an `extra-args --write json=` destination suppressed it and a valid secondary
  masked an unusable stdout report. All three now require the thing they claim
  to establish.
- **An `extra-args --output` override is honoured when locating the report.**
  `extra-args` can override this script's own `-o`, and Click's last-wins rule
  makes the override the real destination, so keying on the `output-file` input
  alone both failed working runs (an override with no input named no
  destination, and the run was judged as the stdout shape) and validated a
  superseded path while the report landed elsewhere. Both the destination
  inventory and the report-reading chain now resolve the effective path.
- **An unrecognized `verdict` string is no longer read as a result.** The
  reader accepted any non-empty verdict, so `{"verdict": "write interrupted"}`
  parsed as a readable result and then matched none of the tiers the Action
  acts on, leaving the COMPATIBLE fallthrough in place. Verdicts are now
  checked against the vocabulary the emitters actually produce, pinned against
  `checker.Verdict` and the release fan-out's own rollup values so the table
  cannot drift out of step with either.
- **A report destination named in `extra-args` can no longer forge a workflow
  command.** The diagnostics naming a missing `--write json=` destination
  interpolated that PR-controlled path into a `::error::` line without
  escaping, so `--write json=x%0A::add-mask::secret` reached the runner as a
  second command once GitHub decoded it. Sanitized at the point of emission.
- **A self-contradictory report no longer reads as a passing assurance check.**
  A report claiming schema 2.40 or newer that carries an `analysis_assurance`
  block while omitting the `analysis_assurance_exit_contribution` emitted
  beside it now fails the step, rather than being absorbed by
  `_assurance_gated`'s deliberate "cannot tell means not gated" rule. Reports
  that legitimately carry neither key, and reports predating the field, are
  unaffected.

### Changed

- **The Action's JSON-report reader is now a real file, `action/report_query.py`,
  instead of ~300 lines of Python embedded in `action/run.sh`.** Embedded, its
  query semantics were unreachable from `pytest`, `mypy` and `ruff`, which is how
  the surrounding `cli-mirror` comments came to cite modules deleted with
  ADR-068. The extraction was verified behavior-preserving against the heredoc it
  replaced over roughly 247,000 adversarially-generated (document, query,
  argument) cases, including the malformed values where Python's `==` disagrees
  with the string comparison `run.sh` performs. The retired `assurance_status`
  query, which no caller asked for, was dropped rather than carried over.
