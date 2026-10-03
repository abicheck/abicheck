# CI cost and assurance — closing the audit's remaining items

**Status:** Proposed. Phases 0 and 6 landed in PR #1240; Phase 2's platform-safe half landed as a follow-up; Phase 1 and Phase 7 landed 2026-09-30; Phases 3, 4 and 5 not started.

Origin: a CI audit that profiled this repository's workflows and found one
real product performance bug plus a family of *inert configuration* — settings
that read as careful and have no effect on the platform or path where nothing
fails to reveal them. Phase 0 closed the bug and four of those; this document
records what the audit found and left, each grounded in a measurement taken
against this repository rather than in the audit's own summary.

Two framings from the audit are load-bearing and carried forward here:

- **This is a public repository on standard GitHub-hosted runners.** The win is
  runner occupancy, queue congestion and developer waiting time — not a dollar
  figure. Applying private-repo minute rates here would produce a misleading
  number, and no phase below is justified on cost savings.
- **Reducing assurance is not an optimization.** Every phase must keep the
  same tests running, or say explicitly which capability moved to a scheduled
  owner and what detection delay that accepts.

## Phase 0 — landed (PR #1240)

| Finding | Fix |
|---|---|
| `read_snapshot_bytes` passed the gigabyte-scale safety *ceiling* to the allocator as a read *size* — reading an 8 KiB snapshot requested ~1 GiB. Invisible on Linux's overcommitting allocator; on Windows it is committed and zeroed, which is where the Windows CI wall clock was going (one stored-package parity test: 118.03s → 1.80s, assertions unchanged). | Chunked read preserving the single descriptor, the byte bound, and the one-byte overshoot |
| Six workflows grouped concurrency by `pull_request.number \|\| run_id`, so every `push` landed in its own group and `cancel-in-progress` could never cancel a superseded `main` run | Group by ref on `push`, `run_id` isolation kept for `schedule`/`workflow_dispatch` |
| Three path-filtered workflows omitted the infrastructure that decides *how* they run (`pyproject.toml`, composite actions, the scripts those actions execute) | Filters closed; dependency set derived from what each workflow executes |
| The macOS unit lane and the `slow` lane collected coverage no consumer ever read | Collection dropped, every test kept |
| `COVERAGE_CORE=sysmon` claimed "the same line+branch data" while silently falling back to CTracer | Claim corrected, disagreement asserted by a test |
| Attacker-controlled free text reaching a `run:` body was unguarded (both real sites already safe, nothing enforced it) | Repo-wide guard plus an executing attack and a control |

Each is registered as a bug class in `tests/regressions/manifest_tool_surface.py`.

## Phase 1 — replace the polling required-check bridges

**Measured.** `docs-pr (required)` polls up to **25 minutes** inside a 30-minute
job; `test-action (required)` polls up to **35 minutes** inside a 40-minute job.
Both occupy a runner doing nothing but waiting for another workflow. That is a
configured upper bound (60 runner-minutes), not a measured typical cost.

**Target.** Reusable workflows called at job level with ordinary `needs`
dependencies, plus one stable aggregate required check. The aggregate must
distinguish *intentionally unselected* from *unexpectedly missing*: a selected
job that is missing, skipped, cancelled or failed must not produce a green
aggregate. `test-action summary` already implements exactly that predicate and
is the model to follow.

