---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A `kind: bundle` check still cannot run at `depth: headers`: only the *baseline* half of per-member header staging is wired (2026-09-12)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-12): OPEN, partly narrowed.** actions/check-target/action.yml:1828 still routes bundle old-library to `steps.resolve.outputs.binaries-dir`, not `member-snapshots-dir`. BUNDLE_CHECK_DEPTHS is unchanged (abicheck/buildsource/project_targets.py:151). (b)'s fail-early requirement is met: `actions/check-target/validate-inputs.sh` rejects `requested-depth: headers` for `kind: bundle` at input validation, before any tool runs, and the message now names the real remaining blockers instead of the retired "no historical header snapshot" reason (pinned by `tests/test_action_check_target.py::TestValidateInputs::test_bundle_kind_rejects_headers_depth`). (a) routing and the candidate-side per-member header selection are still open; see "Why (a) was not routed in this pass" below.


`abicheck/buildsource/bundle_member_snapshots.py` now resolves and stages
each bundle member's own historical baseline snapshot -- the per-member
header evidence `actions/baseline` already dumps from the baseline
checkout -- into a clean, one-snapshot-per-member directory usable as a
directory-`compare` old-side operand, and reports per member whether that
snapshot genuinely carries header-derived evidence
(`AbiSnapshot.from_headers`), exposed by `actions/resolve-baseline` as the
`member-snapshots-dir`/`member-header-evidence` outputs
(`stage-member-snapshots: true`). `BUNDLE_CHECK_DEPTHS` and
`actions/check-target/validate-inputs.sh` are deliberately **unchanged**:
the restriction is a false-clean guard, and parts of the evidence path
are still missing, any of which alone would re-create the very
silent-miss it prevents (updated 2026-09-13: (a)'s stated blocker is
gone, its routing is not).
(a) **`check-target` still routes the bundle compare at `binaries-dir`.**
The *reason* recorded here through 2026-09-12 -- that the cross-library
bundle graph skips snapshots, so the old side would trade header evidence
for bundle-graph evidence -- **no longer holds**, and this entry said so
for longer than it was true. `abicheck/bundle.py` now reads a stored
member as bundle-graph evidence on both shapes: a directory-backed
`ProjectSnapshot` sub-package (`_stored_elf_metadata`) and a loose,
file-backed snapshot, content-sniffed rather than recognized by suffix
(`_stored_file_snapshot_evidence`, which also recovers the member's real
on-disk filename from `AbiSnapshot.library` so a sibling's `DT_NEEDED`
still resolves). A directory of loose per-member snapshots staged by
`actions/resolve-baseline` (`stage-member-snapshots: true`) is therefore
a usable old-side operand that keeps *both* kinds of evidence -- measured
on a real six-member bundle: bundle layer present, no phantom
`bundle_library_added`, per-member findings matching the single-pair
control leg. What remains is the routing itself (`check-target` passing
`member-snapshots-dir` instead of `binaries-dir`), plus (b) below, plus
the cost calibration in (c).
(b) **The candidate side still has no per-member header selection.**
`check-project.yml` carries one project-wide `header:` input, and the
release fan-out resolves headers run-wide
(`cli_compare_release_helpers._resolve_release_headers`, threaded to every
pair) rather than per member. A bundle whose members have disjoint public
header sets would therefore parse each member's candidate side against
the union.
(c) **Header-depth bundle cost still does not fit the runner.** The
per-worker *memory budget* half of this is closed:
`release_job_mem_budget_gib(depth)` now defaults to 4.0 GiB at `headers`
(6.0 at `build`/`source`) instead of the binary-depth 1.0 GiB, so the
fan-out clamps its worker count instead of overcommitting. What remains
is the total, and the wall-clock and memory halves of it are now at
different stages -- **do not read this gap as closed by either alone.**

