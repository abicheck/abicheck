# CI cost and assurance — closing the audit's remaining items

**Status:** Proposed. Phase 0 landed in PR #1240; Phases 1–6 not started.

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

**Blocked on a human decision.** This changes required-check *names*, so it
needs a coordinated branch-protection update — either preserve the existing
names through the migration or change the protection rule in the same window.
Not a unilateral workflow edit.

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

The audit recommended giving the file to the dedicated native jobs. Doing that
as stated would **silently drop Linux execution of those 20 tests**, because the
dedicated job has no Linux lane. The genuine duplication is macOS and Windows
only.

**Target.** Either exclude the file from the generic selector *only* where the
dedicated job also runs (`--ignore` gated on `runner.os`), or add a Linux lane
to the dedicated job and exclude the file from the generic selector everywhere.
Both preserve today's platform coverage exactly; the first is smaller.

**Before changing anything:** both selectors carry `ABICHECK_MIN_EXECUTED`
floors (20 on the generic Linux lane, 5 on the dedicated job). Removing tests
from a selection moves the executed count those floors gate, and *collected* is
not *executed* — most of the 654 are compiler-gated. Measure executed IDs per
platform first. This phase buys correctness of ownership, not a measured
runtime saving.

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

**Target.** A small representative component-and-bundle fixture exercised
through `compare --depth binary` / `--depth headers`, guarding the pipeline
rather than reviving tests for a retired command. This is the one remaining
phase that needs no decision from anyone and no unavailable toolchain — it is
test-authoring work.

## Explicitly not pursued

**Adding Python 3.10 to the unit matrix.** `pyproject.toml` advertises
`requires-python = ">=3.10"` while the matrix starts at 3.12, so the advertised
floor is tested by nothing. The audit recommended adding the oldest supported
interpreter. Reviewed and declined (2026-09-12): `AGENTS.md` documents the
3.12/3.13/3.14 set as a deliberate choice, and adding a lane runs against this
document's own purpose. The gap is therefore **accepted, not closed** — recorded
here so it is not rediscovered as an oversight. Reversing it means either
testing the floor or raising `requires-python`; both are packaging decisions.