**Landed (2026-09-30) — by deletion.** The blocker recorded here ("changes
required-check names, needs a branch-protection update") dissolved when the
maintainer removed the `required_status_checks` rule altogether
(`.github/AGENTS.md`, "Required-status-check configuration"): with nothing
required, the two bridges gated nothing and only held a runner. Measured on
run 36642140703 (2026-09-29): `test-action (required)` queued 52.6 minutes
and then polled for 35.2. Both jobs are gone;
`tests/test_required_checks_governance.py` rejects any job that polls another
check in a sleep loop, and states the reusable-workflow design above as the
route if merge-blocking is ever re-enabled.

## Phase 2 — one owner for `test_cross_platform_integration.py`

**Measured, and this corrects the audit.** The generic selector
`pytest tests/ -m integration` collects **654** tests, of which **20** come from
`test_cross_platform_integration.py`; the dedicated native job collects exactly
those same 20. So the overlap is real and fully contained.

But the two jobs do **not** run on the same platforms:

| Job | Matrix |
|---|---|
| `integration-tests` (generic selector) | `ubuntu-24.04`, `macos-latest`, `windows-latest` |
| Native PE/Mach-O compare workflows (dedicated) | `macos-latest`, `windows-latest` |

> **Correction (2026-10-03, PR #1460):** every test in that file skips on
> Linux (each needs Apple clang or MinGW gcc), so the Linux leg never executed
> them — 20 skipped, 0 run. The file is now `verify.py`'s `native-compare`
> step on macOS/Windows only, and the integration step excludes it everywhere.

The audit recommended giving the file to the dedicated native jobs. Doing that
as stated would **silently drop Linux execution of those 20 tests**, because the
dedicated job has no Linux lane. The genuine duplication is macOS and Windows
only.

**Landed** (the first of the two options). The generic integration step was
split per OS: Linux keeps the file, macOS and Windows exclude it and leave it
to the dedicated job. Platform coverage is unchanged — the file still runs
everywhere it ran before, once.

This phase originally said to measure executed IDs per platform first, because
`ABICHECK_MIN_EXECUTED` gates the executed count and *collected* is not
*executed*. Reading the floors settled it without that measurement: the two
lanes that now exclude the file sit at a `'1'` floor (macOS and Windows, both
"> 0" guards against a missing toolchain), so dropping 20 tests from a
selection that still collects hundreds cannot trip them, and Linux — the only
lane with a real floor, `'20'` against a known ~195 — is untouched. The
caution was warranted in general and did not apply here.

The same split fixed a second defect in the same step, found by the guard
written for Phase 0's own known gap: the step collected
`coverage-integration.xml` on Linux *and* macOS while the Codecov upload is
gated to `ubuntu-24.04`, so macOS paid ~60% instrumentation overhead for a
report nobody read. Same class as the two sites Phase 0 fixed by inspection,
and the one it missed.

## Phase 3 — make the coverage-core request live, or retire it

**Measured.** `COVERAGE_CORE=sysmon` cannot take effect while
`[tool.coverage.run] branch = true` applies and the interpreter is below 3.14:
coverage.py warns and falls back to CTracer. Verified on this repository's own
interpreter. Phase 0 documented the disagreement rather than resolving it.

**Target.** Move the canonical coverage lane to Python 3.14, where
sys.monitoring measures branches, then verify the engine actually selected and
compare the produced reports. The 95% branch-coverage floor stays unchanged.

**Blocked on a project decision.** The canonical Python is a repository
contract: `repo_facts.json`'s `canonical_python`, the `ai-readiness` job's pin,
and `AGENTS.md`'s own statement of which interpreter to develop against all
move together. `tests/test_coverage_core_effectiveness.py` will report the
change automatically once the lane moves.

## Phase 4 — consolidate the Action's semantic scenarios

**Measured.** `test-action.yml` defines ~20 jobs, **19** of which invoke
`uses: ./` — each allocating a runner and installing the Action to exercise one
semantic scenario (compatible/breaking, additions, severity, report formats,
exit codes).

**Target.** Separate *installation coverage* (clean environments, dependency
sources, toolchains, native platforms — genuinely needs separate jobs) from
*semantic coverage* (the cross-product of verdicts and outputs — can run in
fewer prepared environments).

**Caveat the audit itself flags, and it is decisive:** the Action installs
unconditionally, so merging repeated `uses: ./` invocations into one job does
**not** by itself remove the install work. Any consolidation has to address
that explicitly or it will move jobs around for no gain.

## Phase 5 — bound the mutation schedule

**Measured.** The scheduled mutation job carries `timeout-minutes: 355`, after a
recorded 240-minute overrun. The PR lane is already diff-scoped
(`--scope-run-to-diff`) and is not the problem.

**Target.** Bounded, disjoint mutant shards with complete receipts before any
baseline is accepted or published. `--require-baseline` already refuses to let a
run that gated nothing exit 0, and an unresolved run is a *failed measurement*
rather than zero survivors; sharding must preserve both.

**Not benchmarked** — by the audit or here. Needs measurement before design.

## Phase 6 — restore a vision-aligned performance guard

**Measured.** `tests/test_performance.py` benchmarks `compare()` over in-memory
snapshot pairs. The deleted `tests/test_perf_binary_scan.py` guarded something
different: the **CLI end-to-end** path at `--depth binary` / `--depth headers`
against a real artifact. It went away with the `scan` command, and `ci.yml`'s
own comment already records the absence of a `compare`-side equivalent as a
tracked capability gap.

**Landed** (`tests/test_perf_compare_depth_scaling.py`). Both depths are
exercised through the real CLI against compiled ELF fixtures, guarding the
pipeline rather than reviving tests for a retired command.

It asserts a *scaling exponent* rather than a wall-clock ceiling, following
`_perf_scaling.py`, which exists because fixed per-run time bounds on a shared
runner flaked an unregressed `main`. Measured when written: `--depth binary`
costs 0.36s at n=500 and 1.50s at n=2000 — 4.15x for 4x the input. A
non-vacuity precondition fails the test if fixed overhead ever starts
dominating, since the exponent would then tend to zero and pass while
measuring nothing; that predicate cannot fire under today's in-process
harness (~27ms of overhead), so it is exercised directly rather than left as
an assertion nobody has seen hold.

## Phase 7 — queueing, the critical path, and work done once per OS

**Measured** (run 36642140703, 2026-09-29, a PR push while four other PRs
were also running CI). The run took 101 minutes. Every one of its 21 CI jobs
waited **24–54 minutes in the queue** before starting — including jobs that
then ran for 12 seconds — and trivial workflows (changelog fragment check,
dependency review) took ~30 minutes of wall clock for seconds of work. A
single PR push starts roughly 60 jobs across 14 workflows, so a handful of
concurrent PRs saturate the account's concurrent-runner limit, and macOS's
much smaller pool worst of all. Of the running time, the canonical
coverage-collecting unit lane was the critical path: **33m29s** for 58,803
tests on a 4-core runner (it measured 13m23s on 2026-08-29).

That lane's slowest-25 list was dominated not by behaviour tests but by
whole-tree structural scans (`fact_field_readers` 129s, `fact_detector_misuse`
124s, `subprocess_bash_is_resolved` 81s + 46s + 28s, the encoding scan 73s,
...). Each ran three times per push — Linux under coverage tracing, macOS,
Windows — although its result depends only on the committed tree.

**Landed:**

| Change | Effect |
|---|---|
| `repo_scan` marker on 31 whole-tree scan test functions (selected from measured durations, each confirmed to walk the committed tree); excluded from every unit leg; run once, uninstrumented, by the new `repo-scan-tests` job (`verify.py --only repo-scan-tests`) | ~2 minutes once instead of ~15 CPU-minutes per leg ×3; same tests, same PR |
| Canonical lane split into three `pytest --shard=K/3` jobs (`tests/pytest_shards.py`: whole files, LPT by test count, order-independent — property-tested) plus a `unit-tests-coverage` fan-in that combines the data and enforces the 95% floor once | Same selection, same floor, critical path divided; a missing shard fails the fan-in's explicit count check |
| Four scale tests (≥35s each on CI) moved to the `slow` lane, which runs on every PR | Off the critical path; still run per PR |
| `packaging (ubuntu-latest)` removed | It ran `build` + `twine check`, which `fair-metadata`'s `distribution-build` step already runs on Linux in the same workflow; the Windows leg stays |
| The two polling bridges deleted (Phase 1) | Two fewer runners held per PR, up to 60 runner-minutes |
| macOS/Windows full unit legs, the 3.15 prerelease smoke leg and the 3.15t free-threading job run on `main` pushes, schedule/dispatch, and PRs labelled `ci:full` — not every PR push (maintainer decision, 2026-09-30) | The ~27-minute legs on the scarcest runner pools stop competing on every push. **Moved detection point, stated:** a macOS/Windows-only unit regression now surfaces on the first `main` run after merge unless the PR carries `ci:full`. The native PE/Mach-O compare jobs and the macOS/Windows integration legs still run on every PR |

**Considered and not landed here**, each for a stated reason:

- *examples-validation's clang half on PRs.* Its merge and
  `full-example-matrix` collectors require both toolchains' artifacts, so
  gating one half cascades through three downstream jobs; needs its own
  change to the collectors. The `agentready` PR diff also stays: it exists
  only to comment on PRs.
- *`needs: lint-and-types` in front of the heavy matrices.* It saves runners
  only on pushes that fail lint, and on a saturated pool it adds a second
  queue wait to every push that passes — the common case. Not landed.
- *Letting a non-`performance` label skip `performance.yml`.* Adding any label
  re-runs that lane; the fix needs `github.event.label`, which
  `tests/test_classify_perf_paths.py` deliberately bans from the workflow for
  a separate, real bug. Needs its own design, not a local exception.
- *Larger or self-hosted runners.* An account-level decision.

## Explicitly not pursued

**Adding Python 3.10 to the unit matrix.** *Superseded (2026-09-25): the floor
was raised to 3.11 and `python-compat.yml` now smoke-tests every non-canonical
supported interpreter; the full suite runs on 3.13 only.* Original note: `pyproject.toml` advertises
`requires-python = ">=3.10"` while the matrix starts at 3.12, so the advertised
floor is tested by nothing. The audit recommended adding the oldest supported
interpreter. Reviewed and declined (2026-09-12): `AGENTS.md` documents the
3.12/3.13/3.14 set as a deliberate choice, and adding a lane runs against this
document's own purpose. The gap is therefore **accepted, not closed** — recorded
here so it is not rediscovered as an oversight. Reversing it means either
testing the floor or raising `requires-python`; both are packaging decisions.