*Wall clock, improved but not on a verified receipt here.* The ~84 min
figure this entry has carried is the original six-member observation.
A later external oneDAL experiment on the same profiled setup reports the
same shape at **2,247 s wall / 2,979 s CPU (37:27)**, against 4,814 s /
5,950 s CPU on the older tree. That is a **reported** measurement from
outside this repository, taken under a profiler, and it is recorded here
as such: it has not been reproduced in-tree, no oneDAL binary is present
in this workspace to reproduce it against, and a profiled run's overhead
does not subtract cleanly from an unprofiled one. It also measures the
*scan*, not the job -- setup, download, dependency resolution and output
publication are outside it, so it does not by itself establish that the
job fits a 45-minute `timeout-minutes`.

*Memory, reduced but still short of the target.* The same experiment
reports 19.93 GiB peak RSS, essentially unmoved from the original 20.61
GiB -- so the speedup did **not** come with the memory fix this gap needs.
The 16 GB runner is the binding constraint, and 16 decimal GB is ~14.90
GiB, not 16: a ~20 GiB peak is over the real byte limit by a third, not
by a rounding error.

One contributor to that peak is now removed in-tree and measured:
`policy/depth_projection.py` answered every `--depth` rung with one
unconditional `copy.deepcopy`, so a `--depth headers` projection
duplicated an entire L2 surface in order to rebind two fields
(`build_mode`, `build_source`), neither of which is read-modified-written
at that rung. On a real 327-type/4,802-function header snapshot that cost
+34% resident (0.516 -> 0.691 GiB) and +12.5 s per comparison, paid once
per fan-out member, concurrently; the copy is now scoped to what each rung
actually writes to, and the same projection is 0.000 s and +0.000 GiB with
byte-identical findings. **This is a contributor, not the fix:** it was
measured on a local C++ fixture, not on oneDAL, and it removes a
*duplicate* of the surface rather than shrinking the surface itself, so
the resident baseline each worker holds is unchanged. Whether it moves the
bundle peak enough to matter is unmeasured, and a re-derived
`release_job_mem_budget_gib` `headers` constant deliberately was **not**
taken on the strength of it -- lowering that constant on an unvalidated
expansion factor is the same mistake the "conservative fix ... implemented
twice and reverted twice" paragraph below records.

A second contributor is now removed, and this one is an *admission*
change rather than a per-copy one. `workflows/release_jobs.py` clamped the
fan-out with `int(available / budget)` -- it committed 100% of what the
memory probe reported to worker working sets and reserved nothing for the
state resident beside them. That state is not hypothetical: the fan-out is
a `ThreadPoolExecutor`, so every member's result accumulates in the *same*
address space its workers allocate in, the shared header-depth context
(AST/template indexes, acquisition and metadata reuse) is live throughout,
and `MemAvailable` is sampled once, at pool-sizing time, before any of it
exists. Measured on this repository's own 16 GB reference host: `headers`
depth probed 13.17 GiB available and admitted 3 workers at the 4.0 GiB
budget -- a **12.0 GiB commitment, 91% of available**, with the parent's
share still to come out of the remaining 1.17 GiB. Workers are now
admitted against committable memory (a utilization fraction, less a flat
reserve; `ABICHECK_RELEASE_MEM_UTILIZATION` /
`ABICHECK_RELEASE_MEM_RESERVE_GIB`), which on that same host admits 2
workers for an 8.0 GiB commitment (61%). It can only ever admit *fewer*
workers than the rule it replaces, still floors at one, still skips
entirely when RAM cannot be probed, and still scales -- a 32 GiB host
admits the full six. Binary depth is unchanged on any host that was not
already memory-clamped, so an ordinary `compare OLD_DIR NEW_DIR` is sized
exactly as before.

**This is still not the receipt, for a specific and measurable reason.**
Admission control bounds the *concurrent* half of the peak only. The
other half is retention, and it is bounded on the default path but not on
all of them: `workflows.release_members` keeps a compact
`BundleSignatureEvidence` per member normally, but under
`need_full_snapshots` -- which JUnit output and `--bundle-facts-out` both
set -- it retains **every** member's full old *and* new snapshot
simultaneously, for the life of the fan-out. For a six-member bundle at
header depth that is twelve full L2 surfaces held at once, a quantity no
worker-count clamp can reduce, and the reported 19.93 GiB peak was taken
on a shape that may include it. Closing that means spooling completed
members' full results through the existing storage contracts rather than
holding them, which is a larger change than this one and is deliberately
not attempted here. Until it is done, a bundle run that requests JUnit or
bundle-facts output should not be assumed to fit the 16 GB runner on the
strength of the admission change alone.

So the routing decision this entry gates still needs a real receipt:
a full six-member run, unprofiled, under the actual runner's byte limit
and `timeout-minutes`, with peak RSS sampled externally. Routing a bundle
check at header depth needs that receipt (and, if the peak is still ~20
GiB, a runner/timeout decision), not another budget constant.

One further hole in the same budget is **open and deliberately not closed
here**: the depth a worker reaches is inferred from `--depth` and from
whether header roots were supplied, and a release whose members are
*already-serialized snapshots* answers neither -- such a run types no depth
and supplies no roots, so it is budgeted as binary depth while each worker
holds two possibly deep-evidence snapshots resident.

The conservative fix -- size such a run at the table's largest rung, since
the captured depth is not cheaply legible (reading it means decoding the
very snapshots whose size is the problem, once per member, before the
fan-out that would have loaded them lazily) -- was implemented twice and
reverted twice, and the second attempt is the one worth recording, because
the first revert was argued from a bad measurement. The claim then was that
it "moved 75 tests from passing to failing"; that number came from a run
racing concurrent local test sessions, and a clean run of the same suite
gives 8 failures against `main`'s 13, none in the affected family. So the
measurement was noise and the reasoning that rested on it was wrong.

Re-measured properly, serially, the change still moves **21** release tests
from passing to failing, for a real reason rather than load: at a 6.0 GiB
per-worker budget an ordinary host clamps the fan-out from 4 workers to 1
for *every* stored-baseline comparison -- the most common workflow this
tool has -- and the clamp notice that then appears changes the command's
output. That is a defensible trade against an OOM-killed job and a bad one
against a package of small snapshots, and it is not this PR's trade to
make: it belongs in its own change, where the worker-count reduction, the
21 tests' expectations, and a *measured* encoded-to-resident expansion
factor (the accurate signal is a member's resident cost, for which on-disk
size is the cheap proxy, but the budget model is depth-keyed) can be
reviewed together.
(d) **No first-party Action emits a bundle-facts document.** The
producer itself exists and is reachable -- `compare`'s release fan-out
takes `--bundle-facts-out`, forwardable today through the root Action's
`extra-args` -- but no Action declares it as an *output*, so the
stored-OLD-side bundle route has no supported CI producer to pair with
its (fully wired) consumer. Deliberately not closed by adding a root
command: "write this pipeline stage's artifact" is precisely the shape
ADR-054's admission bar rejects (`AGENTS.md`, "Adding a new top-level
command", items 3 and 5). The right slice is an output on the Action
that already runs the fan-out, not new CLI surface.
**Why (a) was not routed in this pass (2026-10-12).** Routing the old side
to `member-snapshots-dir` is only safe at header depth, and header depth is
still rejected upstream, so the routing would be dead code. Routing it at
binary depth would change existing binary-depth outcomes (the old side
would become stored snapshots instead of the binaries the bundle graph
reads today), which this entry does not license. Two further gaps block
enabling header depth: `check-target` does not yet pass
`stage-member-snapshots: true` to `resolve-baseline`, so no
`member-snapshots-dir` is produced for a bundle run; and (b)'s candidate
side is unsolved. (c)'s receipt is also still required. Shipping the
routing before (b) and (c) would replace a loud rejection with a silent
mismatch, which is the failure mode this entry exists to prevent.

Until these land, a bundle check stays binary-depth, and staging is
additive: it changes no existing outcome, and every pre-existing
invocation (which does not pass `stage-member-snapshots`) is unaffected.
Covered by `tests/test_bundle_member_baseline_staging.py`, whose central
test proves executably -- over generated member sets, against an oracle
independent of every abicheck detector -- that a header-derived change
*is* detected per member once the old side comes from the staged
baseline, and *is not* when both sides come from the candidate (today's
behavior).
