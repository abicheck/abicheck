---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Known gaps — acknowledged remaining work

This page lists the gaps that are still open, the fix attempts that were
tried and should not be repeated, and the deliberate limitations that look
like bugs but are not. Per `AGENTS.md`'s "Fix the cause, not the instance"
principle, **read the entry for an area before re-attempting a fix there**:
several entries record a heuristic or a narrow patch that looked like the
obvious fix, was reviewed, and was reverted.

Closed entries, and entries whose defect lived only in deleted code (the
`scan` family removed by ADR-068 Phase 6), are kept for history in
[Known gaps — closed and moot](archive/known-gaps-closed.md). Look there
before re-reporting a gap that is no longer listed here.

## How to read this page

Every entry was re-verified against the code on 2026-10-09 at `456989f`,
and each one opens with a status line:

| Status | Meaning |
|---|---|
| OPEN | The gap is present as described. |
| PARTIAL | Part of the entry is fixed; the status line names what remains. |
| NEGATIVE_RESULT | An approach measured or reviewed as not helping. Do not re-attempt it. |
| POLICY_NOTE | A deliberate ruling or scope limit, not a defect. |

The status line overrides older wording in the entry body. When you fix a
gap, move its entry to the archive page with a CLOSED status line rather
than striking it through here. When you re-check an entry, update its
status line's date and commit.

## Priority

Open correctness gaps, in the order of the maintainer ruling of 2026-10-01
(items that ruling listed and that are now closed are in the archive):

1. [A directory `compare` still feeds the union `-H` set to every member (no per-member header operand); unreached type findings still count in member verdicts](#a-directory-compare-still-feeds-the-union-h-set-to-every-member-no-per-member-header-operand-unreached-type-findings-still-count-in-member-verdicts) — the per-member header operand (step 2) and the verdict half of
   step 3 remain; step 2 also fixes the quadratic multi-library cost.
2. [An `-I`-reached sibling library's headers yield LOW-confidence export obligations; release reconciliation and JSON confidence not yet narrowed](#an-i-reached-sibling-librarys-headers-yield-low-confidence-export-obligations-release-reconciliation-and-json-confidence-not-yet-narrowed) — narrowed to LOW confidence; release reconciliation and the JSON
   confidence field are not narrowed yet.
3. [`--exclude-header` cannot stop a transitively-included header being analyzed](#-exclude-header-cannot-stop-a-transitively-included-header-being-analyzed) — the `--dump-manifest` combination is now rejected outright (see
   the archive); what remains is that exclusion acts on the resolved header
   list, not on transitive includes.

Next, unranked (the `--no-baseline` one-sided-detector gap, the target-platform gap and the untagged `dependency_scope` gap were closed on 2026-10-10; see the archive). Each of these can let a run read as cleaner or more
comparable than its evidence supports:

- [An informational, no-material-change reconciliation outcome gates the build under `--severity-preset strict` (2026-09-12)](#an-informational-no-material-change-reconciliation-outcome-gates-the-build-under-severity-preset-strict-2026-09-12)
- [A snapshot's `build_mode` is captured only for ELF, and only from the main image](#a-snapshots-build_mode-is-captured-only-for-elf-and-only-from-the-main-image)
- [A stored package's generation drift is reported only for `ProjectSnapshot` packages, and generations are bumped by hand](#a-stored-packages-generation-drift-is-reported-only-for-projectsnapshot-packages-and-generations-are-bumped-by-hand)
- [Scalar and release `compare` still fold the exit code in two places (2026-10-07)](#scalar-and-release-compare-still-fold-the-exit-code-in-two-places-2026-10-07)
- [`unversioned_exported_symbol` false-positives on base-version (VER_FLG_BASE) symbols of versioned libraries: the model cannot tell base-bound from unversioned](#unversioned_exported_symbol-false-positives-on-base-version-ver_flg_base-symbols-of-versioned-libraries-the-model-cannot-tell-base-bound-from-unversioned)


## Open and partially open gaps

Each entry's status line says what is still open. A PARTIAL entry's body also describes the half that is already fixed; the status line is authoritative where they disagree.

### A `kind: bundle` check still cannot run at `depth: headers`: only the *baseline* half of per-member header staging is wired (2026-09-12)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** actions/check-target/action.yml:1828 still routes bundle old-library to `steps.resolve.outputs.binaries-dir`, not `member-snapshots-dir`. BUNDLE_CHECK_DEPTHS is unchanged (abicheck/buildsource/bundle_member_snapshots.py:18,69). So (a) routing and (b) candidate-side per-member headers are still open.


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
all of them: `cli_compare_release_pairwise` keeps a compact
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
Until these land, a bundle check stays binary-depth, and staging is
additive: it changes no existing outcome, and every pre-existing
invocation (which does not pass `stage-member-snapshots`) is unaffected.
Covered by `tests/test_bundle_member_baseline_staging.py`, whose central
test proves executably -- over generated member sets, against an oracle
independent of every abicheck detector -- that a header-derived change
*is* detected per member once the old side comes from the staged
baseline, and *is not* when both sides come from the candidate (today's
behavior).

### `unversioned_exported_symbol` false-positives on base-version (VER_FLG_BASE) symbols of versioned libraries: the model cannot tell base-bound from unversioned

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/elf_symbol_versions.py:180 still returns early for ver_idx<2, which leaves version='' and is_default=True (the model default in model/elf_facts.py:84). cross_source_checks.py:1065 skips only `not sym.is_default or sym.version`, so symbols bound to the base version still get flagged as unversioned_exported_symbol.

Found once ADR-068 §3
row 3's first slice made `checker.compare()` run this check
automatically: self-comparing a real, canonically GNU-symbol-versioned
C library (`zlib1g`, exercised by `.github/workflows/realworld-
validation.yml`'s "Ubuntu package and bundle smoke" job) reports
`COMPATIBLE_WITH_RISK` with one `unversioned_exported_symbol` finding
per base-API function — `readelf -V`/`objdump -T` confirm every one of
them is legitimately bound to the anonymous/default GNU version node
(`Base`, i.e. the library's own SONAME), the standard, permanent
convention for a versioned library's original API: symbols present
before the library adopted symbol versioning stay on the base version
forever, and retroactively assigning one a named version tag would
itself be an ABI break. `_check_unversioned_exported_symbol`
(`buildsource/cross_source_checks.py`) cannot currently distinguish "bound to the
base version" from "genuinely absent from `.gnu.version`" — both leave
`ElfSymbol.version == ""` (`elf_metadata.py`'s `_apply_version_to_symbol`
intentionally never populates `version`/`is_default` for `ver_idx < 2`,
and `_build_verdef_index` intentionally excludes the base/`VER_FLG_BASE`
entry from `ver_index_map`), so nothing in the persisted model lets the
check tell them apart.

**Why not fixed here.** A sound fix needs a real distinguishing fact
(e.g. a new `ElfSymbol` field recording "explicitly bound to the base
version"), which is a `model/`+`storage/` schema change (`AbiSnapshot.
SCHEMA_VERSION` bump, `elf_from_dict`/`elf_to_dict` wiring in
`snapshot_platform_blocks.py`) — real, scoped work, but a materially
larger and riskier change than "make an already-migrated check
reachable," and touching the shared `ElfSymbol.version` field's *value*
for the base case (the cheaper-looking alternative) risks silently
changing every other detector that already reads `version == ""` as
"unversioned" (symbol-version add/remove detection, OLD/NEW symbol
matching by `(name, version)`, and others) — an unbounded blast radius
for a targeted fix.

**Why it stands regardless.** `buildsource/cross_source_checks.py`'s own module
docstring states the design tolerance this falls squarely inside: these
findings "are never BREAKING on their own... default to RISK or
API_BREAK and are advisory/suppressible until a check earns its
FP-rate-gate corpus and is promoted" (ADR-035 D4). The smoke-test
assertions were widened to accept `COMPATIBLE_WITH_RISK` alongside
`NO_CHANGE`/`COMPATIBLE` rather than silently suppressing or re-gating
the check — per this repository's standing rule against trading a
flag for a real capability (ADR-068 D5). **Next step, if picked up**:
the model change above, or narrowing the check to require some other
corroborating evidence of genuine omission (unresearched; a naive
ratio/threshold heuristic is explicitly not the fix — see this file's
own "attempted twice, reverted twice" discipline for why an unvalidated
heuristic is worse than a documented gap).

### `--lang c++` explicitness not threaded to remaining fan-out callers (l0_export_delta/appcompat); dump/compare fixed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed for dump/compare CLI and the DumpRequest/CompareRequest API. `lang_explicit` is threaded through frontends/cli/commands/dump.py, compare.py, workflows/dump/{native,pe,macho}.py and workflows/artifact/*, and release_member_request.py now carries it too. The scan follow-up is moot (scan was deleted). appcompat and l0_export_delta are unverified. l0_export_delta.py takes a plain `lang: str` with no lang_explicit, so that follow-up looks still open.


`cli_dump_helpers.py`'s ELF `dump` path (and `service.py`'s two mirrors,
`_dump_elf`/PE-Mach-O's `_header_graph_lang`) all normalize the caller's
`lang` to `lang if lang == "c" else None` before calling `dumper.dump()` —
deliberately, per their own comment: "every format's own main pass
normalizes `lang` to only ever force a language explicitly requested,
letting auto-detection run otherwise (including for the default 'c++')",
so `_attach_header_graph`'s own `_clang_header_dump` call computes the
*identical* cache key and hits the in-process AST memo instead of paying a
second clang invocation. That assumption — "an explicit `--lang c++` and
auto-detection converge on the same result anyway, so unifying their cache
keys is free" — is false whenever the header itself is language-ambiguous:
`_resolve_force_cpp`'s auto-detection (`_detect_cpp_headers`/
`_detect_cpp20_headers`/an explicit `-std=c++NN`) requires the header to
contain *some* C++-only syntax, but a plain POD struct with no such syntax
(`struct Widget { int x; int y; };` — ordinary, real C++ code, e.g. a
value/DTO type) compiles as valid C too and auto-detects as C. Reproduced
end-to-end: `abicheck dump lib.so -H widget.h --ast-frontend clang --lang
c++` silently parses `widget.h` in **C mode** for the primary snapshot
(confirmed via the raw clang AST: a plain `RecordDecl`, not
`CXXRecordDecl`, no `definitionData` at all) — with no error, warning, or
any user-visible sign the explicit `--lang c++` was overridden — while the
*same* `dump`'s internal `_attach_header_graph` pass, reached through a
different `lang` derivation one call removed
(`abicheck/service.py:1281`/`abicheck/service.py:608`'s ELF branch shares
the identical `lang if lang == "c" else None` squash, so it too loses the
signal — the divergence traced here is specifically the ELF `_dump_elf`
helper's own second, ADR-050/G31-era call site,
`abicheck/cli_dump_helpers.py:1717`, versus `_clang_header_dump`'s
documented "an explicit `--lang c++`/`cpp` always wins" contract at the
function it's calling into — the squash happens one layer *above* that
contract, defeating it before it ever runs). Concrete, silent correctness
cost: `RecordType.is_standard_layout`/`is_trivially_copyable` (and any
other clang-only, C++-semantic-only fact — `_clang_record_type_traits`
itself documents "a plain C `RecordDecl`... genuinely absent", the correct
behavior *for C mode*, just not the mode the user asked for) silently read
`None` instead of a real value for a header that would parse identically
either way *except* for these C++-only facts, with the user's own explicit
override having no effect. **Not fixed here**: the squashing is not an
oversight — it is a deliberate, twice-Codex-reviewed cache/memo-consistency
design (see `abicheck/service.py:608`'s own comment) that this finding does
not invalidate for the *common* case (a header with real C++ syntax
auto-detects as C++ regardless, so squashing costs nothing there) — only
for the specific, real, silently-wrong case: an explicit `--lang c++` on a
syntactically-ambiguous header. A correct fix needs the squash itself to
stop conflating "auto-detect, and it happens to reach the same verdict as
the default" with "explicitly override, must not be silently downgraded to
a guess" — e.g. resolving `force_cpp` once, upstream of all three call
sites and their `_attach_header_graph` mirrors, and threading the
*resolved* boolean (or an unsquashed `lang` plus a corrected memo key that
hashes the resolved language rather than the raw flag) through consistently
— a change to a shared cache-key contract three call sites and two
Codex-reviewed comments currently rely on, not a one-line fix at any single
site. No test in the repository currently exercises this path: every
existing `is_standard_layout`/`is_trivially_copyable` regression test
(`tests/test_dumper_clang.py::test_parse_types_populates_standard_layout_and_trivially_copyable`,
`tests/test_diff_layout.py`) hand-builds the parsed AST or `RecordType`
directly, bypassing `dumper.dump()`'s CLI-level `--lang` resolution
entirely — closing this gap should add an end-to-end regression case in
the shape of `tests/test_clang_header_backend_integration.py`'s existing
siblings, verified against a real compiled library with an
intentionally-C-compatible C++ struct, once the fix itself is designed.

**Closed for the `abicheck dump` ELF CLI path — scoped narrower than the
"resolve `force_cpp` once, upstream of all three call sites" design sketch
above (G31 continuation).** Rather than a shared, force_cpp-boolean
cache-key contract spanning `cli_dump_helpers.py`, `service.py`'s
`_dump_elf`, and `service.py`'s PE/Mach-O `_header_graph_lang`, the actual
fix is narrower: only `cli_dump_helpers.py`'s ELF `dump` CLI path had a
real *internal* divergence between its own primary pass (squashed) and its
own `_attach_header_graph` call (raw, unsquashed) — `service.py`'s
`_dump_elf` and its `_header_graph_lang` computation already squash
*consistently* with each other (both feed the identical normalized value),
so `compare`'s implicit-dump path and the Python `service.run_dump` API
were never the site of this specific divergence; PE/Mach-O's own primary
pass (`_try_header_scoped_dump`) never squashed at all. What was missing
everywhere is the one bit no string-normalization scheme can recover on
its own: whether `--lang` was genuinely given on the command line, since
Click's own default for `--lang` (`LANG_DEFAULT`, `cli_options.py`) is the
identical string `"c++"` a real `--lang c++` produces. `dump_cmd` now
resolves this once via Click's own parameter-source tracking
(`click.get_current_context().get_parameter_source("lang") ==
click.core.ParameterSource.COMMANDLINE`) and threads a new
`lang_explicit: bool` keyword parameter through `perform_elf_dump`, which
derives one `_effective_lang` (the real `lang` when explicit or `"c"`,
else `None`) and passes that identical value to *both* the primary
`dump()` call and the `_attach_header_graph` call, instead of the primary
pass's own one-off squash and the graph pass's raw pass-through. This also
fixes a second, previously-undocumented half of the same divergence in the
*other* direction: on a plain default invocation (no `--lang`, still
`lang="c++"`), the header-graph pass previously force-parsed C++
unconditionally (since a non-empty `lang` was always treated as explicit
by `_resolve_force_cpp`), while the primary pass correctly auto-detected —
so even a default, no-flags `dump --ast-frontend clang` could already
silently disagree with itself between its own primary snapshot and its
own embedded header-graph. Verified end-to-end against a real compiled
library with an intentionally-C-compatible POD struct (`struct Widget {
int x; int y; };`, exactly this entry's own repro shape) through the real
`abicheck dump` CLI, not a hand-built AST or `RecordType` — see
`tests/test_clang_header_backend_integration.py::
test_cli_dump_explicit_lang_cpp_forces_cpp_mode_on_ambiguous_header`.
**Closed for `service.run_dump`/`DumpRequest`/`CompareRequest` in a later
pass, once a real conda-forge castxml build (0.7.0, within the
`>=0.6.11,<0.8.0` policy range — `castxml_policy.py`) was available in this
environment to verify against, alongside clang 18.** Rather than the
`lang: str | None = None` tri-state default this entry originally
sketched — a public-API *shape* change with a wide, hard-to-verify blast
radius across every `resolve_input`/`run_dump` caller (`compare`, `scan`,
`appcompat`, `l0_export_delta`, ...), most of which still legitimately pass
a concrete, Click-defaulted `lang` string that must keep auto-detecting —
the actual fix is the same **additive** `lang_explicit: bool = False`
parameter the CLI fix above already established, generalized one layer
up: `service.resolve_input`/`run_dump`/`_dump_elf`/`_dump_pe`/
`_dump_macho` and `service_header_scoped._try_header_scoped_dump` all gain
it (default `False`, a no-op — every existing caller's behavior is
bit-for-bit unchanged), `DumpRequest.lang_explicit`/
`CompareRequest.lang_explicit` carry it on the typed API, and
`service_dump_pipeline.run_dump_request`/`service_compare_pipeline.
resolve_compare_request` thread it through `resolve_side_snapshot` (their
one shared per-side resolution function) to `service.resolve_input`. The
`dump`/`compare` CLIs resolve it the identical way `dump_cmd` already did
— `compare_cmd` mirrors the established `_frontend_explicit`/
`_nostdinc_explicit` `ctx.get_parameter_source(...)==COMMANDLINE` pattern
already used one function over in `cli_compare_helpers._embed_inline_
source_side` for `--ast-frontend`/`--nostdinc`, extended to `--lang` and
threaded through `cli_resolve._resolve_compare_snapshots` into
`CompareRequest`. The whole-snapshot disk cache key
(`service_dump_cache._dump_cache_extra_key`/`cached_run_dump`) folds
`lang_explicit` in too — the identical `lang` string now legitimately
resolves to two different parsed ASTs depending on it, so a cache entry
for one must never serve the other. `scan`/`appcompat`/the
release/set-input fan-out/`l0_export_delta` are **not** touched by this
pass — they still pass their own already-resolved, Click-defaulted `lang`
string with `lang_explicit` defaulted `False`, so they keep their
pre-existing (unfixed, but also not regressed) behavior; wiring each is
the identical mechanical pattern applied here, left for its own follow-up
rather than expanding this pass's verified surface further. Verified
end-to-end against the same real, intentionally-C-compatible POD struct
through `service.resolve_input`, `DumpRequest`/`run_dump_request`,
`CompareRequest`/`resolve_compare_request`, and the real `compare` CLI
(Click parameter-source spy) — see
`tests/test_clang_header_backend_integration.py::
test_dump_request_and_compare_request_lang_explicit_forces_cpp_mode` and
`tests/test_service_dump_cache.py`'s
`test_differs_by_lang_explicit`/`test_lang_explicit_reaches_run_dump_and_keys_separately`.

### Opaque-type suppression still falls back to bare RecordType.name when stable identities are incomplete

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** compare/opaque_types.py:74-130 now uses OpaqueTypeIndex carrying stable EntityIds plus a bare-name 'local' tier. contains(strict=True) narrows the bare-name collision only when intersect() proves the pairing is complete, and otherwise falls back to the old bare RecordType.name matching. diff_filtering.py:854 _root_type_name now prefers Change.qualified_name.

`diff_filtering._find_opaque_types()` (and its
siblings `_find_by_value_types()`/`_downgrade_opaque_type_changes()`) index
a snapshot's opaque/impl-private types by `t.name` alone, and
`_root_type_name()` derives a `Change`'s matching key from `Change.symbol`
the same way `diff_types.py` stamps it — also the bare name for a
top-level type change (`name = t_old.name`), never `qualified_name`. Two
distinct records sharing a bare name in different namespaces (a complete,
genuinely public `api::Foo` and an unrelated forward-only `impl::Foo`)
therefore collide in the same `opaque: set[str]`: if `impl::Foo` is opaque,
`_downgrade_opaque_type_changes()` silently suppresses a real
`TYPE_SIZE_CHANGED`/field-change finding on the unrelated, complete,
public `api::Foo` too, since both match the bare key `"Foo"`. Confirmed
by reading the code (no live repro run); this predates PR #719 and
already applied identically to castxml's own `is_opaque=True` types — the
PR's clang-backend opaque-stub fix (previously clang silently dropped
every forward-decl-only type instead of emitting a stub) makes this
reachable from a new source, not a new bug class. **Not fixed here**: a
correct fix needs qualified identity threaded consistently through
`_find_opaque_types`/`_find_by_value_types`/`_downgrade_opaque_type_changes`
and `_root_type_name`'s several call sites in `diff_filtering.py` (at
least five, by grep), none of which currently have test coverage for the
cross-namespace-collision case to validate a change against — a
systematic, cross-cutting rework, not a scoped fix reactive to one review
comment. Filed here rather than attempted under this PR's time budget,
per this file's own "known gaps over risky reactive patches" convention.

### `dumper_clang.py`'s `parse_types()` conflates a C/C++ tag-namespace identity with an ordinary-namespace typedef identity that happens to share the same spelling — pre-existing, not introduced by PR #719's own changes, but investigated and confirmed reachable in this pass (Codex review, investigated, not fixed)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The code moved to extract/headers/clang/records.py:145, where identity = "::".join([*entry.scope, name]) still uses the anon-typedef fallback name. Tag and ordinary namespaces are not distinguished, so `struct Foo; typedef struct{} Foo;` still collides.

For a legal (if unusual) header like
`struct Foo; typedef struct { int x; } Foo;` — an unrelated forward-only
tag `Foo` and a SEPARATE anonymous struct given the ordinary-namespace
name `Foo` via typedef, which C's two-namespace rule keeps genuinely
distinct — `parse_types()`'s `identity = "::".join([*entry.scope, name])`
computation uses the same bare `name` for both (the tag's own `name`,
and the anonymous record's `anon_names`-derived typedef fallback name),
so the two collide into one `identity` key. Reproduced directly against
`_ClangAstParser`: only ONE `RecordType` named `Foo` is emitted (the
typedef-backed definition), and the unrelated opaque tag `struct Foo;`
is silently absent from the snapshot entirely — the exact regression
PR #719's own opaque-handle-type fix was written to prevent, just
reached through a different, adjacent mechanism (a spelling collision
across namespaces rather than declaration-order/redecl-set instability).
**Not fixed here**: closing it correctly needs the tag-namespace vs.
ordinary-namespace distinction threaded through the `identity` key
itself (and every downstream consumer that currently assumes `identity`
uniquely names one type — `_build_record`, the opaque/deprecated/kind
merge maps this same function already builds), which is a real, if
narrow, data-model change to a function this same PR already revised
three times this session (the opaque-stub fix, the kind-canonicalization
fix, and that fix's own regression fix) — each of which independently
needed careful re-verification against `dumper_clang.py`'s exact
2000-line hard cap. A fourth, differently-shaped change to the same
function under continued review pressure is exactly the risk profile
this file's own "known gaps over risky reactive patches" convention
exists to avoid; a correct fix needs its own dedicated pass with fresh
test coverage for the namespace-collision case specifically, not a
same-session extension.

### The castxml L4 source-ABI extractor does not fold the resolved EMULATED compiler's identity into either `fact_set.compiler_version` or the D8 TU cache key — attempted once for the persisted half, and REVERTED after a follow-up review caught it as a real regression, not a fix (Codex review, PR #719, three follow-up rounds)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** buildsource/source_extractors/castxml.py:700-738 still resolves cc_bin. compiler_version is _castxml_identity_with_stat_fallback(self.castxml_bin), and the comment at line 734 says a `cc_bin --version` probe is still future work.

castxml shells
out to the emulated compiler (`cc_bin`, resolved per compile unit by
`pick_compiler_binary` — the real build's own recorded `argv[0]`, absent
an explicit `--gcc-path` override) purely to discover its built-in
defines/include paths (`docs/learn/architecture.md`), so a header
conditional on `__GNUC__`/`_MSC_VER` can extract differently once that
compiler is upgraded at the same path, even though castxml itself and
its own `--version` probe stay identical — a real gap. The second
follow-up round's fix folded a STAT signature (`dev`/`ino`/`mtime_ns`/
`size`) of the resolved `cc_bin` into `fact_set.compiler_version`
(`_stamp_fact_set_and_coverage()` already has the per-TU `compile_unit`
in scope). The third follow-up round found this made things WORSE, not
better: those stat fields are filesystem-local, so (1) two TUs in ONE
surface resolved to DIFFERENT but same-toolchain drivers (`gcc` for a
`.c` TU, `g++` for a `.cpp` TU — an entirely ordinary mixed-language
build) get different suffixes purely from being different files on
disk, tripping `rollup_fact_set()`'s exact-equality check into
`fact_set_inconsistent` for a perfectly healthy, unchanged surface; and
(2) an identical build run on two different machines/paths (baseline
collected in one CI run, compared against another — an entirely
ordinary workflow) never shares device/inode at all, making every such
cross-machine comparison spuriously inconsistent and silently
suppressing every structured/opaque/source-edge finding. Between
"silently under-detect genuine toolchain drift" (the pre-existing gap)
and "spuriously suppress every finding on completely ordinary mixed-
language or cross-machine comparisons" (the attempted fix), the second
is strictly worse for typical usage, so the stat-based fold was
reverted rather than patched further under review pressure. **Not fixed
here, either half**: a correct fix needs a portable SEMANTIC identity —
a real `cc_bin --version` probe, normalized (mirroring
`_castxml_tool_version`'s existing shape, but for an arbitrary
gcc/clang/MSVC driver rather than castxml specifically) — not
filesystem stat fields, for the persisted half; the D8 cache-key half
additionally needs a wider, per-instance-hook-signature change to
`source_replay.py`'s shared cache-key infra (also used by
`ClangSourceExtractor`, whose analogous `--gcc-path` case resolves once
per extractor construction, not per TU, so its existing zero-arg hook
shape doesn't transfer directly), plus a decision on probing cost (a
version probe per distinct resolved `cc_bin` across a build with mixed
toolchains, cached the same way `_castxml_tool_version`'s `lru_cache`
already is). Left for a dedicated follow-up with its own MSVC-vs-
GCC-vs-Clang version-probe design, not a same-PR reactive patch.

### A compatible-but-ambiguous opaque redeclaration set (`class H; struct H;`) that later gains a same-key COMPLETE definition still produces a false `SOURCE_LEVEL_KIND_CHANGED` — investigated, not fixed (Codex review, PR #719, fourth follow-up round on this same area)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for kind_ambiguous in abicheck/ finds nothing, so RecordType has no provenance for an ambiguous forward-declaration kind.

The
earlier "keep the definition's own kind" fix (this same file, above)
deliberately never applies `dumper_clang.py`'s canonicalized opaque
`override_kind` to a record that survives as a COMPLETE definition — the
definition's own real, unmodified `_record_kind()` always wins, which is
correct in isolation (a real kind change on the definition itself must
never be hidden). But this means an identity whose opaque forward decls
were genuinely ambiguous (`class H;` AND `struct H;`, both present,
canonicalized to the fixed `"struct"` spelling per the kind-stability fix
above) reports "struct" in an old snapshot with no definition, while a
new snapshot adding a same-key `class H {...};` definition reports the
definition's real "class" — a spurious kind-change finding even though
"class" was always one of the two already-compatible, already-declared
keys. Reproduced directly against `_ClangAstParser`. **Not fixable at
the extraction layer**: an opaque snapshot has no way to know in advance
which of the ambiguous, compatible keys a LATER definition will use, so
no fixed canonicalization choice can match every possible future
definition. The generic comparison (`diff_types_abicc_parity.
_diff_type_kind_changes()`, shared across all producers) does a plain
`t_old.kind != t_new.kind` check with no notion of "this kind was
extracted from a genuinely ambiguous, unresolved forward-decl set" —
and correctly so, since blanket-suppressing every class↔struct
transition would hide the genuine, intentional ones this detector
exists to catch. Closing this properly needs new PROVENANCE carried on
`RecordType` itself (e.g. a `kind_ambiguous`-shaped field, schema-
versioned, populated by both header backends, read by the diff layer to
skip exactly this one shape of transition) — a cross-cutting model
change touching `model.py`, both backends, serialization, and the
detector, not a scoped fix reactive to one review comment, and
`dumper_clang.py`'s `parse_types()` has already been revised four times
in this same PR session for adjacent findings in this exact area. Filed
here per this file's own "known gaps over risky reactive patches"
convention rather than attempted under continued review pressure.

### `advanced_facts_collected` infers "advanced DWARF extraction ran" from its output rather than recording it

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** model/dwarf_facts.py:181 advanced_facts_collected still infers that extraction ran. The comment at line 229 says an explicit 'advanced extraction completed' status is still future work.

The predicate answers by checking
whether any field `dwarf_advanced.diff_advanced_dwarf` consumes is
non-empty, plus `target_arch` as a discriminator for a parse that completed
but established nothing (`dwarf_advanced` sets it from the ELF header;
the presence-only helpers leave it `""`). That is sound for every shape
reachable today, and it is mutation-checked per consumed family in
`tests/test_debug_evidence_presence.py`. But it is still an inference: the
honest signal is a **status**, not a reconstruction from findings.

The reason it matters is asymmetric. `checker.py` requires the predicate on
*both* sides, so any shape the inference gets wrong does not merely mislabel
coverage — it disables the `advanced_dwarf` detector wholesale and the run
silently misses real drift. That is a false negative, the worse direction,
and two such shapes were already found by review during one PR (the three
`toolchain` flag sets, then the successful-but-empty parse).

**The durable fix is an explicit "advanced extraction completed" field on
`AdvancedDwarfMetadata`**, set where the parse succeeds and persisted, with
the predicate reading it instead of inferring. Not folded into the fix PR
because it is a persisted-model change: a new field means a
`SCHEMA_VERSION` bump and a decision about how a pre-bump snapshot reads
(almost certainly "unknown", resolved conservatively), which this file's own
contract says gets its own ADR and migration rather than riding along with a
behavior fix. Until then the inference stands, and any new consumer of
`AdvancedDwarfMetadata` must be added to `_CONSUMED_ADVANCED_FIELDS` in that
test or it will silently disable the detector again.

### A pre-fix clang-backend baseline compares against a post-fix candidate as a wave of `FUNC_BECAME_INLINE` — no reliability flag guards `Function.is_inline`

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for inline_facts_reliable in abicheck/ finds nothing. diff_symbols.py:477 _check_inline_transitions has no reliability or producer gate.

The implicit-inline fix
(`extract/headers/clang/inline_semantics.py`) changed what the clang
header backend *records* for a `constexpr`/in-class/`= default`
declaration, from `False` to the correct `True`. A snapshot dumped with
the clang backend before that fix therefore carries a wrong value, and
`diff_symbols._check_inline_transitions` compares `is_inline` raw on both
sides with no producer or generation gate — so a stored baseline from
before the fix, compared against a freshly-dumped candidate, reports
`FUNC_BECAME_INLINE` (RISK) for every such declaration. The findings are
spurious: nothing about the library changed.

**Bounded, with a one-step remedy.** castxml is the default header
backend and always recorded these as inline, so only a baseline captured
under the opt-in `--ast-frontend clang` / `ABICHECK_AST_FRONTEND=clang` is
affected, and re-dumping that baseline clears it permanently. The findings
are RISK-class, never breaking, so no gate flips from pass to fail.

**The proper fix is the established one, and it is a schema change.** This
codebase already has the mechanism for exactly this situation — a fact
whose stored value is wrong in snapshots from an earlier generation:
`clang_restrict_facts_reliable`, `clang_va_list_facts_reliable`,
`param_kind_facts_reliable` and their siblings
(`model/snapshot_reliability.py`), each set `False` at load time for a
snapshot below a `_MIN_SCHEMA_VERSION_FOR_*` threshold from the affected
producer, and read by the consuming detector, which then declines rather
than fabricating a finding. Adding `clang_inline_facts_reliable` the same
way is the complete fix. It is **not** folded into the fix PR because it
cannot work without bumping `SCHEMA_VERSION` (a pre-fix snapshot is v46,
and so is a post-fix one — there is otherwise no way to tell them apart),
and this file's own contract is that a schema change gets its own ADR and
migration rather than riding along with a behavior fix. It also lands in
`policy/analysis_assurance_degraded_facts.py`'s `consulted_when` map,
whose every-flag-has-a-key invariant is enforced by a `KeyError` rather
than a check, and — if `is_inline` is converted to a `Fact[bool]` at the
same time, which is the `param_kind` precedent — in the storage backfill
rules too. Recorded here rather than attempted under the same review, per
this file's own convention.

### `Function.elf_binding`/`Variable.elf_binding` (and the pre-existing `elf_visibility` it mirrors) collapse mixed bindings across symbol-versioned aliases sharing one bare name — investigated, not fixed (Codex review, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** model/elf_facts.py:238-244 symbol_map is still `{s.name: s for s in self.symbols}`, last entry wins, which collapses versioned aliases.

`ElfMetadata.symbol_map` is a `name -> ElfSymbol` dict
built from `elf.symbols`, last-entry-wins; `elf_metadata.py`'s own parser
deliberately never embeds a version suffix in `ElfSymbol.name` (pyelftools
doesn't decode `@@`/`@` into the name either — see the `_pyelftools_exported_symbols`
comment), so two legally distinct versioned definitions of one exported
name — e.g. a `GLOBAL` `foo@GLIBC_2.2` and a `WEAK` `foo@@GLIBC_2.14` —
collapse to a single dict entry, and `_populate_elf_visibility` (which both
fields share) reads only that survivor. A `Function`/`Variable` whose
`mangled` matches such a bare name therefore reports whichever binding
happened to parse last, not "the" binding — and if that happens to be
`WEAK` while an older, still-live version was `GLOBAL`, a `binding: weak`
suppression rule (`suppression.py`) could match a removal that, from the
`GLOBAL` version's perspective, is a real break. Not fixed here: a correct
fix needs `symbol_map` (or a sibling index) to preserve every versioned
entry per bare name rather than collapsing to one — a change with several
other consumers beyond `elf_visibility`/`elf_binding` (`dumper_elf_symbols.py`'s
own callers, `diff_versioning.py`'s existing version-aware machinery, which
already correlates symbols against `versions_defined`/`versions_required`
through a different path) that deserves its own scoped design rather than a
drive-by change to a cached property several modules depend on today. Per
this file's own "known gaps over risky reactive patches" convention.

### type_base_changed declines on incomplete bases facts but still fires on a PRESENT-but-empty capture gap

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The vtable half is fixed. compare/base_class_diff.py:36-71 diff_bases now gates bases_fact/virtual_bases_fact through compare_facts and declines when a side is NOT_COLLECTED/FAILED/PARTIAL. A PRESENT-but-empty capture gap can still fabricate type_base_changed, as the entry describes.

A list-valued
`RecordType` field cannot express "not captured", so an empty-vs-non-empty
difference conflates a real change with one side's debug info simply not
covering the declaring TU. Confirmed for `vtable`: identical headers, no
DWARF vtable, and zero `_ZTV` symbols on either side still produced a
`BREAKING` `type_vtable_changed`, because `_diff_type_vtable` guarded on
`t_old.vtable == t_new.vtable` and nothing else. Fixed by requiring an
independent layout signal (a size change — the vptr a genuinely polymorphic
class gains — or a virtual-base change) before an empty↔non-empty
transition is reported; both-sides-captured differences are untouched, and
an unknown size on either side keeps the finding, since the suppression
needs positive evidence that layout held still rather than being a fallback
for missing information. This is the discipline `diff_vtable_layout.py`
(tri-state `None`, "degrading to B1's L0 view rather than fabricating a
break") and `diff_elf_layout.py` (compare only a `_ZTV` present on *both*
sides) already state in their own docstrings; the type-level detector had
neither. **Two things remain open.** (1) `_diff_type_bases`
(`set(t_old.bases) != set(t_new.bases)`, and its virtual-base half) has the
same unguarded shape and **stays that way — an attempted guard was written
and reverted before merge, and the reason is worth not rediscovering.**
Every layout-based premise for it is false: an *empty* base is invisible by
the empty-base optimization, and — the one that killed the attempt — a
*storage-contributing* base can be added without moving the derived class's
size at all when the class is over-aligned (verified against g++:
`struct alignas(8) D {}` and `struct alignas(8) D : B {}` with
`struct B { int y; }` are both 8 bytes, as are the `alignas(16)` pair at
16). So "size held still" proves nothing about a base list, in either
direction. Unlike the vtable case there is no independent evidence stream
to fall back on — `snapshot.functions` answers "did this class's virtuals
change" but nothing answers "did this class's bases change" except
`RecordType.bases` itself. Guarding it therefore needs evidence the model
does not currently carry (per-finding provenance, or a captured base-layout
fact such as `base_offsets` corroboration), not a cleverer reading of
`size_bits`. Until then a fabricated `type_base_changed` from a capture gap
is the accepted cost, because the alternative — suppressing a real
hierarchy change, which is sometimes the *only* breaking finding a
same-size base addition produces — is strictly worse. (2) The vtable guard is **narrower than
a first reading suggests, and its own docstring used to overclaim it.** The
class's virtual functions and its `RecordType.vtable` are two projections of
the same DWARF evidence (both trace to `DW_TAG_subprogram`), not independent
streams — so a translation unit whose coverage vanishes can take both, the
two sides' signature sets then differ, and the guard declines to suppress.
The false positive survives in that shape. That is the failure direction to
have (it leaves a pre-existing false positive standing rather than hiding a
real break), but it means the guard covers the *reported* case — identical
headers, no DWARF vtable, no `_ZTV` anywhere, virtuals absent from both
sides — and not every capture gap. Closing the rest needs artifact or
provenance evidence (`_ZTV` presence, per-finding providers) the type-level
detector does not receive today. (3) One accepted
false negative in the
fix itself: a class already polymorphic *through a base*, declaring no
virtuals of its own, that gains one — its vtable grows while its object
size does not, making it indistinguishable from capture noise without a
real polymorphism walk over both sides' base chains
(`diff_vtable_layout._is_polymorphic`) plus the per-finding provenance the
entry below describes. (4) A *pure* virtual reaches that same size backstop
for a different reason: it has no out-of-line definition, so
`dwarf_snapshot` drops its declaration-only DIE from `snapshot.functions`
while still counting it as a vtable child of the class, leaving both
owned-signature sets empty — and with `alignas` absorbing the new vptr the
size does not move either. Reproduced against g++
(`struct alignas(8) A { virtual void f() = 0; }` compiled alongside a
concrete derived class, which is what makes GCC emit A's complete DIE
rather than a `DW_AT_declaration` stub): old vtable `[]`, new vtable
`['_ZN1A1fEv']`, both 8 bytes, `_ZN1A1fEv` absent from the function map on
both sides. **An attempt to close this one was written, merged into the
branch, and then reverted — the reason is the most useful thing in this
entry.** The fix consulted `RecordType.vptr_offset_bits` as "the layout
descriptor's own witness, the only signal here that is not another
projection of the same subprogram DIEs." That claim was false, and
checkably so: **both** producers assign the field as `0 if vtable else
None` (`dwarf_snapshot.py`, `dumper_castxml.py`), so on every real DWARF
or CastXML snapshot `(old.vptr is None) != (new.vptr is None)` is
*identical to* the empty↔non-empty vtable transition being guarded. It was
therefore true by construction for every input that reached it, which did
not merely weaken the guard — it made the guard **inert**, restoring the
original capture-gap false positive in full (Codex review). Only the
optional `ABICHECK_CLANG_LAYOUT_TOOL` path computes a real vptr, and
nothing in the model distinguishes that value from the derived one, so the
field cannot be trusted here at all. Reverted; case (4) joins case (3) as
an accepted false negative. **The tests are the second lesson**: the
guard's unit tests built `RecordType`s with `vptr_offset_bits` left `None`
on both sides — a record no backend can emit — so the entire suite,
including the pre-existing test for the guard's whole purpose, passed
against a guard that suppressed nothing on real input. The helper now
derives the field the way the producers do, one test pins that derivation
in the producers' own source (so the reasoning fails loudly if a producer
ever computes a real vptr), and five of these tests fail against the
reverted revision. Worth recording for both (3) and (4) that the break is
**not** hidden: `diff_layout._check_vptr_introduced` fires independently
on the same `None → 0` transition and the verdict stays `BREAKING`
(verified end to end, and now pinned by its own test) — which is also why
the FP-rate corpus, a verdict-level gate, could not catch either the
original false positive or the inert-guard regression, and why the
regression coverage is a direct unit test on the predicate
(`tests/test_vtable_evidence_guard.py`) instead. Leaning on a sibling
detector remains uncomfortable, but the alternative on offer was a witness
that only appeared independent. Guarded by four FP-corpus cases under the
`evidence-absence` axis (one FP guard, three FN sentinels), the unit tests
above, and three Hypothesis properties in
`tests/test_detector_properties.py`.

**A named follow-on, found by a user rather than by any gate in this
repo, and worth being explicit about why: two detectors reading the
identical evidence gap and reaching two different severities is its own
bug class, distinct from either detector being individually wrong.**
`LAYOUT_UNVERIFIABLE` (`diff_layout.py`) answers "was there real layout
evidence for this type" from the exact same asymmetric-evidence condition
`_vtable_transition_is_evidenced` above declines to suppress on. Both
answers are individually correct and individually documented (this entry
is the vtable side's own justification) — but a type carrying both at
once reads as a hard BREAKING verdict sitting next to a finding that
already says the evidence for that type couldn't be verified either way,
reproducible with zero real ABI change (scanning a binary against a dump
of itself; reported against real oneDAL binaries, three types, two
libraries). None of this repo's existing quality gates were positioned to
catch it: the FP-rate/tier-accuracy gates are verdict-level (the overall
verdict was already correctly `BREAKING`, dominated by the vtable
finding, so nothing there looked wrong), and every existing test —
including the Hypothesis properties this entry already describes — is
scoped to one detector's own correctness in isolation, never to whether
two detectors' outputs are mutually *legible* when they land on the same
subject.

**The fix went through four designs before landing, and the last pivot is
the one worth reading closely — it generalizes past this one pair of
detectors.** (1) Demoting `TYPE_VTABLE_CHANGED` was tried first and
rejected immediately: a real last-virtual-method removal with a new-side
capture gap is indistinguishable from this same evidence gap, so
softening its severity risks a false negative on a genuine break. (2)
Folding the now-redundant `LAYOUT_UNVERIFIABLE` finding into
`redundant_changes` unconditionally was tried next, and review found four
real defects in it in turn: correlating on bare `Change.symbol` (two
distinct same-named records in different namespaces can share it, so
fold on `qualified_name`); folding on mere co-occurrence rather than a
marker recording *why* the stronger finding was kept (an
independently-evidenced `TYPE_VTABLE_CHANGED` — a real reorder, a real
size delta, a real virtual-base change — must never trigger the fold);
reusing `Change.modulation_reason`/`modulation_rule` for internal
signaling (those are a public, report-facing audit trail for an actual
verdict override — tagging an unmodulated finding with them is a false
audit entry a downstream consumer would read as real); and running the
fold *before* `FilterNonPublicSurface`/`ApplySuppression` had settled
both findings, which both hid the redundant finding from a suppression
rule targeting it directly and let it outlive its covering finding's
later exclusion. (3) Fixing all four and gating the fold on a
policy-resolved-verdict subsumption check (`checker.
_vtable_gap_finding_is_verdict_subsumed`) closed the `PolicyFile`-override
case, but review found the fix was still solving the wrong layer: **the
severity-scheme axis (`severity.SeverityConfig` — `abi_breaking`/
`potential_breaking`/etc., each independently `error`/`warning`/`info`)
is chosen by the CLI/reporter caller entirely *after* `compare()`
returns, so no compare()-time decision to remove a finding from
`changes` can ever be correct for it.** Concretely: a caller configuring
`abi_breaking=info` / `potential_breaking=error` wants
`LAYOUT_UNVERIFIABLE`'s error-level contribution to reach
`severity.compute_exit_code`, but that function reads `result.changes`
directly — if the fold already removed the finding because the
*policy* axis (a completely orthogonal gate, resolved before
`compare()` returns) ranked the covering `TYPE_VTABLE_CHANGED` as more
severe, the severity axis never gets a chance to see it, and this
reproduces with **zero policy override involved** (Codex review, fresh
evidence). The same review round also found the fold's pipeline
ordering relative to `DemoteUnreachableInternalChurn` (which runs later,
to demote a confirmed-unreachable internal-namespace type) could orphan
an already-made fold decision when the covering finding was itself
demoted afterward. (4) **The only structurally-safe fix was to stop
removing information from `changes` for this reason at all.**
`post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged`
leaves both findings exactly where they always were and only sets the
already-existing, generic `Change.correlated_change_kind` field (reused
from its original ADR-041 purpose, not a new one) on the redundant
`LAYOUT_UNVERIFIABLE` finding, pointing at `"type_vtable_changed"`.
`Change.vtable_covers_unverifiable_layout_gap` (set in
`diff_types._diff_type_vtable`) still records which `TYPE_VTABLE_CHANGED`
findings rest purely on the ambiguous evidence gap versus real,
independent evidence — that detection logic is exactly what survived all
four designs unchanged; only the *action* taken on it changed. This
design is immune by construction to every one of the defects above and
to any future one shaped like them, because nothing is ever hidden from
a consumer that reads `changes` — there is no "was this the right time
to remove it" question left to get wrong. **The general principle this
leaves behind: never remove a finding from a shared, multiply-consumed
list (`DiffResult.changes`) to resolve what is fundamentally a
*presentation* problem (two findings reading as contradictory) when that
list has independent downstream consumers — a legacy verdict, a
policy-file override, a severity-scheme exit code, a future pipeline
step — that this fix cannot fully enumerate and whose configuration is
chosen after the removal decision was made. Annotate; never remove.**
Regression coverage: `tests/test_vtable_severity.py`'s
`TestLayoutUnverifiableCorrelatedWithVtableChanged` (hand-picked example
cases, including one exercising the severity-axis gap directly via
`severity.compute_exit_code`) and a generalized Hypothesis property,
`test_layout_unverifiable_always_correlated_when_vtable_change_shares_its_gap`
in `tests/test_detector_properties.py`, checking the annotation holds
over arbitrarily generated classes and evidence-descriptor shapes.

**A whole-class structural fix was attempted mid-way through the fold
design (2) above, for the field-ordering half of it, and reverted — the
in-repo audit that justified it was the wrong audit.** (This sub-entry
predates the pivot to design (4) and is kept because the lesson is
independent of which design won.) The field-ordering bug (found twice
now: first on `AbiSnapshot` in PR #582, again on the new `Change` field
this fix originally added mid-list) was first "fixed" by making `Change`
keyword-only from `old_value` onward via the `dataclasses.KW_ONLY`
sentinel, on the strength of an AST-parse of every `Change(...)` call
site in this repo confirming every positional call anywhere passes
exactly the three required leading fields and never more. That audit
answers the wrong question for a type this codebase itself documents as
public API (`checker_types.py`'s own module docstring; CLAUDE.md:
"changing their public surface is a breaking change to the Python API —
coordinate it"): it proves this repo's own call sites are safe, and says
nothing about an external consumer who previously called `Change(kind,
symbol, description, old_value, new_value, ...)` positionally — for whom
the whole-class sentinel is exactly the same breaking shift the fix
exists to prevent, just moved to a boundary this repo cannot see or test
(Codex review, fresh evidence). Reverted in favor of the same per-field
`field(kw_only=True)` PR #582 already used for `AbiSnapshot`, applied
only to the one new field (`vtable_covers_unverifiable_layout_gap`) and
appended at the true end of the dataclass (not mid-list) so every
pre-existing field's position — and therefore every pre-existing
positional caller's behavior, in or out of this repo — is completely
unchanged. The lesson generalizes beyond this one field: for a type
documented as public API, "no in-repo caller breaks" is not the bar:
prefer the narrowest change that cannot break a caller this repo cannot
see, even when a broader structural fix looks more complete. The
`KW_ONLY` sentinel is still the right tool for a genuinely *internal*
dataclass with no external-construction contract (nothing here argues
against it in general) — the mistake was applying it to one that has
such a contract without checking for one first.

### Type reachability (direct vs. transitive stdlib references) — computed and wired into `diff_types.py`'s RecordType-based detectors; enum/typedef paths remain unwired

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** type_reachability.py is wired into the RecordType detectors (diff_types_surface.py and others), but the diff_types.py:1113-1118 enum paths still call _is_non_abi_surface_type(e.name) directly, as the entry says.

`abicheck/type_reachability.py`
(`directly_referenced_stdlib_types()`) computes, from a snapshot alone,
which `std::`/`__gnu_cxx::`/etc. record types are directly referenced by
a non-stdlib function's signature or a non-stdlib type's own field — as
opposed to only reachable via deep template-instantiation internals
(`std::string::_Alloc_hider`, `std::_Rb_tree_node_base`) that
`is_non_abi_surface_type`'s existing whole-name-prefix filter already
correctly excludes as toolchain-artifact churn either way. A Codex review
round found and fixed a real correctness gap in the computational claim:
candidate identification originally matched only `RecordType.name`, but
castxml/direct-clang populate the bare leaf there and the
namespace-qualified spelling separately in `qualified_name` (`model.py`,
`dumper_clang.py`) — so `name` alone never carries a `std::` prefix for
those two backends and the helper silently found nothing on any real
castxml/clang-produced snapshot. Fixed by identifying candidates via
`qualified_name or name`. That fix alone was still insufficient, confirmed
by dumping a real compiled `std::vector<int>` parameter end to end:
`Function.return_type`/`Param.type` spell the outer type **bare**
(`"vector<int, std::allocator<int> >"`) even when the matching
`RecordType`'s identity is fully qualified
(`"std::vector<int, std::allocator<int> >"`), across *all three* backends
(DWARF bakes the qualified form straight into `name` with no separate
field; castxml/clang keep `name` bare and `qualified_name` separate) — so
a pure full-identity substring match still couldn't connect the two.
Fixed by also generating a namespace-prefix-stripped spelling per
candidate and matching against either form. **Since resolved** (a later
pass, user-requested): a signature spelled with a typedef alias
(`std::string`, `std::wstring`, ...) names the alias, not the real
underlying class (`std::basic_string<char, ...>`) that owns the
`RecordType` entry — no current model field maps one back to the other
directly, but `snapshot.typedefs` does carry the alias → target mapping.
Verified empirically against a real DWARF-dumped `std::string`
parameter: `snapshot.typedefs["std::string"]` resolves to the bare
`"basic_string<char, std::char_traits<char>, std::allocator<char> >"`,
while the owning `RecordType.name` is the fully-qualified
`"std::__cxx11::basic_string<char, std::char_traits<char>,
std::allocator<char> >"` — libstdc++ wraps its own post-C++11 dual-ABI
types in an inline namespace (`__cxx11::`) the exact same way libc++
wraps its whole standard library (`__1::`/`__ndk1::`, already handled),
so that inline-namespace-stripping list gained a third entry. The
typedef *key* itself needed the identical bare-vs-qualified treatment
already applied to `RecordType` identities (the DWARF backend spells the
signature with the bare form `"string"`, never the qualified typedef key
`"std::string"`), so `_typedef_spelling_targets()` builds a
`spelling -> target` index covering both the literal key and its
namespace-stripped bare form (dropped instead of recorded when
ambiguous, same false-negative-over-false-positive principle as the
`RecordType` spelling index), and `_scan()` now follows a matched
typedef alias to its target the same way `surface.py`'s own
reachability closure does. What this does *not* cover: a stdlib alias
the producing backend never emitted into `snapshot.typedefs` at all (no
empirical case of this found across the three backends so far, but
nothing guarantees one couldn't exist) — that residual case degrades
silently back to "not directly referenced," the same conservative
false-negative default this whole module already uses throughout.

**Two more real gaps found and fixed in the same pass** (Codex review,
fresh evidence): (1) the non-stdlib bare-alias fallback derived a
record's unqualified spelling via `identity.rsplit("::", 1)`, which
splits inside a *template argument's own* qualified name rather than at
the outer namespace boundary — for `"api::Wrapper<dep::Tag>"`, the
lexically last `"::"` belongs to the template argument `dep::Tag`, not
the outer namespace path, so the old code derived the corrupted bare
form `"Tag>"` instead of `"Wrapper<dep::Tag>"`, and a real dumper
backend's bare signature spelling for that wrapper then never matched
anything. Fixed with a new `_bare_type_name()` that tracks `<`/`>`
nesting depth and only treats a `"::"` at depth zero as a namespace
separator. (2) stdlib and non-stdlib spellings were matched via one
*combined* compiled pattern in a single non-overlapping `finditer()`
pass — when a non-stdlib record's own identity embeds a stdlib type's
spelling verbatim (e.g. a template instantiation `"Wrapper<std::string>"`
registered as its own record identity), and a public signature names
that wrapper's full identity exactly, the combined pattern's
longest-first alternation matches the whole wrapper span first,
consuming it — since regex matches never overlap, the nested
`"std::string"` substring inside that same span was never independently
found, even though it is directly present in the public signature text.
Fixed by splitting `_spelling_index()` into two independent indices
(stdlib vs. non-stdlib/record) with two independently compiled patterns
scanned separately over each declaration, so one pattern's match can
never mask the other's.

**A third real gap found in the same pass** (Codex review, fresh
evidence): both the stdlib-stripping collision guard (in
`_spelling_index`) and the typedef-key stripping collision guard (in
`_typedef_spelling_targets`) checked a stripped spelling only against
*full* non-stdlib record identities, not against the bare
(namespace-unqualified) alias a real backend actually spells that record
with. A non-stdlib record like `api::vector<int>` is spelled bare as
`"vector<int>"` — the same bare spelling `std::vector<int>` reduces to
after namespace-stripping — so a signature naming the unrelated user
type by its bare spelling incorrectly marked the real `std::vector<int>`
as directly referenced too; the identical gap existed one level up for
`api::string`/`"std::string"`'s typedef key. Fixed with a new
`_non_stdlib_signature_spellings()` helper (full identity plus bare
alias — deliberately keeping an ambiguous bare alias that
`_spelling_index`'s own `record_index` drops, since it's still a real
spelling *some* non-stdlib record can be named by) shared by both
collision guards.

**A fourth finding pointed one level deeper, into shared infrastructure
this module calls rather than into `type_reachability.py` itself**
(Codex review, fresh evidence): `diff_cxx_rules.owner_class_of()` — the
helper this module's owner-class seeding reuses, also used by
`diff_symbols.py`'s owner-based move detection, `diff_cxx_rules.py`'s
own member-move heuristics, and `surface.py`'s reachability closure —
mis-parses a public conversion operator's owner when the operator's own
target type is namespace-qualified. Confirmed against a real compiled
and demangled symbol: `struct Foo { operator ns::Bar() const; };`
demangles to `"Foo::operator ns::Bar() const"`, and abicheck's own
`Function.name` (after its existing signature-stripping step) is exactly
`"Foo::operator ns::Bar"`. The old naive `rsplit("::", 1)` split at the
*lexically last* `"::"` — which belongs to the operator's own qualified
target (`ns::Bar`), not the owner/member boundary — producing the
corrupted owner `"Foo::operator ns"` instead of `"Foo"`, so a public
conversion operator to a qualified type would never seed its owner
class, potentially hiding a genuine layout break in one of the owner's
fields. Fixed in `owner_class_of()` itself (not duplicated locally) by
locating the literal `"::operator "` marker — present only for a
conversion-to-named-type operator, never for a symbol operator like
`operator+`/`operator[]`, which has no target type to separate from the
keyword with a space — and splitting there when present, falling back to
the previous behavior otherwise. Fixing the shared helper directly
(rather than working around it only in `type_reachability.py`) also
corrects the same latent mis-parse for its other three callers, since
none of them could have been relying on the old behavior's output for
this input shape without already being wrong.

**A fifth finding, on the same owner-seeding feature, investigated and
deliberately not implemented this pass:** a public method whose dumper
backend recorded only a bare member name (CastXML's convention — "the
bare `bar` rather than `C::bar`", per `owner_class_of()`'s own
docstring) on a class-template specialization falls through to
`owner_class_of()`'s mangled-name fallback
(`itanium_scope_components`), which — confirmed empirically
(`itanium_scope_components("_ZN3FooIiE3barEv")` returns
`["FooIiE", "bar"]`) — deliberately keeps the **raw, undemangled**
Itanium template-argument encoding (`"FooIiE"`) rather than the spelled
form (`"Foo<int>"`) a real `RecordType` identity actually uses; that
design choice is itself intentional and documented in
`itanium_scope_components`'s own docstring ("the raw template-argument
encoding is kept so distinct specializations stay distinct"), since its
other callers use it for grouping/distinguishing specializations, not
for matching against demangled model spellings. `type_reachability.py`'s
owner-seeding then feeds this raw string into `_scan()`, which correctly
finds no match (a silent false negative — the same
false-negative-over-false-positive default this whole module already
uses throughout, not a new failure mode). A real fix has two paths, both
rejected as out of scope for a drive-by extension here: (1) making
`owner_class_of()` itself resolve raw template encodings to spelled
form would mean invoking the real demangler (`demangle.py`'s
`demangle()`, which shells out to `c++filt`/`cxxfilt` on a cache miss)
from a hot path every one of its four callers shares, directly
contradicting `itanium_scope_components`'s own stated design rationale
("avoids any dependency on an external demangler ... so this works
identically on Linux, macOS, and Windows and never shells out"); (2) a
narrower, local-only translation in `type_reachability.py` (demangle
just `fn.mangled` when `owner_class_of()` took the mangled-fallback
path, then re-derive the owner from the *demangled* qualified name)
would need a genuinely new depth-aware "class::member" boundary splitter
for demangled text — not a reuse of `_bare_type_name` (which strips a
*leading* namespace qualifier, the opposite half of this problem) — and
would have to correctly compose with the already-fragile
`"::operator "` marker special-case from the fourth finding above (a
demangled conversion operator on a qualified template specialization
could combine both edge cases at once), which is exactly the kind of
compounding-edge-case complexity this file's own docstring already
flags as needing "its own scoped follow-up," not a reactive patch.

**Two more real gaps found and fixed in the same pass** (Codex review,
fresh evidence): (1) A real backend does not always spell a nested type
as either the fully-qualified identity or the fully-bare leaf —
confirmed empirically via `clang -ast-dump` on `namespace api { struct
Outer { struct Inner {}; }; Outer::Inner g(); }`: direct-clang prints
the return type as exactly `"Outer::Inner"`, dropping the enclosing
namespace (`api::`) while keeping the class-nesting qualifier
(`Outer::`). Neither the full-identity match nor the single
fully-bare-leaf match (`_bare_type_name`) covered this partial
qualification. Generalized `_bare_type_name` into
`_namespace_suffix_spellings()`, returning every suffix obtainable by
dropping some prefix of the scope chain at each depth-zero `"::"`
boundary, and updated all three call sites to register every suffix
(same ambiguity-drop collision guard extended to each). (2)
CastXML/direct-clang record a function or namespace-scope variable's
own display name bare (e.g. `"touch"`, never
`"__gnu_cxx::Node::touch"` or `"std::touch"`), so the existing
`name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)` guard cannot catch a
retained, seemingly-public declaration that is actually part of the
standard library itself — verified with two real Itanium
mangled-symbol repros (a namespace-scope stdlib variable and a stdlib
free function) that both incorrectly marked `std::string` as directly
referenced before the fix. Fixed by also checking the declaration's
recovered qualified name (`diff_cxx_rules.itanium_qualified_name`, from
`mangled`) against the stdlib prefixes for both functions and
variables — which subsumes the narrower owner-only check from the
fourth finding above (a stdlib-prefixed owner always makes the full
qualified name stdlib-prefixed too, but not vice versa: a stdlib
namespace's own direct free function/variable is a single mangled
scope component, so `owner_class_of` returns a bare `"std"` with no
trailing `"::"`, never matching the `"std::"` prefix string), so the
now-redundant owner-only guard was removed.

**A sixth finding found a different shape of gap again: an owner-seeding
correctness bug, not a missing-spelling one.** `owner_class_of()`
derives its result by chopping the trailing `"::"`-component off *any*
already-qualified declaration name or mangled-symbol scope chain, with
no way to tell — from the string alone — whether what remains is really
an enclosing *class* or just an enclosing *namespace* (Codex review,
fresh evidence, confirmed with a minimal repro): a public namespace
function `api::run()` makes `owner_class_of` return the bare namespace
fragment `"api"`, which the general suffix-matching mechanism
(`_namespace_suffix_spellings`, added for the first finding above) could
then coincidentally match against an unrelated internal record's own
bare-suffix spelling (e.g. `other::api`), wrongly walking that record's
fields and unfiltering its layout churn. Fixed by seeding an owner only
on an *exact* match against a non-stdlib record's full identity —
bypassing `_spelling_index`'s `record_index`/suffix mechanism entirely
for this specific seed, rather than routing it through `_scan()`. This
is safe rather than a regression risk: unlike a genuine signature type
spelling (which a backend can legitimately partially-qualify, per the
first finding), `owner_class_of`'s result is always either the complete,
exact scope chain of a real class (both its already-qualified-name path
and its mangled-decomposition fallback reconstruct the *full* chain,
never a partially-elided one — DWARF always bakes the complete
namespace/class path into a qualified name, and Itanium mangling always
encodes the complete nested-name unambiguously) or, when the function
isn't actually a method, namespace noise — so restricting to exact
matching loses no real case while closing the false-positive collision.
While verifying this fix through the full `compare()` pipeline (not just
the unit level), the same class of bug was found to independently exist
in `surface.py`'s `compute_public_surface()` — its own, separate
`owner_class_of`-based seeding (`_seed_public_roots`) feeds the raw
owner through `_type_identifiers()` into `seed_types`, and
`_walk_type_closure()`'s `record_by_name` lookup is *itself* keyed by
bare-tail aliases (an intentional, correct mechanism for genuine type
references — "a short alias reached inside its own namespace resolves
to the namespaced record"), so the identical `"api"` vs. `other::api`
collision reproduces there too, confirmed with the same minimal repro
(`compute_public_surface` marks `"api"` — and therefore `other::api` —
public). **Deliberately not fixed in this pass**: `surface.py` is a
different, foundational module (the public-surface-scoping gate every
other detector in the codebase depends on) that this PR never otherwise
touches, and unlike the narrow `type_reachability.py` seeding path, its
`record_by_name` bare-tail lookup is a *shared* mechanism relied on by
every other seed type too — restricting it for the owner case
specifically needs its own careful, independently-verified design (which
seed paths may legitimately need the ambiguous-tail lookup and which
must not), not a same-PR drive-by extension of an unrelated finding.

**Two more ambiguity-tracking gaps found in the same collision guards**
(Codex review, fresh evidence, both confirmed with minimal repros): (1)
when two non-stdlib records had identities `"Inner"` and `"api::Inner"`,
`_spelling_index`'s derived-suffix collection only counted contributors
to the *derived* suffix `"Inner"` (from `"api::Inner"`) — the unrelated
global `"Inner"` identity never contributes to that same tracking
structure (it's already a full identity, not a derived suffix), so the
ambiguity count saw only one contributor and merged `"api::Inner"`
straight into the pre-existing full-identity entry for the global
`"Inner"`. Fixed by also treating a derived suffix that collides with a
*different* record's own full identity as ambiguous. (2)
`_typedef_spelling_targets` gave an *exact* pre-existing typedef key
automatic priority over a derived suffix from a different key, rather
than tracking both through the same ambiguity-counting structure: when
`snapshot.typedefs` held both a global `"Alias" -> "std::…"` and a
qualified `"api::Alias" -> "Foo"`, a declaration inside `api` can
legitimately spell the latter as bare `"Alias"` too — silently
preferring the pre-existing exact key could resolve it to the wrong
one. Fixed by unifying exact keys and derived suffixes into one
target-set-per-spelling structure, resolving a spelling only when every
contributing source agrees on exactly one target.

**A follow-up review round on the same fix found the removal above was
necessary but not sufficient.** Refusing to *merge* `"api::Inner"`'s
candidates into the pre-existing `record_index["Inner"]` entry still
left that entry pointing at the unrelated global `"Inner"` record
(Codex review, fresh evidence, confirmed with a minimal repro):
direct-clang's own "drop the enclosing namespace" convention (the same
mechanism `_namespace_suffix_spellings` models for the `Outer::Inner`
finding above) means a signature declared *inside* namespace `api` can
spell `api::Inner` bare as `"Inner"` too — not just a partially-qualified
form. A public `api::f()` returning (bare-spelled) `api::Inner` would
then have its `std::` field misattributed to the *unrelated* global
`Inner`'s own field instead of correctly failing to resolve. Fixed by
removing the colliding spelling from `record_index` entirely
(`record_index.pop(bare, None)`) rather than merely refusing to add the
other record's candidates to it — since the bare spelling is genuinely
ambiguous between both records, leaving it resolved to either one
(including the "already there by default" one) is the wrong outcome,
not just an incomplete fix.

**A separate, deeper finding on typedef keys, investigated and
deliberately not implemented this pass:** direct-clang's own
`parse_typedefs()` (`dumper_clang.py`) stores a typedef's bare
`node["name"]` as the `snapshot.typedefs` key — never the scope-joined
qualified form `_qualified()` uses for every other decl kind — so a
namespaced alias loses its namespace at the point the snapshot is
produced, not merely at the point this module reads it. Confirmed
empirically via a real `clang -ast-dump` on `namespace api { struct Foo
{}; using Alias = Foo; } api::Alias make();`: the `TypeAliasDecl`'s own
name is bare `Alias`, while the function's return type is printed fully
qualified `"api::Alias"` (a typedef reference is always spelled
qualified by clang's printer, unlike a plain class reference) — meaning
`snapshot.typedefs` ends up with `{"Alias": "Foo"}` while the real
signature spells `"api::Alias"`, the exact inverse of the
qualified-key/bare-signature shape `_typedef_spelling_targets` was built
to handle. Since suffix-stripping only ever produces a *shorter*
candidate from a key, it can never reconstruct a *longer*, more-qualified
spelling from an already-bare key — there is no string-level fix
possible in this module for this direction, only two heavier ones, both
out of scope for a drive-by extension here: (1) fixing
`dumper_clang.py`'s `parse_typedefs()` to store the qualified key
instead — a genuine, separate producer-side bug, but one whose blast
radius reaches every other consumer of `snapshot.typedefs` (typedef
diffing, `surface.py`'s own typedef-following in `_walk_type_closure`),
each needing its own re-verification against the FP-rate/mutation-score
gates before trusting a changed key shape; (2) a local reverse-namespace
guesser in this module (re-attaching every namespace prefix seen among
the snapshot's own record identities to a bare typedef key and hoping
one matches) — pure speculation with no way to verify which, if any,
namespace a given bare key actually belongs to, and a real risk of
fabricating new false-positive matches rather than closing a
false-negative gap. Left as a silent false negative — the same
conservative default this module already uses throughout.

**A seventh finding pointed at a platform-specific mangled-name quirk,
silently disabling the mangled-scope-recovery guard on every Mach-O
snapshot.** Confirmed via `dumper_clang.py`'s own `_visibility()`
docstring: clang's `mangledName` carries an extra platform leading
underscore on macOS (`"__ZN3lib3addEii"`, not the plain Itanium
`"_ZN3lib3addEii"`), and empirically: `itanium_scope_components(
"__ZSt5touchv")` returned `None` before this fix, since
`_itanium_strip_prefix()` only recognized the bare `"_Z"` prefix
(Codex review, fresh evidence). Since every declaration's stdlib-scope
check in this module (and `owner_class_of()`'s mangled fallback) relies
on this recovery, a bare-named stdlib declaration on macOS bypassed the
guard *entirely* — not just in the one edge case a synthetic unit test
would reach, but for every symbol on that platform. Fixed in the shared
`diff_cxx_rules.py` parser (benefiting all four of its callers, not
just this module) by stripping the extra leading underscore before the
Itanium-prefix check, mirroring `dumper_clang.py`'s own
`_symbol_candidates()` de-prefixing approach for the identical quirk.

**An eighth finding pointed at a different mangling scheme entirely, not
a variant of the same Itanium quirk.** A `clang-cl` (or any
`--target=*-windows-msvc`) direct-clang snapshot records a method's bare
AST name — the same unqualified-leaf convention CastXML uses — while
`mangledName` is mangled in the proprietary Microsoft C++ ABI scheme, not
Itanium (Codex review, fresh evidence). `owner_class_of()`'s mangled-name
fallback only ever recognized the Itanium `_Z`/`__Z` prefix, so this
owner seed stayed `None` on every MSVC-mangled bare-named method,
regardless of the Mach-O fix above (a different, unrelated prefix
convention, not fixed by it). Confirmed empirically by compiling real
headers with `clang --target=x86_64-pc-windows-msvc -fms-compatibility
-Xclang -ast-dump=json`: `Foo::run()` mangles to `?run@Foo@@QEAAXXZ`
(scope components written *innermost first*, `@`-separated, terminated
by the first `@@` — the reverse order and terminator convention Itanium
uses, confirmed against nested-namespace, single-letter-class-name, and
global-free-function cases too). Fixed with a new, genuinely separate
`msvc_scope_components()`/`msvc_qualified_name()` pair in
`diff_cxx_rules.py` (not a branch inside the Itanium parser, since the
two schemes share no structure beyond both being length/separator-based),
tried as a second fallback in `owner_class_of()` after Itanium — the two
prefixes (`_Z`/`__Z` vs. `?`) are mutually exclusive, so trying both in
sequence is unambiguous and free on the common Itanium path. Deliberately
conservative, mirroring `itanium_scope_components`'s own "model the
simple cases, return `None` for the rest" contract, confirmed unmodelled
against the same real compiler output: special member functions and
operators (`??0` ctor, `??1`/`??_D` dtor, `??4` `operator=`, ...) mangle
with a *second* `?` immediately after the first, so the leaf/scope split
does not apply and is rejected outright; template classes/functions
(`?$Name@Args@`) embed the template-argument encoding inside the same
`@`-delimited region as the scope chain, and an argument token is
indistinguishable from a scope token by simple splitting, so any
component starting with `?` (the template marker `?$` or the anonymous-
namespace marker `?A`) is rejected; a bare-digit component is a
name-backreference into MSVC's per-symbol substitution table, not a
literal identifier (no real C++ identifier is all-digits, so this is an
unambiguous, lossless signal to bail — verified this does *not*
misfire on a genuine single-letter class name like `struct A`, which
mangles as a component that is a letter, never a bare digit). Also wired
the same new fallback into `type_reachability.py`'s two direct
`itanium_qualified_name()` call sites (the free-function/variable
stdlib-namespace guards, not just the owner-seeding path the review
comment named) — same root cause, same one-line fix, verified against a
`std::`-namespaced MSVC-mangled free function that would otherwise have
bypassed the guard identically to the Mach-O case above.

**A ninth finding pointed at an asymmetry in the typedef-spelling
ambiguity guard, not a mangling gap.** `_typedef_spelling_targets()`
registers every *derived* candidate spelling (a stdlib-stripped or
namespace-suffix form of a typedef key) only after checking it against
`_non_stdlib_signature_spellings()` — but the typedef's own *exact* key
was registered unconditionally, with no equivalent guard (Codex review,
fresh evidence). The already-documented direct-clang typedef-scope-loss
gap above (`parse_typedefs()` storing only the bare `node["name"]`) means
an exact key like `"Alias"` can itself collide with an unrelated
non-stdlib record's own bare signature spelling — e.g. a global `struct
Alias {};` sharing the same name as a namespaced `namespace api { using
Alias = std::string; }` whose `api::` the producer already dropped.
Confirmed empirically: `directly_referenced_stdlib_types()` incorrectly
returned `{"std::string"}` for a public function taking the unrelated
`Alias` record by value, purely because of the same-named, unrelated
typedef. Fixed by applying the identical `non_stdlib_spellings` guard to
the exact-key registration, matching how a colliding derived candidate is
already skipped — the spelling belongs to the real record, so the
typedef contributes nothing for it, rather than competing through the
ambiguity-resolution machinery.

**A tenth finding closed the conversion-operator half of the owner-
seeding gap the earlier `"::operator "`-marker fix only partly covered.**
That earlier fix handled a *display-name* conversion operator whose own
qualification embeds `"::"` (e.g. DWARF's `"Foo::operator ns::Bar"`), but
a direct-clang snapshot stores a conversion operator's AST name bare —
`"operator Bar"`, no owning-class prefix at all, confirmed via a real
`clang -ast-dump` — so `owner_class_of()`'s display-name branch never
applies (there is no `"::"` to find), and it falls through to the
mangled-name fallback (Codex review, fresh evidence). That fallback had
no coverage for conversion operators either:
`itanium_scope_components()`'s underlying component parser deliberately
excludes the Itanium `cv` (conversion-to-*T*) code from
`_ITANIUM_OPERATORS` — correctly, for that set's own purpose of grouping
operator *overloads* by a fixed 2-char code, since every conversion
operator carries a different target type and is never an overload of
another one — but treating `cv` as entirely unparseable meant hitting it
aborted the *whole* scope-recovery attempt, discarding the class name
already parsed before it. Confirmed empirically: `_ZNK3FoocvN2ns3BarEEv`
(`Foo::operator ns::Bar() const`) made `itanium_scope_components()`
return `None` outright, and `owner_class_of()` therefore returned `None`
instead of `"Foo"`. Fixed by recognizing `cv` as a distinct, opaque leaf
component (`"{op:cv}"`) in `_parse_operator_component()` — separately
from `_ITANIUM_OPERATORS`, since the overload-grouping semantics
correctly stay excluded — and forcing `_step_next_component()`'s `done`
flag to `True` immediately upon seeing it, regardless of nesting: the
conversion operator's own leaf is always the last component, and the
target-type encoding immediately following `cv` (e.g. `N2ns3BarE` for
`ns::Bar`) is a full, arbitrary Itanium `<type>` production — a much
larger grammar than this structural parser attempts elsewhere — but
recovering the *scope prefix* never needs that type parsed at all, only
a signal to stop before attempting it. Regression tests added: direct
parser-level cases in `TestItaniumScopeParser`/`TestMsvcScopeParser`'s
sibling `diff_cxx_rules` test file, plus an end-to-end
`directly_referenced_stdlib_types` test confirming a `Foo`-owning
conversion operator's embedded `std::string` field is no longer
filtered.

**An eleventh finding pointed at a masking mechanism the earlier
cross-index split didn't fully close.** Splitting `_spelling_index()`
into independent `stdlib_index`/`record_index` patterns (an earlier
fix) solved masking *between* the two indices — a non-stdlib wrapper's
identity embedding a stdlib type's spelling verbatim. It did not solve
the identical masking *within* either index (Codex review, fresh
evidence): `.finditer()` only returns non-overlapping matches, so when
one candidate's registered spelling is itself a substring of another
candidate's spelling *in the same index* (e.g. `"std::string"` inside
`"std::vector<std::string>"`, both stdlib; or a non-stdlib `"Inner"`
inside `"Wrapper<Inner>"`), the longest-first alternation matches the
outer candidate first, consumes the whole span, and the search
continues from the end of that match — so the inner one, though
directly present in the text, is never independently reported.
Confirmed empirically for both the stdlib and non-stdlib cases (and, on
further investigation while fixing this, the identical mechanism in
`typedef_pattern`'s typedef-key matching too — a third, independently
confirmed instance of the same root cause). Fixed with a single new
helper, `_finditer_allow_nested()`, used at all three call sites: for
every match found, it recurses into `text[m.start()+1 : m.end()]` — a
strictly narrower window, so recursion terminates — to catch a shorter
candidate embedded anywhere inside it, at any nesting depth, not just
one level. Kept as one shared helper rather than three inline copies
since all three loops have the exact same masking mechanism. Verified
against the existing large-corpus performance regression guard
(`test_many_unreferenced_stdlib_candidates_scan_efficiently`) to confirm
this doesn't reintroduce the quadratic candidate-by-candidate cost the
single-pattern rewrite was originally built to eliminate — the extra
recursive search only runs when a match is actually found (rare in the
common case), bounded by nesting depth, not candidate count.

**A twelfth finding closed a narrower gap in the conversion-operator
owner fix itself (tenth finding, above).** The `"::operator "`-marker
fix only detects a conversion operator when an *owner* precedes the
marker; a bare-recorded conversion operator (no owning-class prefix at
all, per the tenth finding) can still carry a *qualified target* with
its own `"::"` — e.g. `"operator ns::Bar"`, no `"Foo::"` prefix — and
for that shape neither the marker (there's no owner text before
`"operator"`) nor the previous unqualified-bare check applied, so the
naive `rsplit` fallback still ran and returned junk like `"operator
ns"` (CodeRabbit review). Confirmed empirically: constructing exactly
this input shape reproduced the bad `"operator ns"` result before the
fix. Fixed by checking for the `"operator "` prefix the same way the
already-fixed unqualified case is detected, falling through to
mangled-name recovery for both shapes uniformly.

**A thirteenth finding pointed at a robustness gap in the eleventh
finding's own fix, not a new correctness bug.** `_finditer_allow_nested()`
(the nested-match helper from the eleventh finding) recursed one Python
call per nesting level to search each match's own span for a further
embedded candidate (Codex review, fresh evidence). For a genuinely deep
chain of registered spellings each nested one inside the next —
plausible for template-metaprogramming-heavy C++ under a compiler's
configured template-instantiation depth (GCC/Clang both default well
into the hundreds, and it is routinely raised higher for real
metaprogramming-heavy code) — that per-level recursion follows the C++
template depth 1:1. Confirmed empirically: 1,000 successively nested
registered candidate spellings raised `RecursionError` under Python's
default 1,000-frame recursion limit, aborting the whole comparison
rather than degrading gracefully. Fixed by converting the recursive
search into an explicit stack — each entry is still a strictly narrower
window than the match that produced it, so the search still always
terminates, just without consuming Python's call stack to do it, so no
amount of nesting depth can overflow it.

**A fourteenth finding was a genuine regression the tenth finding's own
fix introduced, caught before merge.** Recognizing the Itanium `cv` code
as an opaque leaf component (tenth finding) used a single fixed
placeholder label (`"{op:cv}"`) regardless of the conversion's actual
target type. `diff_types._overload_group_key()` chains
`itanium_qualified_name()` — which now runs this label onto the scope
prefix — to decide whether two declarations are genuine overloads of one
another for `_diff_overload_additions()`'s KDE-policy check (Codex
review, fresh evidence). A fixed placeholder made *every* conversion
operator on a class produce the same qualified name regardless of
target — e.g. both `operator int()` and `operator double()` on the same
class reduced to `"Foo::{op:cv}"` — collapsing two conversion operators
that are never overloads of each other (each is a distinct, unambiguous
conversion function; there is no shared `&Foo::operator T` that becomes
ambiguous) into one group. Confirmed empirically:
`_diff_overload_additions()` fired a false `OVERLOAD_ADDED` for adding
`operator double()` alongside an existing `operator int()` before this
fix. Fixed by embedding the raw, un-decoded remainder of the mangled
string after `cv` into the label itself, instead of a fixed placeholder
— Itanium mangling is deterministic, so the same target always
reproduces the identical remainder (keeping genuine re-declarations in
the same group) while distinct targets always mangle differently
(keeping them in distinct groups), without this parser needing to
actually decode the arbitrary Itanium `<type>` grammar the remainder
encodes. Owner recovery (`owner_class_of()`, which only ever consumes
`comps[:-1]`, dropping the leaf entirely) is unaffected either way.

**A fifteenth finding pointed at a data-model assumption this module's
own new code introduced without verifying, contradicted by an existing
sibling.** `directly_referenced_stdlib_types()` built `non_stdlib_records`
as a plain `dict[str, RecordType]` keyed by identity — when
`snapshot.types` contains multiple entries sharing the same identity
(e.g. a complete definition alongside an ODR-duplicate or incomplete
declaration), a later entry silently overwrote an earlier one, so a
public signature reaching that identity walked only the survivor (Codex
review, fresh evidence). `surface.py`'s own `record_by_name` index —
the established reference this module has mirrored throughout every
finding above — already anticipates exactly this by keying on a *list*
of records per identity (`dict[str, list[RecordType]]`) and walking
every one (`for rec_node in rec_nodes: ...`), not a single winner; this
module's new dict introduced a real regression relative to that already-
correct sibling pattern, not a hypothetical edge case. Confirmed
empirically both orderings (the complete definition first, and the
complete definition last): whichever entry didn't survive the dict
overwrite, if it carried a `std::` field the survivor lacked, that field
was silently missed. Fixed by changing `non_stdlib_records` to
`dict[str, list[RecordType]]` (appending instead of overwriting) and
walking every record for a reached identity in the worklist loop,
checking each one's own `origin` independently (a private-origin
duplicate still excludes only itself, not a public-origin sibling
sharing the same identity) — exactly mirroring `surface.py`'s own
per-record walk.

**Wiring (this pass):** `diff_types.py`'s single choke-point gate,
`_is_abi_surface_type()`, now accepts a `directly_referenced` set (built
once per detector via `_directly_referenced(old, new)`) and un-filters a
std:: record that set names, instead of blanket-filtering every std::
record regardless of direct use. Because every RecordType-based
struct/union/field/kind/reserved detector in that file already shares this
one gate function, wiring it there once covers all of them uniformly —
not 9 independent, individually-drifting call sites. While wiring this in,
the FP-rate corpus's own new cases (`stdlib-direct-reference` category)
surfaced a second, *pre-existing* correctness gap in the gate's std::
check itself (independent of `directly_referenced`): it filtered using
`_is_non_abi_surface_type(t.name, ...)`, i.e. bare `t.name` only, the exact
same bare-vs-qualified split as the `type_reachability.py` fix above — so
a real castxml/clang-produced std:: record (bare `name`, qualified
`qualified_name`) was **never actually filtered as std:: at all**,
independent of whether anything referenced it. Fixed in the same gate by
keying the std:: prefix check on `qualified_name or name` (the anonymous-
type-marker half of the check still uses bare `name`, unaffected).
`diff_platform.py`/`diff_symbols.py`/`diff_vtable_layout.py`/
`diff_stdlib_impl.py`/`diff_layout.py`/`diff_filtering.py`/
`diff_type_spellings.py`, plus `diff_types.py`'s own enum/typedef paths
(which call `is_non_abi_surface_type`/`is_abi_surface_type_name` directly
on enum/typedef names, not through `_is_abi_surface_type`), remain
unwired and carry the identical bare-name gap — each needs its own
individually-verified follow-up (FP-rate/mutation-score gates), not a
drive-by extension of this pass's RecordType-scoped fix.

### L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag vocabulary fixed, the two-pass JSON-document crash fixed, real host+device dual-context replay still not implemented

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Fixed: -fsycl flag vocabulary (buildsource/adapters/base.py:160) and the two-pass JSON crash. Still open, as the entry says: host+device dual-context L4 replay.

Fixing L4 clang_bin
resolution to honor `--gcc-path` (an earlier PR) meant L4 could for the
first time actually invoke a SYCL-capable compiler (`icpx`/`dpcpp`)
instead of always a bare `clang`, which surfaced a narrower, real gap:
`-fsycl`/`-fsycl-*` wasn't in `adapters.base.ABI_RELEVANT_FLAG_PREFIXES`,
so it never reached the reconstructed L4 replay command even when the
real build recorded it (Codex review) — fixed, since the existing
`abi_relevant_flags` carry-through (`replay_extra_flags`) already handles
this class of flag correctly for every other case (`-std=`,
`-fvisibility`, …), so this was a one-line vocabulary gap, not a design
gap. That fix's own consequence #1 — "whether replaying without an
explicit `-fsycl-host-only` pin causes `icpx` to attempt a device pass
this pipeline can't consume" — was later **confirmed against a real
toolchain**: a real `-fsycl` bazel compile unit (oneDAL, 2.8GB AST) hit
exactly this, `json.load()` failing with `Extra data: line 26076735
column 2` at the byte offset where the device pass's document begins,
i.e. consequence #2 also confirmed (legacy `dpcpp`/`icpx` both emit
multi-document host+device AST output for a plain `-fsycl`, the identical
shape `sycl_context.py` already handles for the *L2* header-AST backend).
**Now fixed** by mirroring the L2 backend's own fix
(`dumper_ast_config._build_clang_header_command`/
`dumper_clang._needs_sycl_host_only`) rather than adopting
`sycl_context.py`'s full host/device dual-context decoder: L4 replay
parses ONE compile unit at a time and has no `--frontend-context device`
concept to select against (unlike L2's header-AST dump, which can be
asked for either context), so there is nothing for a second document to
serve here — `_clang_context_args` (shared by both the AST pass and the
macro pass) now appends `-fsycl-host-only` whenever
`dumper_clang._needs_sycl_host_only` says the resolved compiler is an
Intel oneAPI driver with SYCL effectively enabled and no single pass
already pinned, collapsing the compile back to the one host-side pass
that actually links into the scanned `.so` — the device pass's SPIR-V
kernel code never does. Reuses `_needs_sycl_host_only` directly (not a
reimplementation) so the last-flag-wins `-fsycl`/`-fno-sycl` scan and the
legacy `dpcpp`/`dpcpp-cl` SYCL-implied-by-default handling stay a single
source of truth with the L2 fix. The remaining "Extra data" `json.load()`
path now also emits an actionable hint (mirroring
`dumper_clang_errors._parse_clang_ast_result`'s) for any *other*,
not-yet-special-cased offload flag that still produces a multi-document
stream, rather than a bare byte offset. **Still not implemented, and
deliberately out of scope**: a genuine host+device dual-context L4
replay (an L4 counterpart to `--frontend-context device`) — nothing in
L4's `SourceAbiTu`/`CompileUnit` model has a notion of "this TU's device
pass," so adding one is a real, separate design (schema + linker +
cross-check changes), not a follow-up to this crash fix.

### `depfile_args_from_argv()`'s `trusted_root` parameter — the self-jail vulnerability is closed, real production wiring not implemented

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep finds no `trusted_root=` argument at any production call site; only buildsource/include_graph.py defines the parameter (lines 119-216), so production callers still drop the @file token.

Closing
the vulnerability (a compile unit's own `directory` field, attacker-
controlled for a unit sourced from an untrusted build pack, was used as
both the resolution base *and* the trust jail for expanding an unexpanded
`@response-file`) required the three production call sites
(`ClangIncludeExtractor.extract_from_build`,
`ClangPreprocessorExtractor.capture_macros`, `preprocessor_scan._depfile_context`)
to fall back to the existing safe "drop the token" behavior, since none of
them currently supply an independently-trusted `trusted_root` (Codex
review). **Not implemented**: threading a genuinely-trusted root into
those three call sites so response-file expansion works again for this
secondary L5/S2-scoping path. This is real, non-trivial plumbing, not a
one-line fix: `BuildEvidence.build_root`/`source_root` exist as fields but
no adapter (`compile_db.py`, `cmake_file_api.py`, `ninja.py`, `bazel.py`,
`make.py`) actually populates them today, so there is no already-flowing
trusted value to read off the model — the real anchor would have to be
threaded as a new parameter from `inline.collect_inline_pack()`'s own
`sources`/`build_info` CLI arguments (or, for the separate Flow-2
`abicheck_inputs/` ingest path in `inputs_pack.py`, the pack's own `root:
Path` already used for `_safe_pack_path` containment) through several
call layers in `inline.py` (already WARN-flagged oversized) and
`preprocessor_facts.py`. The functional impact of the current gap is
narrower than it first appears: `build_context.py`'s own `@file`
expansion (correctly jailed to the compile database's own directory since
the first response-file fix in this PR) already expands a
`compile_commands.json`-sourced `CompileUnit.argv` *before* it reaches
these three call sites, so they rarely see a raw, unexpanded `@file`
token for that primary path in practice — the gap mainly affects the
Flow-2 untrusted-pack path this fix was specifically about securing in
the first place. Confirmed via the full local suite (20935 passed) that
disabling expansion at these three call sites introduces no test
regressions.

### `Param.is_va_list` (G31 Phase C continued) inherits the toolchain- identity-probe gap above, rather than being a new problem

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** snapshot_reliability.py:182 and fact_registry_schema.py:273 have clang_va_list_facts_reliable as a plain flag without target identity, and no toolchain-identity probe exists yet (see entry 9).

Its
extraction predicate (`dumper_clang_qualifiers._clang_param_is_va_list`)
is deliberately scoped to the one ABI verified here — x86-64 System V —
and a snapshot from an unrecognized target already degrades to a
conservative `False` (see the function's own docstring). What's still
open (Codex review, fresh evidence): the snapshot-level reliability flag
(`AbiSnapshot.clang_va_list_facts_reliable`) records only "did the fixed
extractor run", not "against which resolved target" — so two
genuinely-different-target clang snapshots (x86-64 vs. AArch64, say) can
both read as reliable, and a real cross-architecture comparison (which
this tool's comparability layer permits in general) could read the
x86-64 side's real detections against the AArch64 side's uniform `False`
as a spurious `PARAM_BECAME_VA_LIST`/`PARAM_LOST_VA_LIST`. No header-AST
fact has resolved-target validation today, not just this one — closing
it here alone would be an inconsistent one-off fix for a structural gap;
it belongs with the toolchain-identity probe above once that lands.

### A using-declaration re-exporting a namespace-scope constant produces two `AbiSnapshot.constants` entries for one declaration on the castxml backend — reported against real oneDAL headers. A per-snapshot, name-shape-based dedup was implemented, reviewed, found unsound in two independent ways, and reverted rather than shipped broken (Codex review, three rounds)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The entry's own later verification (known-gaps.md ~4030-4068) found both backends now drop the alias spelling (a false negative), so the alias-identity gap remains open. There is no UsingShadowDecl threading in the code.

`cpu.hpp` opens a plain (non-inline)
`namespace v1 { constexpr ... cpu_feature_map = ...; }` and later does
`using v1::cpu_feature_map;` inside the enclosing `detail` namespace —
an ordinary C++ re-export pattern, not versioned-inline-namespace ABI
tagging (`inline namespace v1 {}`, which is the shape
`diff_namespaces.py`'s `_paired_stable_indices`/
`EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT` machinery already merges
aliases for — that machinery is function/type-scoped, cross-*snapshot*
(old vs. new), and keys on `experimental`/`preview`/`v0` segments
specifically, so it neither targets nor would catch this same-snapshot
pattern even if it applied to constants). castxml's `<Variable>` XML
records the using-declaration as a second full element rather than a
reference to the original, so `dumper_castxml.py`'s
`_iter_public_constants()` (the shared source for `parse_constants()`/
`parse_constant_headers()`) emits both `detail::v1::cpu_feature_map`
and `detail::cpu_feature_map` as independent entries in
`AbiSnapshot.constants` with the same value — distinct qualified-name
*keys*, not a key collision, so this is invisible to `model.py`'s
existing first-wins duplicate-name dedup (`function_map`/
`variable_map`/`type_by_name` all dedup by identity — mangled name or
bare type name — under which a using-shadow naturally collapses onto
its target; `AbiSnapshot.constants` has no mangled-name equivalent).
Reported at real scale, not a one-off: 98 alias groups across 267
constants in the reporting library's own dump. The same castxml
behavior duplicates other declaration kinds too, with two different
outcomes: 1,477 of 6,009 mangled function names were duplicated the
same way, but that half is **not a *double-reporting* bug** —
`model.py`'s mangled-name-keyed `function_map`/`variable_map` already
collapse a using-shadow function or variable onto its target for free
for the ABI/mangled-symbol question (a using-declaration doesn't change
what a symbol mangles to), so this is not a repeat of the constants
over-reporting problem above. **It is a separate, undocumented
false-negative gap on the source/API side, narrower in scope than the
`std::`-specific `STD_REEXPORT_REMOVED` detector below (Codex review,
fresh evidence):** because the diff is keyed on the unchanged mangled
symbol, a release that removes only the `using` re-export of a
library's *own* function or variable — not a `std::` name, which is
the one case `STD_REEXPORT_REMOVED` already covers — while keeping the
real declaration and its export produces no finding at all, even
though a consumer that named the alias-qualified spelling no longer
compiles. This is the identical shape of gap the clang-side note below
documents for constants (an unchanged underlying identity hides a real,
source-visible alias removal), just reached from the opposite side
(castxml capturing the alias correctly here, the *diff* layer being the
one blind to it) — not attempted in this pass, and would need
`detect_std_reexport_removed`'s general shape (matching declared
qualified names, not `std::`-specific) extended to a library's own
namespaces, which is its own scoped detector design, not a drive-by
extension of this entry. A duplicated bare *type* name (`range`
alongside `v1::range`) is a **separate, still-open** gap from the
type-dedup entry already documented above
(opaque-type suppression keyed by bare `RecordType.name` colliding
*across namespaces* on an accidentally-shared bare name) —
`range`/`v1::range` are two distinct qualified spellings of what may be
the same using-re-exported record, so today's exact-bare-name
first-wins dedup does not merge them either, and would need its own,
differently-shaped fix (threaded through `RecordType` identity, not
`AbiSnapshot.constants`), not attempted here.

**Confirmed independently that the duplicate-entry shape is castxml-only
— but the direct-clang L2 backend is not clean either, it just fails in
the opposite direction.** Verified directly against real Clang 18
(`-Xclang -ast-dump=json`) on a minimal repro of the same shape: a
using-declaration lowers to a `UsingDecl` node carrying the qualified
target name (`v1::cpu_feature_map`) but no `init`/value of its own,
immediately followed by an **implicit**, **unnamed** `UsingShadowDecl`
(`"isImplicit": true`, no `name` key at all — confirmed by inspecting
the emitted JSON directly, not inferred). **That `UsingShadowDecl` node
does carry real target identity** — a `target` object with the
underlying `VarDecl`'s own `id`, `kind`, `name`, and `type` (confirmed
by inspecting the real JSON: `{"id": "0x...", "kind": "UsingShadowDecl",
"isImplicit": true, "target": {"id": "0x...", "kind": "VarDecl", "name":
"cpu_feature_map", "type": {"qualType": "const int"}}}`) — so this is
identity Clang exposes and `dumper_clang.py` simply never reads, not
identity Clang lacks (Codex review, fresh evidence; an earlier revision
of this entry claimed the latter, which was wrong). `_categorize()` has
no branch matching either `UsingDecl` or `UsingShadowDecl` — its
`VarDecl` branch requires both `kind == "VarDecl"` and a non-empty
`name`, neither of which either using-related node satisfies — so both
are silently dropped, `target` included, and the re-exported spelling
never enters `AbiSnapshot.constants` at all on that backend.
This is *complementary* missing coverage, not a clean bill of health:
a release that removes only the `using v1::cpu_feature_map;` re-export
while keeping the real `v1::cpu_feature_map` definition breaks every
consumer of the re-exported spelling, but both the old and new
Clang-derived snapshots contain only the one underlying key —
`_diff_constants()` has nothing to compare and emits no
`CONSTANT_REMOVED`, a silent false negative. Castxml's two qualified
keys, over-reporting as they are for the case this entry is about, would
at least detect that removal correctly. So the two backends fail in
opposite directions on the same underlying gap (no alias-identity
evidence for constants) — over-reporting on castxml, under-reporting on
clang. Not verified against a live castxml run (no castxml binary
available in this environment to reproduce the XML shape directly) —
the castxml-side mechanism above is taken from the original report, not
independently re-derived from castxml's own output. **Update (G31 Phase
C, later pass): now independently re-derived, against a real
conda-forge castxml 0.7.0 build, and the "opposite directions" framing
in this paragraph does not hold — see the "Option (b) closed" note at
the end of this entry, below, for the reproduction and its
implications.**

**Attempted fix, reverted (three review rounds):** a
`qualified_name_segments.dedup_versioned_namespace_alias_items()`
helper collapsed one spelling onto the other within one already-
collected `_iter_public_constants()` item list, gated on BOTH (1) the
two qualified names differing by exactly one versioned-inline-namespace
*segment* (`version_strip_segments` — the same, already-trusted
structural check `diff_namespaces.py` already relies on for its own
alias merging) AND (2) the two values being byte-identical. Deliberately
narrower than the plain value-equality merge this same module's own
docstring already rejected after three earlier review rounds for a
*different*, cross-snapshot reason (that heuristic had no name evidence
at all and could merge unrelated same-valued declarations that never
even coexist together) — this attempt was same-snapshot only and added
a real structural name-shape gate. It was not enough. Two more rounds of
review found it unsound from two independent directions, and a third
round (after the second was "fixed") found the second fix was *also*
wrong, at which point it was reverted rather than iterated a fourth
time:

1. **Round 1 (P1):** the first revision kept the shorter, version-
   stripped alias and dropped the version-qualified original —
   reasoned as "the spelling a consumer of the `using` re-export
   actually writes." This actively fabricated findings: a release that
   adds or removes only the re-export (the real declaration unchanged)
   went from one snapshot keeping `detail::v1::x` (no alias present,
   nothing to strip) to the other collapsing down to `detail::x`
   instead — two different surviving keys for one unchanged
   declaration, which `_diff_constants` read as a spurious
   `CONSTANT_REMOVED` + `CONSTANT_ADDED` pair. In other words: this
   revision manufactured exactly the kind of double-reporting it was
   written to eliminate, just shaped as fabricated add/remove instead
   of a duplicated `CONSTANT_CHANGED` — arguably worse, since a
   fabricated `CONSTANT_REMOVED` reads as BREAKING.
2. **Round 2 (P1 again, on the "fix" for round 1):** reversing the
   direction — always keep the version-*qualified* spelling, always
   drop the alias — looked sound (invariant to whether either side
   happens to carry the re-export) and was reviewed as fixing round 1.
   A further round then produced a concrete counterexample proving the
   fixed direction was *also* wrong in general: `namespace detail {
   constexpr int x = 42; namespace v1 { using detail::x; } }` is
   legal C++ where a using-declaration imports a name **into** a
   versioned-looking namespace rather than out of one — here
   `detail::x` (the shorter spelling) is the real declaration and
   `detail::v1::x` (the longer, version-qualified spelling) is the
   alias, the exact reverse of what round 1's fix assumed universally.
   "Always keep the longer/qualified spelling" reproduces round 1's
   failure mode for this reversed input shape instead. **This is the
   structural finding that ended the attempt**: qualified-name *shape*
   alone (segment count, or which segment looks version-tagged) cannot
   determine which of two spellings is the real declaration and which
   is the using-introduced alias — a using-declaration can legally go
   in either direction relative to a versioned-looking namespace
   segment, and `_iter_public_constants()`'s current
   `(qualified_name, value, declaring_header)` output carries no
   signal (declaration order, an `artificial`-style marker, a
   using-shadow/target back-reference) that distinguishes the two
   directions. No fixed, name-shape-based rule can be sound for both.
3. **Round 3 (P2, on the value-equality gate itself, independent of
   direction):** even a correctly-directed rule would still merge two
   genuinely independent declarations that happen to form a
   version-alias-shaped name pair AND happen to share a value at
   extraction time (e.g. `namespace detail { namespace v1 { constexpr
   int x = 42; } constexpr int x = 42; }` — two unrelated `x`s, no
   `using` anywhere) — the same class of coincidence-driven risk this
   module's docstring already flags for the cross-snapshot merge it
   rejects, now shown to reach the narrower same-snapshot case too.

Given round 2 proves no per-snapshot, name-shape-based direction rule
can be sound, and a genuinely correct fix needs real using-shadow/
target identity evidence rather than name shape — continuing to patch
the heuristic's direction a third time was exactly the "one more
counterexample" pattern this file's own linkage-blind-removal and
type-identity entries above already warn against repeating. Reverted in
full (code, tests, changelog fragment) rather than shipped with a
known-unsound direction, per this file's own "known gaps over risky
reactive patches" convention — and per the same "attempted twice,
reverted twice" discipline already established here: a false BREAKING
finding fabricated by an unsound heuristic is a worse failure mode than
the pre-existing double-reporting it would have fixed, since the
original bug is merely noisy while a wrong-direction fabricated
`CONSTANT_REMOVED` blocks a release for nothing.

**What "real identity evidence" actually means differs by backend, and
the two must not be conflated (Codex review, fresh evidence corrected
an earlier draft of this entry that did conflate them).** On
direct-clang, the identity evidence demonstrably EXISTS today and is
simply unused: `UsingShadowDecl.target` (see above) names the exact
underlying `VarDecl` by AST id, so a sound clang-side fix is a real,
scoped option — capture the shadow, resolve `target.id` back to the
already-visited `VarDecl`, and re-register the alias under the
using-declaration's own enclosing-scope name, carrying its target's
identity forward rather than guessing from name shape. That is new
extraction work (`_categorize()` doesn't currently visit
`UsingShadowDecl` at all) and was not attempted in this pass, but it is
a materially different, better-founded starting point than the
name-shape heuristic this entry's earlier rounds tried and reverted. On
castxml, whether an equivalent target back-reference exists in its
`<Variable>` XML is genuinely **unknown** — not confirmed absent, not
confirmed present — since no castxml binary was available in this
environment to inspect real output for this construct; the reported
castxml duplication mechanism throughout this entry is taken from the
original report, not independently re-derived. A correct, full fix
needs one of: (a) the clang-side `target.id` threading described above,
landed and verified against the FP-rate/mutation-score gates before
trusting it; (b) inspecting real castxml XML for this construct to
determine whether it exposes anything equivalent, which this
environment cannot do; or (c) a genuinely different architecture that
resolves the ambiguity at diff time with both sides' full data available
simultaneously (mirroring how `diff_namespaces.py`'s
`_paired_stable_indices` jointly builds paired OLD/NEW indices for
functions/types, gated on real identity) rather than destructively
dropping information from one side's snapshot in isolation — a
materially larger, cross-cutting change on its own, not a follow-up
patch to the same per-snapshot helper.

**Option (b) closed (G31 Phase C, real conda-forge castxml build):** a
real, policy-conformant castxml (0.7.0, `conda-forge`, within the
`>=0.6.11,<0.8.0` range `castxml_policy.py` enforces, bundled Clang
20.1.8) is now available and was used to reproduce this construct
directly through the exact invocation shape `_build_castxml_command`
emits (`--castxml-cc-gnu (g++ -x c)`/`--castxml-cc-gnu g++`, matching
real header-dump usage) — **the originally-hypothesized castxml
duplication mechanism does not reproduce.** For `namespace detail {
namespace v1 { constexpr int cpu_feature_map = 42; } using
v1::cpu_feature_map; }`, real castxml emits exactly **one** `<Variable>`
element (`context="_13"`, i.e. `v1` — never `detail`), and that single
element's id is listed in *both* `detail`'s and `v1`'s `<Namespace
members="...">` attribute — a shared reference, not two elements. There
is no `<Using...>`-shaped XML tag in castxml's schema at all (checked
against castxml's own `--help`), and forcing the value to be ODR-used
(`&cpu_feature_map` from an inline function) does not change this.
Since `dumper_castxml.py`'s `_variable_els`/`_iter_public_constants()`
iterate the flat, once-per-XML-element id map (`_build_id_map`), not
either namespace's `members` list, they see this construct exactly once
too — `_qualified_name()` resolves it to `detail::v1::cpu_feature_map`
only; the alias spelling `detail::cpu_feature_map` never enters
`AbiSnapshot.constants` on this castxml version, at all. The identical
single-element behavior was independently confirmed for the sibling
type-dedup case this entry cross-references (`struct range` in `v1`,
`using v1::range;` in `detail`): one `<Struct>` element, shared between
both namespaces' `members` lists, never a duplicate. **What this means
for the entry above:** the "castxml over-reports (duplicate keys),
clang under-reports (missing alias)" asymmetry this entry documented
was written from an unverified report and does not hold for the
currently-supported castxml version range — for this exact construct,
both backends now demonstrably fail the *same* way (silent false
negative: the alias spelling is absent from the snapshot entirely,
matching the clang-side finding already confirmed above). This does not
retroactively invalidate the original report (98 alias groups across
267 constants, real oneDAL headers) — that scan may have used a
different castxml build, or the real duplication may come from a
different source construct entirely (e.g. two independent, textually
separate definitions sharing a value, rather than a language-level
using-declaration/using-directive) that this pass did not have the
original headers to reproduce against. It does mean: (1) the three
reverted name-shape-heuristic attempts documented above were correctly
reverted regardless of this finding (their unsoundness was proven by
counterexample, independent of whether castxml duplicates), so nothing
here reopens that question; (2) a future fix attempt should design for
the *false-negative* alias-identity gap confirmed symmetric on both
backends (option (a)'s `UsingShadowDecl.target` clang-side threading is
now the best-founded starting point, since it is real, already-present
AST identity, not a heuristic), rather than a castxml-side dedup this
evidence no longer motivates; (3) closing this for real still needs the
original large-scale report's exact source construct identified before
trusting any fix against it — not attempted here, since the oneDAL
headers that produced the original 98/267 count are not available in
this environment either.

### Qualified typedef twin exists but diff_types/surface still read bare-keyed AbiSnapshot.typedefs

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** AbiSnapshot.typedefs_qualified now exists (model/snapshot.py:466; dumper_castxml_typedefs.py:105) and type_reachability reads it. diff_types.py typedef diffing and surface.py typedef-following are still not wired to it.

`dumper_castxml.py`'s `parse_typedefs()` and
`dumper_clang.py`'s `parse_typedefs()` both do the identical
`typedefs[name] = underlying`, where `name` is the typedef's own local
`name` attribute/node field — never the scope-joined qualified spelling
`_qualified_name()`/`_qualified()` uses for every other declaration kind
in the same two modules. Verified directly against real CastXML XML
output (`--castxml-output=1`, clang-emulated) for a minimal repro: a
member typedef nested inside a struct (`struct WithUsing { using
value_type = int; ... };`) is emitted as an ordinary top-level
`<Typedef name="value_type" ... context="_12" .../>` element, structurally
indistinguishable from a namespace-scope typedef except for its
`context` attribute — which `parse_typedefs()` never reads. Two structs
each declaring their own `value_type` member alias (an extremely common
C++ pattern — STL-container-shaped types conventionally expose
`value_type`/`size_type`/`reference`/... as member typedefs) collide on
the identical bare key `"value_type"` in the resulting dict, and
whichever element is encountered last in document order silently wins —
the other's aliasing information is dropped from the snapshot entirely,
with no warning, error, or any user-visible sign of the loss. The same
collision shape reproduces on the direct-clang backend: `_typedefs` is
populated by the same flat walk used for every other decl kind, but
`parse_typedefs()` discards the entry's own recovered `scope` and keys
only on `node["name"]`. **Not fixed here**: this is a real, if narrow,
public-model change — `AbiSnapshot.typedefs`'s key shape is read by
`type_reachability.py`'s `_typedef_spelling_targets()` (see the
"Type reachability" entry above, which already works around this same
bare-key ambiguity from the *consumer* side via its own
ambiguity-counting helper, `_typedef_spelling_targets`, rather than
assuming a bare key uniquely names one typedef), by `diff_types.py`'s
typedef diffing, and by `surface.py`'s typedef-following in
`_walk_type_closure` — none of which currently have test coverage for
the cross-class member-typedef-collision case to validate a change
against. A correct fix needs a qualified (or at minimum
collision-detecting) key threaded through both producers and every
consumer simultaneously, each independently re-verified against the
FP-rate/mutation-score gates before trusting it — the same systematic,
cross-cutting shape (and the same "known gaps over risky reactive
patches" reasoning) as the already-documented bare-`RecordType.name`
opaque-type-suppression collision above, for a different model field.
Filed here per this file's own convention rather than attempted under
this pass's time budget.

**Closed, additively, in a later pass (G31 Phase C continued).** Rather
than replacing `AbiSnapshot.typedefs`'s key shape — which would have
meant re-verifying every one of its existing consumers
(`type_reachability.py`, `diff_types.py`, `surface.py`) against a changed
contract, plus every external Python-API caller reading that field
directly — the actual fix is purely additive: a new field,
`AbiSnapshot.typedefs_qualified` (schema v25), a fully-qualified-name-keyed
twin populated by both `dumper_castxml.py`'s and `dumper_clang.py`'s
`parse_typedefs_qualified()` (using the identical `_qualified_name()`/
`_qualified()` scope-joining every other declaration kind in those modules
already uses) alongside the existing, deliberately-unchanged
`parse_typedefs()`. Since a qualified name is unique per declaration, this
twin cannot suffer the bare-name collision at all — both `Foo::value_type`
and `Bar::value_type` survive as distinct entries where only one bare
`value_type` could before. Threaded through the ELF manifest per-TU merge
path too (`TuFragment`/`MergedTuFragments`/`ElfHeaderAstResult` in
`tu_fragment.py`/`tu_merge.py`/`dumper_manifest.py`), closing the identical,
separately-documented "Known, accepted limitation" comment `tu_merge.py`
carried for the multi-TU case specifically. Only one consumer was wired to
actually use the new field: `type_reachability.py`'s
`directly_referenced_stdlib_types()` (via a new `_merged_typedefs()` helper
that folds `typedefs_qualified` into the flat dict already passed to
`_typedef_spelling_targets()` and siblings) — closing the real false
negative where a public signature spelled with the qualified alias the
bare dict had already lost could silently miss a reachable `std::` field.
`diff_types.py`'s typedef diffing and `surface.py`'s typedef-following in
`_walk_type_closure` are **not** wired to the new field in this pass — each
is its own scoped follow-up, not a drive-by extension, since each has its
own call shape and (per this file's own established discipline) needs its
own test coverage before trusting a change to it. No reliability flag was
needed for the new field (unlike the `*_facts_reliable` flags v19–v23 use
for a real-but-wrong scalar default): an empty `typedefs_qualified` is
exactly the same value a genuinely-typedef-free snapshot would carry, so a
pre-v25 snapshot degrades cleanly to "no extra qualified data available"
rather than being misread as a real fact — see `serialization.py`'s own
v25 history-comment entry for the full reasoning. Verified via new unit
tests on both header backends directly (`_CastxmlParser.
parse_typedefs_qualified`/`_ClangAstParser.parse_typedefs_qualified`) and
an end-to-end `type_reachability` regression proving the bare dict's lossy
collision no longer hides a real `std::` field.

### Closure-parameterized non-ctor functions still churn (func_params_changed) on lambda line shifts; '?' ctor param unexplained

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Type-level marker fix, ctor/dtor binary-evidence fix and L5 declaration_renamed fix (model.graph_identity.closure_location_free_identity) are recorded as landed. Still open: non-ctor func_params_changed/template_param_type_changed on closure-parameterized symbols, the mangled-only surface filter gap, and residual (2), the literal '?' ctor parameter.

`name_classification._ANONYMOUS_TYPE_MARKERS` did not
recognize clang's own closure spelling (`(lambda at <path>:<line>:<col>)`,
or the `(lambda:<file>:<line>:<col>)` form
`strip_anonymous_type_location` normalizes it to), so a template
instantiated over a closure —
`raii_guard<(lambda:task_group.h:522:26)>` — was treated as ordinary ABI
surface. That half is fixed: the marker list now covers it, and an
unrelated edit that merely shifts the lambda's line no longer produces a
`type_removed`/`type_added` pair. **Two residuals were reproduced and are
not closed.**

(1) *Function-level findings on closure-parameterized symbols.* A
destructor of that instantiation is still reported as a BREAKING
`func_removed` (plus `func_added` for the shifted spelling), and a public
function taking the closure-parameterized type by value still produces
`func_params_changed`/`template_param_type_changed`, because the
*mangled symbol* and the *parameter type spelling* both embed the
closure's source coordinates. Reproduced directly through `compare()`
with the two spellings differing only in the line number.
`is_non_abi_surface_type` is a *type*-name predicate and is not consulted
on either path, so the marker fix cannot reach them. **Demoting the
removal is not the fix, and the reason matters**: this codebase already
states the correct reading in `change_registry`'s own
`unnamed_type_in_public_abi` entry ("the Itanium mangling of unnamed
types is per-translation-unit and compiler-ordering dependent ... so
exporting one is an ABI time bomb: a rebuilt consumer can fail to resolve
the symbol"). A consumer *already linked* against the old numbering
really does fail at load when the numbering shifts — so the removal is a
genuine break, and softening it would hide a real one. That is the same
direction of error the linkage-blind-removal entry above was reverted
twice for. Nor is "strip `:<line>:<col>` before comparing two spellings"
a free win: `strip_anonymous_type_location`'s own docstring records why
the coordinates are kept (two unrelated lambdas in one header collapse to
one key, silently overwriting an entry in every name-keyed map that
consumes the spelling), and this pass has no real oneTBB snapshot to
check a narrower "normalize only for pairwise spelling comparison, never
for keys" variant against. What a correct fix needs is the annotation
route ADR-style precedent already establishes elsewhere in this file
("Annotate; never remove"): correlate such a finding with the existing
`unnamed_type_in_public_abi` RISK signal so a reader can see *why* the
symbol churned, rather than removing it from `changes` or lowering its
verdict. That is a new correlation pass with its own identity question
(which finding covers which), not a marker-list edit.

**Update: the ctor/dtor half of (1) is now closed via binary evidence,
not annotation.** A later report against real oneTBB 2021.13.0 →
2022.3.0 binaries reproduced exactly this shape at scale:
`demote_lambda_closure_unexported_findings` (`diff_templates.py`) had
already been added to demote a `FUNC_PARAMS_CHANGED`/
`TEMPLATE_PARAM_TYPE_CHANGED`/`TEMPLATE_RETURN_TYPE_CHANGED` finding
whose reported symbol is confirmed absent from BOTH sides' real ELF
exported symbol table (never escalates; fails closed when no ELF
evidence exists on either side) — but it deliberately excluded every
castxml-synthesized ctor/dtor key
(`dumper_castxml.is_synthetic_ctor_key`/`is_synthetic_dtor_key`), on the
correct-as-far-as-it-went grounds that such a key is *never* itself a
real exported symbol (castxml synthesizes it specifically because it
could not produce one), so a membership check against the key text
would always read "confirmed absent" — vacuously, not because anything
was actually verified. That exclusion left every destructor/constructor
of a closure-parameterized instantiation permanently un-demotable,
which is exactly the residual (1) describes and exactly what the oneTBB
report reproduced: 5 breaking `func_removed` findings, all on synthetic
ctor/dtor keys naming
`tbb::detail::raii_guard<(lambda:task_group.h:522:26)>`/
`try_call_proxy<...>`/`task_arena_function<...>`/
`delegated_function<...>`, paired 1:1 with 5 compatible `func_added`
findings differing only in the lambda's line number.

Fixed by asking the binary a question the synthetic key's *text* can
answer honestly, even though the key itself is not a real symbol: is
the owning class/class-template exported under ANY instantiation at
all, on either side?
`finding_identity_ctor_dtor.synthetic_ctor_dtor_template_base_name`
recovers the owning scope from the key (that same module's own
`synthetic_ctor_scope` for a ctor key's
depth-aware `scope(params)` split; a plain prefix strip for a dtor key),
reduces it to the bare, template-argument-stripped template name via
`type_reachability._bare_type_name` plus a top-level `<` scan
(`raii_guard<(lambda:...)>` → `raii_guard`), and
`finding_identity_ctor_dtor.itanium_source_name_token` renders that name
as its Itanium `<source-name>` encoding (`"raii_guard"` → `"10raii_guard"`,
keyed on the identifier's encoded UTF-8 **byte** length rather than its
Python character count, so a non-ASCII class name still produces the
correct token) — a literal substring every real mangled symbol naming
that class as a scope component must contain (Itanium C++ ABI §5.1.1),
checked directly against the raw exported names with no external
demangler invoked, the same "structural, not textual" preference
`diff_cxx_rules.itanium_scope_components` already established for this
codebase. Both helpers live in `finding_identity_ctor_dtor.py`, not
`diff_templates.py` itself (which only keeps the classification/
modulation entry point, `demote_lambda_closure_unexported_findings`):
`diff_templates.py` is one of ADR-061's `debt.yaml`-tracked
no-growth-baselined legacy files, `finding_identity_ctor_dtor.py` already
owns every other castxml synthetic-ctor/dtor-key helper and had headroom
under the flat 800-line production limit, and a brand-new flat `diff_*`
sibling module is explicitly rejected by `scripts/check_architecture.py`'s
`frozen_root_families` list (confirmed by trying it — `[frozen-root-family]`
and `[root-module]` findings) — while a real `policy/` package migration
for just this one function was investigated and found to cascade into
`unclassified-import` findings for every one of its ~8 currently-flat,
not-yet-layer-classified dependencies (`checker_policy`, `diff_symbols`,
`dumper_castxml`, `elf_symbol_filter`, `type_reachability`,
`name_classification`, ...), which is its own separate, much larger
ADR-061 migration slice, not a follow-up to this fix. A template with
zero exported members under any instantiation on either side is demoted
(`Verdict.COMPATIBLE_WITH_RISK`,
`modulation_rule="lambda_closure_never_exported"`, same ADR-025 hook,
never removed); a template that *does* export some other instantiation
is left exactly as severe as the detector made it, since this check
cannot rule out that the specific closure-parameterized instantiation
was the one a consumer actually linked against.

Deliberately narrower than reconstructing the *exact* per-instantiation
mangling: the real Itanium mangling of a closure-type template argument
uses the compiler's own unnamed-type encoding (`Ul<parameter-types>E_`),
never castxml's `(lambda:file:line:col)` spelling, so there is no way to
derive the precise mangled substring for one specific instantiation from
the snapshot text alone — checking the template's own name is the safe,
strictly more conservative substitute this function's whole design
(fail closed, never escalate) already calls for. See
`tests/test_lambda_closure_function_demotion.py`'s
`TestSyntheticCtorDtorKeysDemotedWhenTemplateNeverExported`/
`TestSyntheticCtorDtorKeysNotDemotedWhenTemplateIsExported` for both
directions, verified against the exact class names from the oneTBB
report.

**A review round on the same fix found a real gap in the substring
search itself, not in the demotion logic around it.** Six `std::` names
(`allocator`, `basic_string`, `basic_istream`, `basic_ostream`,
`basic_iostream`) carry a *fixed, mandatory* Itanium ABI substitution
(`Sa`/`Sb`/`Si`/`So`/`Sd`, C++ Itanium ABI §5.1.2) — the mangler always
emits the abbreviation instead of the literal source-name, even on the
first occurrence in a symbol. A real `std::allocator<int>::allocator()`
mangles to `_ZNSaIiEC1Ev`, never to anything containing the literal
substring `"9allocator"` — so a synthetic `std::allocator<(lambda:...)>`
finding's literal-token search would read that class as "never
exported" regardless of the truth, silently demoting a genuine removal.
Fixed with `finding_identity_ctor_dtor.itanium_standard_substitution_
token`, checked alongside the literal token whenever the owning class's
qualified name is exactly one of the six; see
`tests/test_lambda_closure_function_demotion.py`'s
`TestStdAllocatorSyntheticKeyNotFalselyDemoted` for the exact
counterexample from review, reproduced and fixed.

Two related residuals from the same report. The compatible `func_added`
churn is ordinary add/remove pairing (not itself a bug), left as-is.

~~The risk `declaration_renamed` findings elsewhere in the same report (7
further ones) is pure noise from the lambda's identity embedding its
source `line:col` rather than an ordinal position — closing that needs
changing how a closure is *identified*, a materially larger change to
`name_classification`/the castxml and clang backends' own closure naming
than this fix's binary-evidence check, and is not attempted here.~~
**Update: fixed, narrower than the originally-scoped "change how a
closure is identified" (fresh report, real oneTBB 2021.12.0 -> 2023.0.0,
139 `declaration_renamed` L5 findings against oneDNN's 3 for a comparable
corpus size).** Tracing a representative sample confirmed the same
mechanism this entry already names: `buildsource.graph_reconcile`'s
structural-context tier correctly PAIRS an unrelated-edit-shifted
closure with its own unchanged predecessor (that matching evidence never
reads the coordinate text at all), but `_classify_outcome` then compared
the two nodes' *literal* qualified-name spelling — which embeds the
closure's own `:line:col` — to decide "renamed", so every closure an
unrelated header edit merely shifted read as a rename. Rather than the
originally-scoped identity-naming rewrite (which risks the exact
same-header-collision trade-off `strip_anonymous_type_location`'s own
docstring documents for *matching*), the actual fix is narrower and
provably safe: `model.graph_identity.closure_location_free_identity`
drops a closure/anonymous-tag marker's basename+coordinates entirely (not
merely the checkout-root prefix `_normalize_graph_identity` already
strips) for the *outcome-classification* comparison only, never for
matching/merging — the pair was already established by tier evidence that
never consults this text, so no new merge rides on it, and a genuine
cross-file move is still caught by the separate, coordinate-text-blind
`old_file`/`new_file` check right below it (reclassifying it
`declaration_moved` instead of the misleading `declaration_renamed`,
never hiding the file change). See
`tests/test_graph_reconcile_closure_rename.py` — both the oneTBB-shaped
regression cases and a standalone property-test class for the new
primitive (idempotence, location/basename invariance, parenthesized/bare
spelling agreement, marker-kind non-collapse, never-raises on arbitrary
text) per this file's own "Primitive-level property tests" guidance,
since this is a reusable identity-comparison helper, not a one-off
patch. The separate, still-open gap about the L5 graph's own node *ids*
not sharing the flat snapshot's ordinal renumbering (this file's own
"The L5 source graph's own node identities are never renumbered..."
entry, elsewhere in this file) is unaffected by this fix — it is about
node-id/flat-field spelling agreement, not the rename-vs-not-renamed
classification this fix corrects.

And a public-surface filter gap (ELF-only, mangled-only symbols
such as `std::once_flag::_Prepare_execution<lambda>`'s internal guard
thunks, which `surface.py` cannot scope-classify because it never
demangles) is a separate detector, not this one, and is likewise not
addressed here.

(2) *A constructor key rendering a literal `?` parameter.* Traced to two
legitimate "type not recoverable" sentinels rather than to a formatting
bug: `dwarf_snapshot._process_param` returns `Param(type="?")` for a
`DW_TAG_formal_parameter` carrying no `DW_AT_type`, and
`dumper_castxml._type_name` returns `"?"` when a referenced type id does
not resolve in the XML (a closure class declared inside a function body
is exactly the shape that goes unemitted). Both are honest unknowns, and
which one produced the reported key cannot be determined without the
original snapshot, which this pass does not have. Guessing a
substitution here would replace a visible unknown with a fabricated
spelling, which is strictly worse; closing it needs the real artifact (or
a live castxml/DWARF repro of a closure-parameterized ctor) first.

### The L5 source graph's own node identities are never renumbered alongside the flat snapshot's closure markers (Codex review on PR #868, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/snapshot_load_normalization.py:123-138 docstring still says surface_graph node/edge identities are not renumbered. renumber_anonymous_closure_identities (storage/closure_identity.py) has no graph handling.

`renumber_anonymous_closure_identities` rewrites a
closure's `:<line>:<col>` discriminator to a stable `#N` ordinal across
`AbiSnapshot.functions`/`variables`/`types`/`enums`/`typedefs`/
`constants`/`fact_provenance` (`_LAMBDA_IDENTITY_FIELDS`), but
`service_header_graph_attach._attach_header_graph`'s embedded
`build_source.source_graph` is built by a genuinely separate clang
parse (`buildsource.header_graph`), whose node ids
(`graph_facts._decl_node_id`/`_type_node_id`) are derived directly from
the raw, un-renumbered identity string -- confirmed by reading both
functions, which apply no renumbering at all. A closure-parameterized
declaration therefore reads as `Foo<(lambda:file.h#1)>` in the flat
snapshot but `Foo<(lambda:file.h:20:5)>` in its own source-graph node, so any
consumer trying to correlate the two (e.g. matching a flat finding back
to its graph neighborhood) sees two different spellings for the same
entity. **Not fixed here**: a correct fix needs the ordinal map
`collect_anonymous_type_ordinals` computes from the flat fields to also
be applied to every graph node id/name *and* every edge's `src`/`dst`
reference to it -- and the source graph can name a closure the flat
ABI-surface fields never mention at all (an internal-linkage helper
visible only in the L5 graph), which the flat-only ordinal map has no
entry for, so naively reusing it risks leaving some graph-only closures
unrenumbered while their flat-visible siblings are. A correct fix likely
needs the ordinal collection widened to scan the graph's own node/edge
strings too, verified against a case that actually mixes flat-visible
and graph-only closures in one header -- a real, cross-cutting change
to two independently-evolving modules, not a same-PR reactive patch.

**Same gap, also reachable from the load path (Codex review, fresh
evidence).** `storage.snapshot_load_normalization.normalize_anonymous_
type_spellings_on_load` (added to close a sibling bug: a raw pre-strip
on-disk baseline was left completely unrenumbered on load, see this
file's own git history) rewrites the identical flat
`_LAMBDA_IDENTITY_FIELDS` only, called after `snapshot_from_dict` has
already decoded a schema-v29+ document's `AbiSnapshot.surface_graph` --
so a loaded raw-marker baseline's attached graph keeps its own
un-stripped node/edge identities even once the flat fields are
normalized. Not fixed here, for the same reason as above: it needs the
same graph-aware widening this entry already calls for, not a second,
independent patch on the load side.

- ~~Neither `scan`'s dry-run report validates `--abi3` applicability before
reporting success~~ **Fixed (CLI cleanup phase two, PR 5 follow-up).**
Both `frontends.cli.scan_dry_run.render_scan_dry_run` (single-binary) and
`frontends.cli.artifact_set_dry_run.render_artifact_set_dry_run`
(`--artifact-set`) now run a cheap, binary-only extension probe
(`python_ext.detect_python_extension_from_binary` -- container-only ELF/
PE/Mach-O read, no DWARF/AST parse, the same "binary export table parse"
already priced under the L0_binary dry-run row) and route a non-qualifying
candidate through `DryRunResult.block()` (exit 1), matching the real run's
`EVIDENCE_CONTRACT_ERROR`. The message text is shared with
`scan_engine._run_abi3_audit`'s real precondition failure via
`python_ext.abi3_precondition_message()` so the three callers cannot
independently drift. See `tests/test_scan_dry_run_abi3.py`.

- ~~A pinned depth backed only by a query-declaring `--config` (no
`--sources`/`--build-info`) prices L3/L4/L5 at zero TUs/zero cost~~
**Fixed (CLI cleanup phase two, PR 5 follow-up).** `_estimate_total_tus`
gained a `query_only` branch: when `req.build_config` declares a real
`build.query` and no `--sources`/`--build-info`/compile DB is given, the
L3 note is flagged `[UNKNOWN: build.query declared, ...]` (the same
"annotate honestly rather than fold a floor into the summed total" shape
`_UNSCOPED_TU_NOTE_SUFFIX` already uses for the sibling `--build-target`
undercount case) instead of the confident-looking `0` every other
"nothing given" case reports. `_source_layer_estimates` carries the same
marker onto the derived `L4_source_abi`/`L5_source_graph` notes too
(Codex review, fresh
evidence: an earlier revision of this fix only flagged L3's own row, so
`--depth source`/`--depth graph` still priced the derived layers as a
confident zero), and `estimate_artifact_set`'s 4th return value
(`unknown_layers`) lets the `--artifact-set` aggregate renderer apply the
same per-layer treatment rather than a single project-wide flag hardcoded
to `L3_build`. Reaches both dry-run paths uniformly, since both call
`estimate_scan()`. See
`tests/test_scan_dry_run_abi3.py::test_estimate_total_tus_query_only_config_marks_count_unknown`
and its `test_estimate_scan_propagates_unknown_tu_count_to_l4_and_l5`/
`test_estimate_artifact_set_reports_unknown_layers_per_layer` siblings.

### Two function/method template overloads distinguished only by a `requires`-clause still collide under ADR-063 Phase 2's `EntityId` discriminator (Codex review, PR #943, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/buildsource/template_graph.py:102-122 still defers CONSTRAINT_DEPENDS_ON_DECL and says requires-constrained overloads with the same signature collide. abicheck/extract/headers/clang has no ConceptSpecializationExpr handling.


`template<class T> requires C1<T> void f();` and the same declaration
constrained by `C2<T>` instead share scope, leaf name, an identical
ordinary parameter list, and an identical `function_template_param_kinds`
result (`("type",)`), so they collapse onto one `EntityId` even after
the parameter-kind/packness/dependent-rename fixes landed for this
discriminator. Confirmed by direct compilation that clang's own
`ConceptSpecializationExpr` node (a `FunctionTemplateDecl` child
appearing right after the constrained `TemplateTypeParmDecl`) carries no
concept name or resolvable reference to one anywhere in its own JSON
subtree -- every key on the node and its
`ImplicitConceptSpecializationDecl` child was inspected directly, and
neither carries anything but synthetic AST ids and dependent-type
placeholders (`type-parameter-0-0`). **Not fixed**: recovering the
concept's actual name would need either a different clang AST-dump
mode/flag or the raw header source text sliced at the node's own
`range` offsets, and `_ClangAstParser` (`abicheck/dumper_clang.py`)
deliberately consumes only an already-parsed JSON tree with no source
text available to it -- a fragile source-offset hack was rejected rather
than attempted. A correct fix needs either threading the header's raw
source text through to this parser (a larger architectural change
outside this discriminator's own scope) or a clang invocation change
that emits a concept reference here, verified against a real build
before landing either way. See
`docs/contribute/plans/one-semantic-pipeline.md`'s Phase 2 section for
the full investigation.

### Out-of-line member template definition gets a different EntityId scope than its in-class declaration (header parser; template_graph fixed)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The template_graph half is fixed: abicheck/buildsource/template_graph.py:1207-1211 uses parentDeclContextId. No parentDeclContextId use exists in dumper_clang.py (_ClangAstParser at :743) or in extract/headers/clang, so the EntityId scope split is still present.

`struct A { template<class T> void f(T); }; template
<class T> void A::f(T) {}` -- confirmed by direct compilation
(`clang -Xclang -ast-dump=json`) that clang emits TWO
`FunctionTemplateDecl` nodes for `f`: one lexically nested inside `A`'s
own `CXXRecordDecl` (the in-class declaration), and one at the
ENCLOSING namespace's own lexical level (a sibling of `struct A`, not a
child of it) carrying a `parentDeclContextId` pointing back at `A`'s own
node id -- clang's own signal for "this out-of-line definition's real
semantic owner is `A`, even though it isn't lexically nested inside
it." `_ClangAstParser._walk` computes both `scope`/`scope_path` purely
from LEXICAL nesting, with no `parentDeclContextId` handling anywhere in
this codebase, so the out-of-line definition gets `scope=()` while the
in-class declaration gets `scope=(Record("A"),)` -- `parse_functions`
parses BOTH nodes into separate `Function` entries with disagreeing
`EntityId`s for what is really one entity. The review further confirmed
`parse_variables` has the analogous gap for an out-of-line class-template
static data member definition. **Not fixed**: unlike this phase's other
fixes (each a small, local addition to one already-threaded parameter),
closing this properly needs a NEW general-purpose facility this codebase
doesn't have yet -- a typed, `ScopePath`-valued sibling of the existing
`dumper_clang_expr._index_decl_id_qualified_names` (which already indexes
every decl id to a FLAT qualified-name string for a different consumer,
but a flat string cannot be losslessly converted back into a typed
`ScopePath` -- collapsing `Record`-vs-`Namespace`-vs-`InlineNamespace`
is exactly the ambiguity `ScopePath` was built to prevent). That index
would need building once per parse, threading through both
`parse_functions` and `parse_variables`, and reasoning through further
edge cases this investigation did not exhaustively enumerate (an
out-of-line member of a NESTED class, an out-of-line member of a
class-template SPECIALIZATION, and whether castxml's own `context`
resolution has the identical gap for parity). A correct fix needs that
index plus verifying each edge case against a real compilation before
landing, not a narrow patch for only the one reported shape.

### Public-surface graph: surface_graph vs L5 header_graph node-id namespaces not unified; type_reachability stdlib query unmigrated

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The traversal half is superseded: the closure walk moved to policy/public_surface_closure.py. Still true: abicheck/type_reachability.py is unmigrated, and the compare/surface_graph.py and header_graph node-id namespaces are not unified.

`policy/public_surface.py`'s
`PublicSurfaceQuery` delegates to `surface.compute_public_surface()`/
`export_surface.compute_export_surface()` unchanged rather than
reimplementing either as a literal graph traversal: both are exactly the
kind of intricate, multi-round-corrected logic this same page's
`_paired_stable_indices` incident (see "Primitive-level property tests"
in `AGENTS.md`) shows costs several review rounds to get right even once
already, and reimplementing one from scratch inside the same phase that
also had to build every piece of graph infrastructure underneath it was
judged materially higher-risk than landing the infrastructure now and
migrating the algorithm as its own later, narrowly-scoped phase.
Consequences: `compute_public_surface()`'s signature was never changed
to accept a structured `resolution` parameter, so there is no lazy,
graph-reading legacy-snapshot backfill path; `type_reachability.
directly_referenced_stdlib_types()` was not migrated into
`policy/public_surface.py` (doing so would reclassify `type_reachability.py`
into the `policy` layer, which would introduce a genuine new
`policy -> extract` architecture violation — that module imports two
already-`extract`-classified siblings); and `compare/surface_graph.py`'s
own node ids (`canonical_key(occurrence_id)`/`approx::`/`typedef::`
fallbacks) are a namespace fully independent of `buildsource/
header_graph.py`'s pre-existing L5 node ids (`decl://<identity>`/
`type://<identity>`) — one shared `SourceGraphSummary` instance carries
both builders' nodes (real, tested — `service_header_graph_attach.
_attach_header_graph()`), but the two schemes do not dedup onto a common
node for a declaration both builders see. Each of these is a real,
separate follow-up migration, not silently-abandoned scope — see the
implementation plan's Phase 3 "Landed"/D5 "Amendment" notes
(`docs/contribute/plans/one-semantic-pipeline.md`; ADR-063 itself no
longer carries a duplicated per-phase status block, removed by PR 0,
2026-09-02) and
`compare/surface_graph.py`'s/`policy/public_surface.py`'s own module
docstrings for the exact reasoning each carries.

**Correction (2026-09-01): the traversal-migration half of this entry's
own title is now stale — the algorithm was migrated after all, just not
in this phase's first landing.** A later round did reimplement
`surface.py`'s closure walk as a real traversal rather than leaving it
in place: `_index_surface_types`/`_seed_public_roots`/
`_walk_type_closure`/`_walk_exact_type_closure`/`_record_exact_identities`/
`_record_nested_in_known_record`/`_record_is_confirmed_public_seed` and
the `PublicSurface` type moved to `policy/public_surface.py` (dataclass +
indexing) and `policy/public_surface_closure.py` (the walk itself, plus
`resolve_public_surface()`), and `surface.py`'s own copies were
**deleted**, not kept alongside — `surface.compute_public_surface()` is
now a thin re-exporting wrapper. `export_surface.py`'s own root-seeding
stayed in place, but its final type-closure step now calls the same
migrated `_walk_type_closure` the header domain uses, so that domain
became graph-native for free. This is the risk the paragraph above
named and chose to defer, not a different fix — it just didn't stay
deferred through the whole phase. The next two bullets below give the
full, three-review-round account of what that migration actually needed
to get right (and what it does *not* touch — `snap.surface_graph`/
`GraphNode.attrs`, in the design that finally shipped). What is **still**
correctly described by the paragraph above, unchanged: `export_surface.py`'s
own root-seeding logic, `type_reachability.directly_referenced_stdlib_types()`
staying unmigrated (same `policy -> extract` reason), and the two node-id
namespaces not deduping onto one node. See the implementation plan's
Phase 3 "Landed"/D5 "Amendment" notes for Phase 3's final accounting —
ADR-063 itself no longer carries this per-phase detail.

### `action/run.sh`'s `extra-args` parsing performs pathname expansion (globbing), not just word-splitting, and no site disables it — investigated, deliberately not fixed (CodeRabbit review, PR #998, ADR-064's effective-format-override fix)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** action/run.sh:2235 `set -- ${INPUT_EXTRA_ARGS:-}` and :3893 `CMD+=($INPUT_EXTRA_ARGS)` are unquoted, with no set -f around them. set -f appears only at :189 in _split_legacy_value.

`_effective_format()` (added by that PR),
`_extra_args_has_write_flag()`, `_extra_args_write_json_path()`, and the
real command assembly (`CMD+=($INPUT_EXTRA_ARGS)`) all read
`$INPUT_EXTRA_ARGS` via an unquoted `set --`/array-append expansion, which
bash expands for both word-splitting AND filesystem globs. A crafted
`extra-args: '*'` (or any value containing a bare `*`/`?`/`[...]`) run in a
workspace that happens to contain a file whose name looks like a CLI flag
(e.g. `--format=json`) would have that filename silently substituted in as
a real argument -- an unintended, workspace-content-dependent flag
injection. `add_flag`'s sibling `_split_legacy_value` already hardens
against exactly this class (`set -f`, Codex/report finding P2.2), so the
precedent for fixing it exists.
**Not fixed here**, for a reason specific to this PR: `_effective_format()`
exists only to predict, from `$INPUT_EXTRA_ARGS`, what `--format` value the
real `CMD+=($INPUT_EXTRA_ARGS)` expansion will actually produce -- so it
reads that variable the *same* (unsafe) way on purpose. Disabling globbing
in `_effective_format()` alone while leaving `CMD` assembly unprotected
would not close the vulnerability (the real invocation would still glob)
and would *introduce* a new divergence between what this detection
function predicts and what Click actually receives -- worse than today's
status quo of "both glob identically, so they can't disagree." Closing
this properly means hardening all four sites together in one coordinated
change (`CMD` assembly, `_effective_format`, `_extra_args_has_write_flag`,
`_extra_args_write_json_path`), verified against a hostile-glob test
corpus the way `test_action_run_sh_helpers.py`'s
`TestAddFlagHostileScalarCorpus` already exists for `add_flag`/
`add_sided_flag` -- a scoped, standalone follow-up, not a drive-by change
bundled into a PR whose actual objective was the effective-format fix
itself.

### `BundleFacts` (and its G40 archive container) has no published JSON Schema, in either `abicheck/schemas/` or `docs/reference/schemas/`

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/model/bundle_facts.py:53 BUNDLE_FACTS_SCHEMA_VERSION = 4, but abicheck/schemas/ and docs/reference/schemas/v1/ hold no bundle_facts schema (only aggregate/audit/audit_set/build_evidence/build_source_pack/compare). The module path in the entry is stale (bundle_facts.py is now model/bundle_facts.py).

—
investigated, not fixed (Codex review, CLI cleanup phase two's PR I
"artifact_type discriminator" prerequisite). That PR's own plan text
states the ordinary "Merge criteria" machine-contract obligations
(packaged *and* documented schema copies, JSON Schema validation) apply
when a manifest changes, and the bump `BUNDLE_FACTS_SCHEMA_VERSION` (in
`abicheck/bundle_facts.py`) got for the new `artifact_type`/
`BUNDLE_ARCHIVE_ARTIFACT_TYPE` markers is exactly that kind of change —
but a repo-wide search confirms neither
container has ever had a schema file: `abicheck/schemas/` covers
`compare_report`, `aggregate_report`, `build_evidence`, and
`build_source_pack` only, and `docs/reference/schemas/v1/` mirrors that
same set. This is a pre-existing gap predating this PR (the container
has existed since G38 Phase 2 with no schema at any version), not one
this PR's own diff introduced or made worse. Not fixed here because
authoring a first JSON Schema for a format with no existing schema
infrastructure (`scripts/publish_schemas.py`'s packaged/documented-copy
machinery, plus real validation tests) is a substantial, separate
deliverable — not a narrow addition to a field-and-classifier PR — and
because `BundleFacts`' own shape is still scheduled to change again
shortly: PR I's own `BundleCompareRequest` unification may still touch
this container's fields before the format truly stabilizes, and
authoring a schema now only to revise it again for that landing would
be wasted work on the exact same axis. **Correction (2026-09-04,
Codex review, PR #1050): `GateOptions` itself is no longer a blocker
here** — ADR-064's own dedicated slice landed it 2026-09-02
(`abicheck/policy/release_gate_options.py`); PR I's own
`BundleCompareRequest` unification simply hasn't landed yet, for
reasons unrelated to `GateOptions`. Tracked here rather than deferred silently;
the schema-authoring work belongs with (or immediately after) whichever
PR actually stabilizes `BundleFacts`' shape at its current
`BUNDLE_FACTS_SCHEMA_VERSION` (`abicheck/bundle_facts.py`) -- the
`BundleCompareRequest` PR itself, or a dedicated follow-up if that PR's
own scope doesn't naturally include it.

### The weekly `Mutation testing` scheduled lane (`.github/workflows/mutation.yml`, job `mutmut (detector core)`) can outgrow its own job timeout before producing a receipt — investigated, partially mitigated, not fully fixed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** .github/workflows/mutation.yml:327 timeout-minutes: 355 (mitigation landed). The entry itself says there is no measurement that a full run completes, and only_mutate scoping/splitting is not done.

The job's `timeout-minutes` was originally set to 240
with a comment recording that a full baseline run "has taken just over
two hours" at the time (2x headroom). `only_mutate`
(`pyproject.toml`'s `[tool.mutmut]`) has grown since — the module map's
own note under "Test-quality gates (beyond line coverage)" in `AGENTS.md`
records it "now covers identity, suppression and serialization alongside
diff_*/checker_policy" — and the scheduled run on 2026-08-31 (job
`99506019384`) ran the full 240 minutes and was cancelled by that exact
timeout without producing a `mutation-receipt.json`: its own "Run
mutation testing (baseline drift)" step shows starting, then nothing in
the job log until GitHub kills it at the wall-clock ceiling. The three
most recent weekly runs before that (2026-08-17, 08-24, 08-31) all ended
`cancelled` this same way, meaning the per-module baseline-drift gate
had not actually completed a real weekly comparison in that whole
window — a silent gap in exactly the class of coverage `AGENTS.md`'s
own "Mutation testing" section describes as this repo's strongest
test-quality signal, though not a *silent* failure on GitHub's own Actions
tab: the workflow's existing "Flag a cancelled or incomplete run" step
already turns a cancellation into a loud step-summary warning, it just
doesn't make the run finish.
**Mitigated, not closed:** raised `timeout-minutes` to 355 — effectively
GitHub-hosted runners' own hard 360-minute per-job ceiling (not something
a workflow can raise higher), minus a few minutes of buffer for this
job's surrounding checkout/install/save/upload steps. This gives roughly
50% more headroom than the run that was observed failing, but there is no
measurement confirming a full run now completes within it — reproducing
that would mean deliberately running (and waiting out) another multi-hour
scheduled job, which wasn't done here. `mutmut`'s own `max_children`
already defaults to `os.cpu_count()` (parallel mutant execution is not a
missing lever), so if 355 minutes still isn't enough, the real fix is
scoping `only_mutate` down or splitting the weekly run across multiple
scheduled invocations (e.g. half the module list per run, in rotation)
rather than requesting a runner tier this repo has not established it has
access to. Recorded here rather than claimed fixed, per this repository's
own "generalize, or record the gap" convention — this was a direct
timeout-value bump for an observed cancellation, not a verified capacity
fix.

### `--exclude-header` cannot stop a transitively-included header being analyzed

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The descriptor half is moot because the ABICC compat front end was removed (the entry says so). The transitive-include limitation still applies to native --exclude-header, since filtering acts on the resolved header list (entry line ~7008).


**Update (2026-10): the descriptor half is moot.** The ABICC `compat` front
end, its XML descriptors and `model/header_skip_rules.py` were removed, so no
run reads `<skip_headers>`/`<skip_including>` any more. The limitation below
still holds for native `--exclude-header`, which shares it exactly.

**The glob question this entry used to record is closed.** It asked whether
real ABICC globs in `<skip_headers>`, and left descriptor skips matched as
exact basenames or paths in the meantime -- under which every tree-relative
rule (Intel MKL's own `fftw/fftw.h`, `fftw/offload/`) matched nothing at all
while the run still produced a confident verdict. The evidence the entry
asked for is in ABICC's own source: `Internals/Path.pm`'s `classifyPath` and
`Internals/Filter.pm`'s `skipHeader_I` classify each rule into one of three
classes -- a bare name matched against the basename, a value containing a
separator matched against the path at component boundaries (including a
directory's descendants), and a metacharacter-bearing value compiled as a
regex-like pattern. `abicheck/model/header_skip_rules.py` is that
classification, and the descriptor's snapshot now records
`excluded_header_matching = "abicc"` rather than `"exact"`, since the same
text genuinely names a different set of headers under the two rules.

Rewriting every rule to a bare basename was considered and rejected: it fixes
MKL and over-excludes an unrelated `version.h` under a different subtree, and
no example test drawn from the reported case can tell the two apart.

**What is still open** is the other half of `<skip_headers>`'s meaning. ABICC
distinguishes two elements -- `<skip_headers>` is "do not include *and do not
analyze*", `<skip_including>` is "do not include directly, but still analyze
when reached" -- and the two are now modelled separately, with only
`<skip_headers>` recorded as an achieved narrowing of the surface. Both
correctly drop a header from the *direct* `-H` operand list. Neither can stop
a header being parsed when another header reaches it through its own
`#include`, because filtering happens on the resolved header list after the
directory walk and before any parse. The native `--exclude-header` path
shares that limitation exactly (`extract/header_exclusions.py`), which is why
closing it is a post-parse filter by defining header rather than a change to
either rule language -- and why it is recorded here rather than patched on
the descriptor side alone.

Until then, `docs/reference/abicc-format-compliance.md` states partial
support for `<skip_headers>`, and the two elements' remaining difference is
in what a run *records*, not in what it parses.

### `bundle_facts_store.read_bundle_facts_package` holds a bundle's raw JSON and its decoded `AbiSnapshot`s concurrently at peak

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/import_bundle_facts.py:597 export_bundle_facts still returns one whole dict[str, Any] document (no streaming form). It is bounded by DEFAULT_MAX_BUNDLE_DECODED_BYTES, as the entry notes.


A Codex review round on PR #1054 (the Track B/C duplicate-multi-artifact-
writer reconciliation) found that delegating `read_bundle_facts_package` to
`storage.import_bundle_facts.export_bundle_facts` regressed peak memory
behavior relative to the reader it replaced. The removed reader converted
each artifact's document to an `AbiSnapshot` immediately inside its
per-artifact loop (`per_library_snapshots[library_name] =
snapshot_from_dict(document)`), discarding the raw JSON document each
iteration; `export_bundle_facts` instead accumulates every artifact's raw
JSON document into one `per_library_snapshots: dict[str, Any]` and returns
it as a whole document, and `bundle_facts_from_dict()` then decodes every
entry in one pass — so at peak this can hold both the full set of raw JSON
document trees and (once decoding starts) their `AbiSnapshot` counterparts
concurrently.

Not fixed: closing this fully means changing `export_bundle_facts`'s own
return contract from "one complete `bundle_facts_from_dict()`-shaped
document" (its module docstring's stated contract, shared with
`storage/import_baseline_set.py`'s sibling adapter) to a streaming/
generator form the caller converts and discards from as it goes — a change
to the shared `storage.import_bundle_facts` module's public shape, not a
caller-local fix, and outside what that PR (reconciling the Track B/C
duplicate writers onto that one shape) was scoped to redesign. It is
bounded in the meantime, not unbounded: `read_bundle_facts_package`'s own
`DEFAULT_MAX_BUNDLE_DECODED_BYTES` check runs *before* `bundle_facts_from_dict`
is ever called (raising during the incremental per-artifact charge if the
raw documents alone already exceed the budget), so peak memory here is
capped at roughly the charged-byte budget's raw-JSON cost plus its decoded-
object cost — a bounded constant, not a growth path uncorrelated with any
limit this module already enforces. If this becomes a real reported
problem rather than a review-found edge case, the honest fix is reworking
`export_bundle_facts`/`import_baseline_set`'s shared adapter shape to
stream per-artifact results to the caller instead of returning one
document, which both known callers would need to be updated for together.

### Dependency static/dynamic linking-mode change has no ChangeKind

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** docs/contribute/abi-taxonomy-coverage.md:177 still lists dependency-abi.linking-mode-change as NOT_IMPLEMENTED, and no ChangeKind containing linking_mode exists in abicheck/model/change_catalog.


Found by Phase 2/3 of
[ABI/API knowledge and corpus](plans/abi-api-knowledge-and-corpus.md) —
the one taxonomy leaf (`dependency-abi.linking-mode-change`,
`docs/contribute/abi-api-failure-taxonomy.md`) that no `ChangeKind` claims
at any evidence tier, and therefore the only `NOT_IMPLEMENTED` row in
[the coverage matrix](abi-taxonomy-coverage.md).

When a dependency switches between static and dynamic linking, the
dependency's own symbols change status wholesale: previously resolved at
load time through `DT_NEEDED` (or the PE import table / Mach-O load
commands), they are now duplicated into the artifact itself, with their own
visibility, ODR exposure, and interposition behaviour changing with them —
and the reverse when a vendored dependency is unbundled. Nothing in the
registry names that transition. Its consequences are *partly* observable
through unrelated kinds — `needed_removed` when the dependency leaves the
needed list, `symbol_leaked_from_dependency_changed` /
`visibility_leak` when its symbols surface in the artifact's own export
table — so a real occurrence produces two or three findings that each
describe a symptom and none of which says what happened.

Not fixed here: Phase 2/3 of that plan is a mapping and classification
pass, and the plan's own "Out of scope" section rules out any detector,
evidence, or `ChangeKind` change (a `NOT_IMPLEMENTED` finding is recorded,
not fixed, by it). Tractable when it is picked up: both sides' needed/import
lists and export sets are already collected at L0, so the evidence a
detector would join is present today — what is missing is the join, a kind,
and the "vendored vs. unbundled" direction in its `impact` text. Note the
adjacent, deliberately *unclaimed* half: whether the newly-static
dependency's code is ABI-compatible with what consumers already linked is a
question about a third artifact abicheck was not given, which is
`dependency-abi.transitive-break`'s territory, not this leaf's.

### AArch64 AAPCS64 by-value aggregate passing is not modeled

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/dwarf_advanced.py:885-893 models only SysV AMD64 (_SYSV_AMD64_RETURN_ARCHES), and its comment says an AArch64 HFA flip is just a generic value-ABI change.


A struct passed or returned by value on AArch64 travels in SIMD registers
when it is a homogeneous floating-point or short-vector aggregate (HFA/HVA,
1-4 members of one type), in general registers when it is at most 16
bytes, and indirectly otherwise (AAPCS64 §5.9.5). A change that moves an
aggregate across one of those boundaries without changing its size -- one
`float` member becoming an `int` -- breaks every compiled caller, and no
`ChangeKind` reports it: the value-ABI trait diff
(`dwarf_advanced._diff_value_abi_traits`) models only the SysV AMD64
register/indirect rule and treats an AArch64 trait flip as a generic
value-ABI change. `docs/reference/platforms.md` already lists HFA/HVA
drift as not detected.

A classifier for those boundaries
(`macho_metadata.classify_aapcs64_aggregate`) existed as an unwired,
unit-tested "modeling primitive"; the plan that was to wire it (G1) closed
without doing so, and the dead-code plan's Stage D removed it (it is in git
history, with its unit tests, `tests/test_macos_arm64_abi.py`). Wiring it is not a
Mach-O concern -- AAPCS64 governs AArch64 ELF too -- so a fix belongs in the
value-ABI trait path for `target_arch == "aarch64"`, needs member base
types from DWARF, and needs an AArch64 toolchain to validate against real
binaries, which this environment does not have.

### A resolved PDB/DWP/dSYM artifact reaches no dump path

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/dumper.py:1089 _dump_pe has no debug_roots parameter, and dwp_path/dsym_path are referenced only in extract/debug_artifact.py (no dump consumer). The directory form still resolves PDB/DWP/dSYM artifacts that are then dropped.


`debug_resolver`'s chain can resolve four kinds of artifact, and only one
of them is consumed. `service_dump_native._dump_elf` reads
`DebugArtifact.dwarf_path` and nothing else -- a `dwp_path`, a `dwo_dir` or
a `dsym_path` is resolved, logged, and dropped -- and the PE branch of
`_run_dump_uncached` never passes `debug_roots` to `_dump_pe` at all, so a
PDB found in a debug root is dropped too (the PE path's only PDB input is
`debug.pdb_path`, via `locate_pdb(pdb_path_override=...)`).

**Pre-existing, and predates plan Phase 7n** -- `--debug-root` had the same
shape. What 7n changed is that the gap became *sayable*: merging the debug
role into one input meant naming a detached artifact directly, which would
have resolved a PDB/DWP and then silently ignored it, and (first review
round) made the resolver return one *ahead* of `EmbeddedDwarfResolver`, so
a binary carrying perfectly good DWARF would have been compared with none.
Both are closed: `extract/detached_debug.DetachedDebugFileResolver` is
DWARF-only and yields to the rest of the chain, and naming a PDB/DWP file
is a usage error naming `debug.pdb_path` (the spelling that *is* read).

The *directory* form is deliberately still accepted, because rejecting it
would break the case that works -- a build-id tree or path mirror of ELF
`.debug` sidecars, which is what the input is mostly for. So a directory
that happens to hold PDBs still resolves to an artifact nobody reads. That
is this gap, unchanged; do not "fix" it by rejecting directories.

Closing it properly means threading `debug_roots` into `_dump_pe` and
teaching `_dump_elf` to consume `dwp_path`/`dwo_dir`/`dsym_path` -- real
capability work on the extraction paths, not a CLI change, which is why
ADR-068's flag-topology slices leave it alone. Found by Codex review on
PR #1253.

**Update (plan Phase 7n):** `--debug-root`, `--devel-pkg` and
`--probe-matrix` no longer exist as flags -- each merged into the one input
for its evidence role (`--debug-info`, `-H/--header`, `--build-info`). The
rejections above are unchanged in substance and still keyed on the
generated destinations (`devel_pkg2`, `probe_matrix_*`, ...), which is what
made them survive the rename; only the spelling each message names moved.
Writing the test found one the manual audit had missed
(`--used-by-manifest`), which is the argument for the mechanism in one line.

The whole compute/render split is new in this pass too:
`report/no_baseline.py` now follows `abicheck/report/AGENTS.md`'s convention
— one `compute_no_baseline_document` resolving per-finding verdict/category,
evolution counts, coverage and exit contributions, and pure `render_*` halves
that format and decide nothing — so a new audit report section goes in the
document, never into one renderer. The audit publishes its own schema
(`abicheck/schemas/audit_report.schema.json`, `audit_report_schema_version`
starting at `1.0`) rather than a version of the compare report's: `findings[]`,
`cross_source_evolution`, `exit_axes` and a candidate-only
`pattern_preprocessor_scan` block are its own, and `changes: []` stays, so a
consumer reading `changes` off any abicheck report still finds it. It briefly
stamped `report_schema_version` instead, which offered the audit under the
compare report's identity -- see that field's own note in
`report/no_baseline_document.py` for why that is not merely cosmetic.

### Interpreter startup dominates a small-input CLI run

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Measurement only. The entry says import cost was never investigated, and no lazy-import work is recorded; the Click registration-by-import pattern is unchanged. unverified: wall time was not re-measured.


`abicheck --version` — interpreter plus import plus Click tree construction,
before any work at all — costs **0.56–0.66 s**. A stored-snapshot/stored-snapshot
`compare` of a small fixture costs ~1.05 s end to end, so roughly **60% of it is
startup**. `compare --dry-run` (startup plus config and input resolution, diff
never run) costs 0.60–0.79 s, which bounds resolution itself at well under
0.2 s.

Consequences worth knowing before optimizing anything else: a change that halves
the actual L2 comparison work would move that run's wall time by under 20%, and
the full-CLI lane's absolute noise floor has to be set around this cost rather
than around the comparison's (hence `--regress-min-delta-seconds 0.5` by
default there, where the in-process gates use 0). A warm-cache `dump` is
*not measurably faster* than a cold one on a small fixture for the same
reason — see below.

Not investigated: which imports dominate, and whether lazy-importing the heavy
ones is feasible without breaking the registration-by-import-side-effect pattern
the CLI is built on.

### The `clang -M` include-graph pass is per-header and cached only in-process, never across runs

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** abicheck/buildsource/header_include_memo.py now holds a process-wide memo, keyed in header_graph.py:635-650, so release members and sides no longer re-run the same clang -M set. Still open: the memo lives only in-process (never persisted across runs), and nothing batches per-header invocations.


Measured on the header-count axis (one library, N top-level public headers over
one shared `detail/` dependency closure, live/live comparison):

| top-level headers | observed `include_pass` invocations | wall |
|---:|---:|---:|
| 1 | 2 | 1.10 s |
| 8 | 16 | 2.48 s |
| 32 | 64 | 10.40 s |

Two separate things show up here. The invocation count is exactly
`headers × 2 sides` — one `clang -M` subprocess per top-level header per side,
with no batching across headers that share a dependency closure. And the wall
time grows **superlinearly** in header count (8x the headers costs ~9.5x the
time going 1→32), so this is not merely a constant per-header cost.

The separate cache finding: on a warm AST cache the header *extraction* count
drops to zero (the AST cache serves it) while the `include_pass` count stays
unchanged. The include graph is recomputed on every run regardless of cache
state. Combined with the startup cost above, that is why a fully warm small
`dump` measures ~0.93 s against a cold ~0.88 s — within noise, i.e. the cache
hit buys nothing observable at that scale even though it demonstrably happened
(proved by the counters, not the clock).

### Multi-library L2 compare scales quadratically with library count

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The memoization landed: the libraries budget is 1.7 in scripts/check_l2_scaling_perf.py:103 and header_scan_memo/include memo exist. Still open: every member walks the union header set and runs the per-member field parse (step 2 of entry 73).


Found by `scripts/check_l2_scaling_perf.py` (2026-09). A directory `compare`
of N generated libraries (2 public headers each, shared dependency context,
`--depth headers`, cold cache, 4 CPUs) costs:

| libraries | 1 | 3 | 6 | 7 | 10 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| wall (s) | 1.3 | 2.2 | 5.0 | 6.9 | 11.5 | 32.2 |

Above the ~1.2 s fixed floor, the added cost grows with a marginal exponent of
~1.6 over 1–10 libraries and ~1.9 between 7 and 16. The directory compare is
therefore close to quadratic, not linear, in the member count. Peak RSS stays
flat (~230 → 365 MB), so this is time, not memory.

**Partly fixed (2026-09).** py-spy (the first cProfile reading misattributed
worker-thread time to `bundle_symbol_status`) showed the per-member time
going to work over the union header set. The two largest pieces are now
memoized:

- extraction-contract fingerprinting resolved paths on every dependency ×
  header/include-root test, about 35% at 16 libraries. It now resolves each
  path once per computation (`comparability_fields`).
- the C++20 dialect scan ran several times per dump. It is now
  content-validated and memoized in-process (`extract/header_scan_memo.py`).

Result: 10 libraries 11.5 s → 8.6 s and 16 libraries 32.2 s → 19.1 s, with
the marginal exponent over 1–10 falling from 1.64 to 1.39. The gate budget is
now 1.7.

**What remains** is many small per-member walks of the union set: header
expansion, inferred include roots, dependency-scope roots, one `clang -M`
include probe per header, and the include-graph gate's deliberate per-probe
memory re-read. Each is linear per member, so the release is still
super-linear. This is the performance face of the "`-H`/`--header` set is
applied to every member" entry below. Its steps 2/3 remove all of these at
once: give each member only its own headers, or compute the release-scoped
part once. Lower the budget toward ~1.1 when that lands. The oneDAL receipts
(`performance.md`, "oneDAL solo L2 compare") involve a handful of libraries
with 125 header roots, which is where this term shows up at real scale.

**Measured on MKL (2026-09-27): the header *parse* is not per member.** A
reported "56 header parses for 28 members" counted the `header AST parse`
progress lines, which one member dump emits whether it runs the frontend or
waits on another worker's run. Instrumenting a 3-member MKL release
(2024.2.2 → 2025.2.0) showed the frontend runs once per side per AST key,
shared through the request's acquisition table (`dumper_cache.
run_ast_acquisition`). The one real duplicate was the release public-surface
parse keying apart from the member dumps on a relative `-I` spelling (fixed:
`resolve_header_ast_result` absolutizes for every caller), which took castxml
runs from 4 to 2. What *is* per member, measured: the export-bound field
parse over the shared DOM (`extract/header_ast_fields.
_parse_header_ast_legacy`, dominated by `parse_functions`: ~2.5 s alone,
~7 s under 4-way contention, per member-side). It is per member on purpose --
visibility, constructor/destructor fallbacks and the surface facts are
decided from that member's export table while the declaration is built
(that module's own docstring) -- so sharing it means splitting each
`Function` into an export-neutral parse plus a per-member binding step. That
split is the remaining lever for a large release, and the RSS it would save
is the per-member declaration lists, not DOMs.

### The `clang -M` include pass is unaffected by a warm AST cache — confirmed at `--repeat 3`

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** header_include_memo.py is a process-wide memo with no persistent/AST-cache tier, so a new run still recomputes the include graph whatever the warm AST cache holds.


Re-confirmed after the cache-lifecycle fix below, now that a multi-repetition run
actually works: across three repetitions of the cold/warm sequence the header
*extraction* count goes 2 → 0 (the AST cache serves it) while `include_pass`
stays at 1 every time. The include graph is recomputed on every run regardless of
cache state, which is the same finding as the per-header scaling above seen from
the cache side.

### A labelled side-scoped `--include` appeared to suppress unlabelled global include roots

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: the entry itself has no minimal reproduction. The labelled/unlabelled --include merge in frontends/cli/options/params.py:187+ was not exercised with castxml here.


Observed once, on a real PVXS comparison, and **not yet reduced to a minimal
reproduction** — recorded so the next person does not lose the observation.
Invoking:

```
compare old.so new.so --include old:public=OLD_INC --include new:public=NEW_INC \
  --include EPICS_INC --include EPICS_INC/os/Linux --include EPICS_INC/compiler/gcc ...
```

failed with `fatal error: 'epicsTime.h' file not found`, despite `EPICS_INC`
being passed as an unlabelled (both-sides) include root and genuinely containing
that header. The same roots passed to a bare `dump` worked. Re-expressing every
root in the per-side labelled form made the comparison succeed.

So either unlabelled roots are dropped once any labelled side-scoped root is
given for that side, or they are ordered such that castxml does not see them.
`--include`'s own help text documents the two forms as composable, so if this
reproduces it is a defect rather than a usage error. Next step: a minimal
two-header fixture mixing one labelled and one unlabelled root.

### Retired `--crosscheck` promotion axis still serialized in ExitDecision and read by action/run.sh

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The engine half is gone: scan_engine.py/cli_scan*.py are deleted, policy/exit_decision.py:147-154 marks PROMOTED_CROSSCHECK a read-only retired producer, and resolve_exit_decision no longer takes it. Residue: ExitDecision still serializes crosscheck_promotion_contribution (exit_decision.py:322,435; compare_report.schema.json:2058) and action/run.sh:5125,5183-5191 still reads promoted_crosscheck.


ADR-068's second 2026-09-09 amendment rules `--crosscheck KEY=error`'s
promotion syntax **(b) — dropped, superseded**: every cross-source check
already reaches `compare` as an ordinary `ChangeKind`, so the underlying
capability (controlling one check's severity) exists as
`policy.overrides.<CHANGE_KIND>: error` in `--policy`/`.abicheck.yml`.

Phase 4's typed-API slice (2026-09-09) deleted the half it owned:
`ScanRequest.severities`/`enabled_checks` are gone with the request type, and
no typed caller can state either any more. **The `scan` CLI flag and its
engine plumbing are still there**, deliberately. Its promotion is not a
`scan`-local detail — it is ADR-064's `crosscheck_promotion_contribution`, a
*published* `ExitDecision` axis:

- `policy/exit_decision.py` carries the field and `resolve_exit_decision`'s
  parameter; it is emitted in every persisted `exit` block.
- `policy/exit_decision_precedence.py` carries it through a prior decision.
- `workflows/scan_abort_result.py` names it as a fold participant for a
  budget/evidence-contract abort.
- `schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` 2.42 entry documents it on
  `compare`'s side of the report contract, and `action/run.sh` reads
  `promoted_crosscheck` out of `blocking_categories` to word its own verdict
  escalation.
- `scan_engine._crosscheck_severity_exit`/`_promote_published_gate` and
  `cli_scan_baseline`'s `info`/`warning` non-gating exclusion implement it.

Removing a published exit axis changes `compare`'s report schema as well as
`scan`'s and needs an ADR-064 amendment deciding what a consumer reading
`crosscheck_promotion_contribution` should see afterwards — a different
change from retiring a request field. It is not done in the typed-API slice,
and the slice does not pretend otherwise.

**What closing it looks like:** delete `--crosscheck` from `cli_scan.py`
(both halves — the `KEY=off` enable/disable side has no separate ruling, but
it is the same flag and `compare` runs every check unconditionally per plan
§3 #3), delete `_parse_crosschecks`/`_CROSSCHECK_LEVELS`/
`_PROMOTABLE_FINDING_KINDS`, drop `severities`/`enabled_checks` from
`run_scan_core`/`_run_baseline_compare`, delete
`_crosscheck_severity_exit`/`_promote_published_gate`/
`CROSSCHECK_BLOCKING_CATEGORY`, retire the `ExitDecision` field under an
ADR-064 amendment with its own `REPORT_SCHEMA_VERSION`/`SCAN_SCHEMA_VERSION`
entries, and update `action/run.sh`'s two `promoted_crosscheck` readers plus
`pr_comment_scan.py`'s `crosscheck_severities` promotion read.

### A directory `compare` still feeds the union `-H` set to every member (no per-member header operand); unreached type findings still count in member verdicts

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Step 1 (dedupe), the public_not_exported release-level half and the step-3 attribution report (workflows/release_member_attribution.py, policy/member_type_attribution.py) have landed. Still open: step 2 (a per-member header operand, plus the quadratic union-header walk) and keeping proven_unreachable findings out of member verdicts.


**Update (2026-09-17), read this first.** The release product model landed:
a directory/package comparison now judges **one** public contract backed by
**several** binary providers (`docs/learn/products-not-libraries.md` § "One
public surface, many providers"). Concretely, against the three steps this
entry proposes below:

- **Step 1 ("de-duplicate first") is done.** A finding several members report
  identically is rendered once, release-scoped, naming every affected library
  (`report.release_public_surface.dedupe_shared_member_findings`), and each
  member entry records how many of its findings were folded there
  (`product_level_findings`). Counts, verdicts and exit codes are untouched by
  the fold, so nothing is hidden or downgraded — only the duplicated evidence
  stops being emitted N times.
- **The `public_not_exported` half is not merely de-duplicated, it is
  answered correctly.** It was never a duplication problem at heart: the
  obligation is satisfied by *any* member, so the per-member answer was a
  Cartesian product. It now runs once, at release level, against the union of
  the bundle's usable exports (`policy.release_contract_reconciliation`), with
  the member pass no longer running it at all
  (`workflows.crosscheck_ownership`). Measured on a 12-library / 480-declaration
  fixture: 5,280 findings -> 0, report 8.7 MiB -> 77 KiB.
- **`exported_not_public` stayed per member, deliberately.** The example below
  reads it as the same defect, and it is not: the exporting member *is* its
  correct attribution. It is judged against the one shared declaration index
  and folded only when several members really do export the same symbol.
- **Steps 2 and 3 remain open exactly as written below** (a per-member header
  operand; reachability-based attribution where no map is declared). The type
  findings in the example above are still not *attributed* to the member whose
  surface reaches `Widget` — they are now reported once for the release,
  naming both members, which is the honest "which member this affects was not
  established" reading step 1 asks for, not the attribution step 3 would give.
  Over-reporting attribution, not duplication, is what is left.

**Performance face of the same gap (2026-09).** Every member walks the
union header set, so the release cost grows super-linearly with its member
count. See "Multi-library L2 compare scales quadratically with library count"
above for the measurements, what has been memoized, and why steps 2/3 in this entry
are the complete fix.

**Update (2026-09-18).** The model is no longer scoped to the live
directory/package fan-out. The other three drivers that compare several
libraries at once — a stored `BundleFacts` baseline against a live release
(`workflows.release_public_surface.stored_old_live_new_reconciliation`), two
stored documents (`workflows.bundle_stored_pair_compare`), and a
multi-library ABICC descriptor (`compat.multi_library_run`, removed with the
`compat` front end in 2026-10) — each reconcile
one product contract through the same
`workflows.release_public_surface.reconcile_member_sets` and enter the same
`member_pass_scope` ownership for their member pass. What remains scoped to
the live path: only that path acquires a surface (the other three read a
recorded or projected one), so the acquisition ledger's instrumentation is
empty on a stored comparison, and only that path performs the
`shared_findings` fold — the stored documents already emit one product-level
finding rather than repeating it per member. The driver inventory is not
mechanically enforced: nothing fails when a fifth multi-member driver is
added, so `tests/test_release_public_surface_drivers.py` is a hand-maintained
list (recorded in `tests/regressions/manifest_classification.py`'s
`release.cartesian_product_contract` known gaps).

**Known dormant branch (2026-09-18).** `storage/bundle_facts_archive.py`'s
public-surface blob carries the same second-materialization byte charge
`manifest_blob` does (a blob served from cache still builds a second object
graph, so its bytes are charged again -- the object-count amplification the
aggregate budget bounds). Through the public loader that branch is
currently **unreachable**: every other slot that could share the surface's
hash decodes it under a different shape first (the instantiation manifest
demands a top-level `provides:` list, a library slot demands a snapshot),
so a shared hash is refused before the surface slot runs. The guard is kept
because it is cheap and becomes load-bearing the moment slot ordering or a
shape check changes;
`tests/test_release_public_surface_persistence.py::TestTheSurfaceBlobIsReadExactlyOnce`
pins the property that makes it dormant, so that change cannot happen
silently. It is also why that file's own patch coverage will not reach
100% -- the uncovered lines are this guard, and covering them would mean
reaching past the public entry point, which this repo's own
third-party-boundary rule refuses.

**Update (2026-10-01): step 3 landed as attribution, not yet as a
verdict change.** `workflows/release_member_attribution.py` computes each
member's export surfaces and `policy/member_type_attribution.py` answers,
per type finding, `reaches`/`proven_unreachable`/`unestablished`; the
release fold attaches that partition to each product-level finding
(`shared_findings[].attribution`, release schema 1.11). On the example
below, `Widget` is now `reaches: libfoo.so`, `unestablished: libbar.so` —
libbar's only export has no declaration, so its reach cannot be decided,
and saying so is the honest answer. What remains open:

- **Member verdicts and counts still include unreached findings.**
  `libbar.so` above is still `BREAKING` on `Widget`. Excluding a
  `proven_unreachable` finding from a member's own verdict is the policy
  half; it was deliberately not bundled with the report half, because it
  changes exit codes and `--contract exports` already offers that scoping
  explicitly per member.
- **Step 2 (a per-member header operand) is still open**, and with it the
  quadratic cost: every member still parses the union header set.
- Making this work exposed an `export_surface` defect that was its own
  bug: the C tag idiom `typedef struct X {...} X;` was flagged as an
  ambiguous name, so every such type was undecidable under
  `--contract exports` too. Fixed in the same change.

The original entry follows, unchanged.


Reproduced while verifying the `--depth`/header-graph behaviour above, on a
two-member fixture (`libfoo.so` built from `foo.h`, `libbar.so` from nothing
but its own source):

```
abicheck compare old/ new/ --header old=inc/foo.h --header new=inc_new/foo.h
libbar.so BREAKING  type_size_changed:Widget, type_alignment_changed:Widget,
                    type_field_type_changed:Widget, type_field_offset_changed:Widget,
                    exported_not_public:bar_fn, public_not_exported:widget_area, ...
libfoo.so BREAKING  type_size_changed:Widget, ... (the same four)
```

`Widget` lives in `foo.h` and is nothing to do with `libbar.so`, yet the
whole `Widget` change set is reported once per member, and `libbar.so`
additionally earns `exported_not_public`/`public_not_exported` findings for
the mismatch between its own exports and a header set describing a different
library. A release's finding count therefore scales with member count rather
than with what changed, and `--output-dir`'s per-library reports carry the
duplicates too.

The cause is a missing *transport*, not a missing model. `cli_compare_release.py`
resolves one `old_h`/`new_h`/`old_inc`/`new_inc` set for the whole release and
passes it unchanged to every `_compare_one_library` call — but that function
already takes its header list **per member** (`cli_compare_release_pairwise.py`'s
`old_h` parameter, forwarded straight to `_run_compare_pair`); only the caller
flattens it. And the per-member mapping already exists upstream:
`build-output.json` carries per-target `public_header_roots`/
`generated_header_roots`, `buildsource/baseline_publish.py`'s
`derive_baseline_libraries()` turns those into `actions/baseline`'s
`libraries[].header`, and `publish-baseline.yml` already dumps **each library
with its own headers** at a selectable `depth`. `BundleSpec.targets` is a list
of target ids and `TargetSpec.public_headers` exists per target, so the
declaration is in the project schema too.

So an earlier revision of this entry was wrong to frame this as "decide what
the attribution model is" and to propose naming conventions or a new
`.abicheck.yml` key. The model is declared; `compare` simply has no operand
shape that carries it. Three things close this, in increasing order of scope:

1. **De-duplicate first.** A header-derived finding that cannot be attributed
   to one member should be reported **once**, release-scoped, rather than once
   per member. That is the honest reading of the evidence ("which member this
   affects was not established") and it removes most of the pain with no
   guessing at all. The current N× duplication is the actual defect.
2. **Carry the map into `compare`.** A per-member header operand (mirroring
   `actions/baseline`'s own `libraries` JSON) so the fan-out resolves
   `old_h`/`new_h` per member. Small, given the seam already exists.
3. **Attribute by reachability** where no map is declared: a header-derived
   finding belongs to the member(s) whose export surface reaches the entity —
   `export_surface.compute_export_surface` and `type_reachability.py` already
   do exactly this per library. Must fall back to (1), never to silence.

Note that this is only half of header-aware package comparison. The other
half is where OLD's headers come from at all:
`buildsource/project_targets.py`'s `BUNDLE_CHECK_DEPTHS = {binary}` exists
because a bundle baseline stages raw binaries and `check-project.yml` has one
project-wide `header:` input, so `depth: headers` would parse *both* sides
with the current checkout's headers and make a header-only change silently
invisible — a false negative, strictly worse than this entry's over-reporting.
Staging bundle baselines as per-member snapshots (what single-target mode
already does) is what would let that restriction be relaxed.

Deliberately not attempted as part of the `--depth` fix: it is a separate
change in a different layer. Over-reporting is the safe direction — no finding
is *lost* today — which is why this is a gap rather than a blocker on the fix
that surfaced it.

### SARIF suppression justification carries only the display label, not the full disposition record

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The cli_scan_baseline.py half is moot because the file was deleted. The sarif.py half is still open: abicheck/sarif.py:963 still interpolates only change.suppression_rule ('suppressed by --suppress rule: ...') and never reads DispositionLedger.rule_for.


ADR-067 D3 says a disposition keeps the rule that made it — rule id, source
file, reason, label, expiry. `Change.suppression_rule` is not that record: it
is `SuppressionOutcome.rule_label()`'s deliberate `label or reason` collapse,
so a waiver stating both publishes one and silently drops the other, along
with the file it lives in and when it lapses — exactly what a reviewer needs
to decide whether the waiver still applies.

`compare --no-baseline` had this in all four of its projections and was fixed
in PR #1188: each suppressed entry now carries the run's own
`DispositionLedger.rule_for` record, and the regression test is parametrized
over `NO_BASELINE_SUPPORTED_FORMATS` so a format added later is held to it.
Two-sided `compare`'s **JSON** was already correct before that
(`reporter.py`'s `_suppressed_change_entry` emits `rule.to_dict()`).

Two readers are still on the display label alone. Both were found by grepping
every `suppression_rule` reader once the audit's own were fixed, and both are
outside the scope PR #1188 was opened for, so they are recorded here rather
than folded into it:

1. **`sarif.py`** (two-sided `compare`) — the suppression's `justification`
   interpolates `change.suppression_rule` only. Since the same run's JSON
   already publishes the full record, this is a *between-formats* split of one
   report: a SARIF consumer sees strictly less than a JSON consumer of the
   identical run.
2. **`cli_scan_baseline.py`** (`scan --against`) — each suppressed
   `findings[]` entry sets `entry["suppression_rule"]` and carries no
   provenance field at all.

Fixing either is the same shape as the audit's fix: route the run's
`DispositionLedger` to that entry builder and read `rule_for(change)`, rather
than re-evaluating the rule set (which can name a different rule than the one
that actually fired, since a finding's fields may have been enriched after the
match). Neither needs a new mechanism — only the existing one wired to one
more builder.

Registered as open residuals on the `report.finding_entry_builder_parity`
bug class in `tests/regressions/manifest.py`'s report sibling
(`tests/regressions/manifest_report.py`), so the next person to touch that
class sees them without re-deriving the grep.

### `compare`'s migrated cross-source checks drop `--since`'s changed-path confidence boost

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** workflows/cross_source_evolution.py:360 is still `def compute_cross_source_evolution(old, new)` with no changed_paths parameter, and checker.py has no changed_paths.


Found during the ADR-068 Phase 6 doc follow-up (a stale mention of the
retired `scan --since` in `catalog/cases/case181_xcheck_public_to_internal_dependency/README.md`
prompted checking whether the claim still held).

`buildsource/cross_source_checks.py`'s `CrosscheckConfig.changed_paths` lets
a check (currently `public_to_internal_dependency`) report a finding at
higher confidence when the internal declaration it reaches lives in a file
the revision actually changed — "this call reaches a file that changed this
revision" is a stronger signal than "this call reaches *something*
internal". The retired `scan --since` wired its changed-path set through to
this automatically.

`compare()`'s own migrated cross-source-evolution path
(`checker.compare()` → `workflows.cross_source_evolution.
compute_cross_source_evolution(old, new)`) does **not** currently accept or
forward a changed-path set at all — the function's signature is
`(old: AbiSnapshot, new: AbiSnapshot)`, with no `changed_paths` parameter,
even though `compare`'s own `--since`/`--changed-path` flags exist and are
already threaded through to other consumers (`cli_compare_helpers.py`'s
`changed_paths` parameter, ADR-043 D7 POI scoping — comment-tagged
`# ADR-068 Phase 2c: ADR-043 D7 POI scoping`). So `public_to_internal_dependency`
is always reported at the lower "reaches something internal" confidence
under `compare` today, regardless of `--since`/`--changed-path`.

Tractable when picked up, but **`checker.compare()` has no `changed_paths`
parameter to thread today** (verified against its real signature,
`abicheck/checker.py`'s `def compare(old, new, suppression=None, *,
policy=..., ...)` — no `changed_paths` anywhere in it) — an earlier version
of this entry wrongly described the value as already existing on
`compare()` and needing only to be threaded further down. Adding a
`changed_paths` parameter to `checker.compare()` (and updating every
caller that already resolves a changed-path set — `cli_compare_helpers.py`
already has one — to actually pass it through) is itself the first piece
of required wiring work, not a step that can skip straight to modifying
`compute_cross_source_evolution`/`CrosscheckConfig`. Needs a regression
test asserting the confidence *does* change under `--since`/
`--changed-path` on a real (not just internal-API) `compare` invocation,
not only an internal `run_crosschecks(...)` call — the gap here was
invisible to internal tests precisely because nothing exercises the public
`--since` flag's effect on this specific check's confidence.

### `compare --depth binary` skipping matrix-wide `--sources`/`--build-info` is tested only on snapshot operands, which cannot prove it

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** tests/test_scenarios.py:653 test_sc_scan_binary_depth_matrix_args was revived, but per docs/contribute/usecase-registry.yaml:1040-1055 it runs on stored JSON snapshots (tests/scenarios/ci_gating.yaml:120), so it cannot tell 'binary depth skipped collection' apart from 'snapshot inputs always skip it'.


Found while retiring `tests/scenarios/ci_gating.yaml`'s
`SC-SCAN-BINARY-DEPTH-MATRIX-ARGS` scenario and its automated test
(`test_sc_scan_binary_depth_matrix_args`) in the ADR-068 Phase 6 doc
follow-up: the scenario's premise was that a CI matrix builds one command
template and varies only `--depth`, leaving `--headers`/`--sources`/
`--build-info` present on every rung, and the binary rung must ignore the
deeper inputs and skip pattern-scan/L3 collection. The retired `scan`
command exposed this as a checkable `coverage` array
(`layer`/`status` rows) plus a `pattern_scan.files_scanned` field; verified
live, `compare -o json=...` emits neither at any `--depth`. `compare
--depth binary` does reach the same verdict/exit code on the scenario's
fixture, but nothing currently asserts it *skipped* the deeper collection
rather than merely projecting an already-collected superset down afterward
-- the two are observably different only through the now-gone coverage
block.

Tractable when picked up: decide what `compare`'s own coverage-reporting
surface should look like for this question (a `--depth`-scoped rung either
was or wasn't collected), add it, and un-retire the scenario against it.

### ADR-061 gap F: nested `buildsource`/`impact` modules still carry no disposition

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The compat/* modules named in the entry are gone (compat front end removed). Several named nested modules still exist with no architecture/*.yaml mention, e.g. buildsource/build_evidence.py, build_output.py, evidence_policy.py, fact_set.py, merge_support.py, source_graph.py and impact/use_case_impact.py (0 hits).


[ADR-061](adr/061-responsibility-package-architecture.md)'s gap F requires
every unclassified first-party module under `abicheck/` to carry one of
three recorded dispositions (migrate/retain/accept) — "unclassified for
now" is explicitly not one of them. `architecture/dispositions.yaml`
already carries this for every *root* `abicheck/*.py` module (its own
schema requires a direct root path, so it structurally cannot record a
nested one), and `architecture/debt.yaml`'s `files` list requires
`baseline_lines >= 800` (this repo's production file-size cap), so it
cannot record a small nested leaf module either. A repo-wide re-audit for
this gap (enumerating every `.py` file under `abicheck/` not already
covered by a canonical layer directory, a layer's `legacy_paths`, or an
existing `debt.yaml` entry) found 44 such nested modules, all under
`abicheck/buildsource/`, `abicheck/compat/`, `abicheck/impact/`, and
`abicheck/schemas/`. 31 of them were classified by adding them to the
appropriate layer's `legacy_paths` in `architecture/modules.yaml` (the
"migrate through a named responsibility slice" disposition — verified,
not merely asserted, by a full `scripts/check_architecture.py` run showing
no new `dependency-direction`/`dependency-cycle`/`unclassified-import`
findings after the change; three of the thirty-one — `entity_identity.py`/
`entity_resolver.py`/`source_graph_query.py` — are pure re-export facades
and are additionally registered in `architecture/modules.yaml`'s `facades`
list so `_check_facade`'s delegation-only rules apply to them too). The
remaining
thirteen could not be classified
the same way without breaking that verification, and are recorded here
instead, following the same "trial classification measured N new
violations, blocked" pattern this ADR's other gaps already use for
`comparability.py`/`build_context.py`/etc.

**Blocked `migrate` dispositions**, three shapes:

- *Caller-forced* (the common case below): each module's own outgoing
  imports are clean for its named target layer, but a *different*,
  already-classified module imports it directly from a layer that target
  does not allow, so classifying it would immediately trip
  `check_architecture.py`'s `dependency-direction` check. Each needs the
  same fix this ADR's other blocked entries name: route the offending
  caller through a workflows-owned wrapper (or otherwise decouple it)
  before the target classification is safe — not attempted here, per this
  ADR's "not the same-PR fix" bar for migrations that already have
  caller-side test/behavior surface to preserve.
- *Self-dependency* (`build_evidence.py` and `graph_impact.py`, the two
  exceptions below): the module's *own* outgoing import, not a caller, is
  what blocks its target classification. The caller-side remedy above
  (wrap or decouple the caller) does not apply here — the fix is to move
  or decouple the blocking dependency inside the module itself.
- *Mixed-responsibility* (`fact_set.py`, `impact/use_case_impact.py`, and
  `evidence_report.py`, the three exceptions below): the module itself
  bundles two structurally different responsibilities whose real callers
  are already split across incompatible layers. Neither the caller-side
  nor the self-dependency remedy applies — no single target layer is even
  the right answer until
  the module is split along its own seam.

- `abicheck/buildsource/build_evidence.py` (target: `model` — its own
  docstring is literally "Build-system-neutral build evidence model", and
  `abicheck/buildsource/pack.py` (already `model`-classified) imports it
  directly, ruling out `extract`) — a *self-dependency* block: it itself
  imports `.comdat_groups` (`extract`-classified), which `model`'s empty
  `may_import` forbids. Unlike every other entry below, no caller needs to
  change — `comdat_groups` (or the piece of it this module actually uses)
  would need to move or be decoupled from this module directly.
- **Closed (2026-10, Lane C stage 3):** `abicheck/buildsource/graph_impact.py`
  (target: `compare`) was a self-dependency block: it imported three call-edge
  label constants from the `extract`-classified `call_graph.py`. Those labels
  are shared vocabulary, so they moved to `model/graph_vocabulary.py`
  (`call_graph.py` and every other reader now import them from there), and
  `graph_impact.py` — which then imports only `model` — is classified
  `compare`.
- `abicheck/buildsource/build_output.py` (target: `extract`, alongside its
  sibling adapters) — blocked because `abicheck/cli_project.py`
  (`frontends`) imports it directly; `frontends` may not import `extract`.
- `abicheck/buildsource/merge_support.py` (target: `extract`) — blocked
  because `abicheck/cli_buildsource_merge.py` (`frontends`) imports it
  directly.
- `abicheck/buildsource/evidence_policy.py` (target: `policy`, per its own
  D7 verdict-modulation/`require_evidence`-gate docstring) — blocked
  because `abicheck/cli_buildsource_helpers.py` (`frontends`) imports it
  directly; `frontends` may not import `policy`.
- `abicheck/buildsource/fact_set.py` — **not a single-target-layer case at
  all**, unlike every other entry here: this module bundles two
  structurally different responsibilities (`rollup_coverage`/
  `rollup_fact_set`/`fact_set_rollup_is_inconsistent`/
  `incomplete_families`, a dependency-free per-TU fold — genuinely
  `extract`-shaped, and `check_fact_set_compatibility`/
  `check_fact_compatibility`, an old/new pairwise comparison rule —
  `compare`/`policy`-shaped) under one file, and its callers split along
  exactly that seam: `abicheck/buildsource/source_link.py` (`extract`)
  uses only the rollup half; `abicheck/buildsource/inputs_validate.py`
  (`extract`) uses both; `abicheck/buildsource/source_diff.py` (`compare`)
  and `abicheck/analysis_assurance.py` (`policy`) use only the
  compatibility half. No single classification — `extract` included, this
  entry's own earlier revision wrongly proposed — can be correct while
  both halves stay in one file, since `compare`'s and `policy`'s
  `may_import` never include `extract`. Splitting the file is necessary
  but not sufficient on its own: the compatibility half's natural owner is
  `compare` (it's an old/new pairwise comparison rule, the same shape as
  `source_diff.py`'s own responsibility — `model` would mis-own it, since
  `model` holds entities/values, not comparison algorithms), but
  `inputs_validate.py` itself is `extract`-classified and calls
  `check_fact_set_compatibility` directly; `extract`'s `may_import` is
  `model`/`storage` only, so moving the compatibility half to `compare`
  would immediately turn `inputs_validate.py`'s own call into a new,
  forbidden `extract -> compare` edge. The real fix therefore has two
  parts, not one: the internal split (rollup functions to `extract`, the
  compatibility-check functions to `compare`) *and* moving or interposing
  `inputs_validate.py`'s own call site (e.g. routing it through a
  `workflows`-owned caller, the same pattern this ADR's caller-forced
  entries already use) — a combination neither the caller-forced nor the
  self-dependency shape above covers on its own, and not a single
  blocked-migrate target either; recorded here as its own third shape.
- `abicheck/impact/use_case_impact.py` — the identical mixed-responsibility
  shape as `fact_set.py`: `build_use_case_impact()` is comparison-time
  orchestration (`workflows`-shaped — not this module's actual
  `architecture/modules.yaml` classification, since it carries none, but
  the layer its own content fits), but the same file also defines
  `render_use_case_impact_lines()` (text/markdown rendering) and
  `add_use_case_impact()` (mutates a serialized JSON report dict) — both
  report-shaping, not orchestration. A `workflows` classification (as an
  earlier version of this change assigned it) would bless report
  construction as workflow logic and let a future workflow-only dependency
  reach those two functions unchecked. No single classification is correct
  while all three stay in one file; the real fix is the same shape as
  `fact_set.py`'s — split the report-shaping functions out to a
  `report`-owned sibling.
- `abicheck/buildsource/evidence_report.py` — the identical
  mixed-responsibility shape again: `resolve_side_pack()`/
  `intrinsic_coverage()`/`optional_coverage()`/`detect_coverage_asymmetry()`/
  `diff_embedded_build_source()`/`prepare_embedded_build_source()`/
  `attach_evidence_metrics()` are pack resolution and diff orchestration
  (`workflows`-shaped — again, the layer its content fits, not a real
  classification this module carries), but `coverage_lines()`/
  `compare_side_coverage_lines()`/`capability_lines()` in the same file
  construct the human-readable evidence-coverage report text — `report`-
  shaped. Same fix shape as the two entries above: split the rendering
  functions out to a `report`-owned sibling before either half gets a
  real target.
- **Closed (2026-10) by deletion, with the rest of `abicheck/compat/`:**
  `abicheck/compat/descriptor.py` (target: `extract` — ABICC XML
  descriptor parsing, the same shape as the already-`extract`-classified
  `compat/abicc_dump_import.py`) — blocked because `abicheck/compat/cli.py`
  (`frontends`) imports it directly (both the runtime `parse_descriptor`
  call and a `TYPE_CHECKING`-only `CompatDescriptor` reference).
- **Closed (2026-10) by classifying it `policy`:** `abicheck/impact/engine.py`
  (originally targeted at `workflows` — its `assess_change` builder is
  called from the consumer-scoping workflow) was blocked because
  `abicheck/post_processing_reachability.py` (`policy`) also imports it, and
  `policy` may not import `workflows`. It imports only `model`, and building
  a finding's impact assessment is a `policy` decision, so `policy` serves
  both callers (the Lane C `appcompat.py` split).
- **Closed (2026-10) by deletion:** `abicheck/compat/_helpers.py` (target: `frontends` — split directly out
  of `compat/cli.py` per its own module docstring, implements ABICC CLI
  translations, imports `click`, and is imported only by `compat/cli.py`
  itself, already `frontends`-classified) — blocked because its own body
  imports `..policy.classification` directly, which `frontends`' `may_import`
  (`model`/`workflows`/`report` only) forbids. Its sibling `compat/_errors.py`
  carries no such import (only `click` and the already-`model`-classified
  `errors.py`) and was reclassified to `frontends` in this same pass —
  verified empirically, not merely asserted, the same way every `migrate`
  disposition in this gap was. Classifying `_helpers.py` under `workflows`
  instead (as a superficial fix) would not be a correct disposition: it
  would paper over the real edge — `compat/cli.py` (`frontends`) reaching
  `policy.classification` through this one intermediate file — rather than
  recording it as the blocked `frontends -> policy` case it actually is.

**Retain, deliberately unclassified**:

- `abicheck/buildsource/source_graph.py` — a pure back-compat re-export
  facade (its own docstring: "Back-compat facade: source-graph values/
  construction/comparison split"), not a real implementation module. Every
  name it used to define now lives in already-classified siblings
  (`model.graph_facts`/`model.source_graph`/the now-`model`-classified
  `source_graph_query.py` for values, `extract`-classified
  `source_graph_build.py`/`source_graph_build_source_abi.py` for
  construction, `compare`-classified `source_graph_compare.py` for
  comparison); this module only re-exports those names (`X as X`) so a
  pre-existing `from .source_graph import ...` call site keeps resolving.
  No internal first-party module actually imports it any more (every real
  internal caller already imports the split-out siblings directly) — it
  exists purely for external/legacy callers. Classifying it `workflows`
  (as an earlier version of this change did) would misstate its
  responsibility: it coordinates nothing, and giving a pure re-export
  facade a real layer identity is exactly the kind of false ownership
  this ADR's `facades` mechanism exists to prevent elsewhere. Unlike
  `dispositions.yaml`, `architecture/modules.yaml`'s `facades` list is
  **not** root-path-limited — `check_architecture.py` resolves each entry
  as `facade.replace(".", "/") + ".py"`, which reaches a nested module
  like `abicheck.buildsource.source_graph` just as well (verified: adding
  it there and running `check_architecture.py` does invoke
  `_check_facade` against it). The real reason it isn't registered there
  is that it doesn't satisfy that check's stricter delegation-only rules:
  it has no `__all__`, and it carries a real `__getattr__` function body
  (a deliberate, documented lazy import breaking a real
  `source_graph -> source_graph_findings -> source_graph` cycle the
  AI-readiness gate would otherwise reject) — trial-registering it
  produces exactly those two `facade-exports`/`facade-logic` findings.
  Making it pass would mean removing that cycle-breaking `__getattr__` or
  restructuring the module, a real code change out of scope for this
  docs-only pass. Recorded here as a retained, deliberately-unclassified
  exception instead, with the concrete blocking findings named rather
  than asserted.
- `abicheck/impact/model.py` — the identical shape gap B already
  established for `checker_policy.py`/`contract_gating.py`/`reclassify.py`:
  `abicheck/checker_types.py` (`model`, whose `may_import` is empty)
  imports `ImpactAssessment` from this module directly and statically
  (`from .impact.model import ImpactAssessment`), so the module cannot be
  classified into anything other than `model` itself without turning that
  pre-existing edge into a real `dependency-direction` violation — but the
  module's own body imports `..policy.evidence_status` (`Confidence`,
  `ReachabilityState`), which `model`'s empty `may_import` equally
  forbids. No single ADR-061 layer admits both directions at once, the
  same conflict gap B's own worked example (`DiffResult`/`checker_policy`)
  describes. Its sibling `impact/engine.py` above depends on this module
  too and inherits the same constraint, which is part of why it stays
  blocked rather than reclassified on its own.

Not fixed here, matching this ADR's own migration-rules bar ("a real,
separate migration slice, not a same-PR fix"). This applies differently
per shape, not as one blanket remedy: each *caller-forced* entry above
names the specific caller that would need to route through a
`workflows`-owned wrapper (mirroring gap A's `cli_dump_helpers.py ->
header_conditionals.py` precedent) before its target classification
becomes safe; each *self-dependency* entry (`build_evidence.py`,
`graph_impact.py`) instead needs its own blocking import moved or
decoupled, with no caller-side fix at all; each *mixed-responsibility*
entry (`fact_set.py`, `impact/use_case_impact.py`, `evidence_report.py`)
needs an internal split before either half gets a target. The two
*retained* entries are not
migration targets at all, but for two different reasons, not one:
`source_graph.py` is a pure facade with no offending caller in the first
place (it exists purely for external/legacy call sites); `impact/model.py`
is the opposite — it has a real, named offending edge
(`checker_types.py -> impact.model -> policy.evidence_status`), the same
"no single ADR-061 layer admits both directions" conflict gap B's own
worked example describes, and is forced *toward* (never actually
classified as) `model` because that conflict has no caller-side or
self-dependency fix available today — not because there's no caller to
fix. It carries no `architecture/modules.yaml` entry at all, same as
every other entry in this file. Tractable per-entry, not as one slice —
`build_output.py`'s single `frontends` caller is independent of any other
entry's fix.

### A stored-`BundleFacts` baseline's narrower format set is not pre-checked by the Action

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** action/validate-inputs.sh:327-338 still checks only the release allowlist (json|markdown|junit|oneline|html) and has no stored-BundleFacts narrowing. No --probe-formats capability exists.


`actions/`' preflight (`action/validate-inputs.sh`) mirrors every one of
`compare`'s format allowlists so a bad `--format` fails before the
multi-minute toolchain install rather than after it — except one. A stored
`BundleFacts` OLD side with a directory/package NEW side renders `json` or
`markdown` only (`frontends/cli/commands/compare_bundle_facts_rejections.py`),
narrower than the release allowlist's `json|markdown|junit|oneline` that
preflight applies when *either* operand is release-style. So
`format: junit` on that shape passes preflight and then fails in the CLI with
a usage error (exit 64, surfaced as `VERDICT=ERROR`). A late clear failure,
not a wrong result — but late.

It is not fixed here because both available fixes are worse than the gap:

- **Classify in shell.** Whether a file *is* a stored `BundleFacts` document
  is answered by `storage/bundle_facts_codec.looks_like_bundle_facts_document`,
  a deliberately two-tier classifier (explicit `artifact_type` marker, then a
  v1-only shape fallback with documented, accepted false positives). A shell
  re-implementation is exactly the duplication `_is_release_style_operand`'s
  own comment warns about, and that one is cross-checked against the live CLI
  by a test — this one could not be, since the classifier operates on decoded
  JSON, not a path.
- **Read the document at preflight.** The operand is attacker-controlled in
  the PR-checkout case, which is the whole reason the decode-node ceiling is
  config-only and the remedy this PR rewrote says only an explicit `--config`
  may raise it. Decoding it in a preflight step that runs *before* those
  limits exist reopens precisely that surface.

A path-extension heuristic (`*.json`/`*.json.gz`/`*.json.zst`) was considered
and rejected: it would wrongly reject `junit` for a stored `ProjectSnapshot`
OLD side, which is the same shape on disk. The honest fix is to make the
narrowing a *CLI-reported capability* the Action can query cheaply (a
`compare --probe-formats` style answer, or having the CLI validate formats
before resolving operands), which is a real design slice rather than a guard.
Until then the Action's `format` input documents the narrowing and says
plainly that it is not pre-checked. Found by Codex review on PR #1237.

### `compare --bundle-facts-out` stays a `compare` flag because `dump` has no release fan-out

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** --bundle-facts-out is still a compare option (frontends/cli/options/bundle_facts.py, commands/compare.py), and no dump command offers a bundle-facts format/fan-out. The blocker is removed, but the dump fan-out is unwritten.


`one-comparison-product.md`'s own framing, recorded rather than fixed here:
`--bundle-facts-out` asks a *comparison* to capture a baseline. Capturing
the OLD side's per-library snapshots into a `BundleFacts` document is a
**dump** operation — it reads one release and writes facts about it, and it
does not need a NEW side at all. It lives on `compare` only because that is
the one command that knows how to turn a directory or package into a set of
per-library snapshots; `dump` takes exactly one artifact, and a directory
operand there is interpreted as a stored `ProjectSnapshot` *package*, not as
a release to fan out over.

**The two options, and why neither landed in this pass.** Adding the `dump`
fan-out is the right shape: `abicheck dump RELEASE_DIR --format bundle-facts`
would make baseline capture a first-class dump, `compare` would consume the
document it already consumes, and `--bundle-facts-out` could be retired
rather than reimplemented. What it needs is the whole release input-resolution
chain — package/debug-package/devel-package extraction, library discovery and
canonical-key matching, stored-`ProjectSnapshot` variant materialization,
`--dso-only` classification — and every one of those functions is currently
`frontends`/flat-`cli_*`-classified (`cli_compare_release_matrix.
_prepare_compare_release_inputs` and its helpers), with `click.UsageError`
raises inside them. `dump`'s own execution path is `workflows`-classified, so
the `engine-cli-boundary` gate forbids it from reaching any of that, and the
fan-out cannot be written without either duplicating the chain or moving it.

That move is the *same* work `one-comparison-product.md` item 4 asks for on
its own terms (relocating `frontends/cli/release_compare_request.py`'s call
chain into `workflows` with typed errors), which is why the ordering here is
a real dependency rather than a deferral: the `dump` fan-out is a
straightforward composition once that chain is engine-side, and an
open-coded duplicate of it before then is exactly the second parallel path
this repository's architecture rules forbid.

**That dependency is now satisfied** (same PR): the chain is
`abicheck/workflows/release_inputs.py`, it raises the typed
`errors.ReleaseOperandError`, and `abicheck.service.resolve_release_compare`
reaches it with no Click context. So the `dump` fan-out is no longer
*blocked* -- it is simply not written yet, and writing it is a new command
surface (`dump`'s own operand arity, `--format bundle-facts`, the ADR-054
root-surface bar for any new spelling, and the retirement path for
`--bundle-facts-out`) rather than a refactor. Recorded here as the next
slice, with the blocker removed, instead of left reading as unreachable.

**Until then, `--bundle-facts-out` stays where it is, and stays honest about
what it is:** a capture ridden on a comparison. It is not reimplemented, not
widened, and not given a second spelling. When the `dump` fan-out lands, the
flag becomes a deprecated alias for it rather than a separate producer —
`abicheck/cli_compare_release_helpers.py`'s `write_bundle_facts_out` already
takes the already-resolved per-library snapshots and nothing else, so it is
the reusable half and moves as-is.

### A directory/package `compare` cannot render sarif/review; `bundles:` cannot state scope policy

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** frontends/cli/commands/compare_routing.py:45 `_RELEASE_FORMATS = {json, markdown, junit, oneline, html}`, so sarif/review are still rejected for a release, and BundleSpec still has no scope-policy keys (not re-checked in detail).


Two halves of `one-comparison-product.md`'s bundle-parity item that this
pass did *not* close, recorded with what each actually needs. The rest of
that item did land (`assurance.require_complete` accepted, `--write`
repeatable, the machine document no longer implicitly truncated) — see the
changelog entry and `tests/test_one_comparison_product_parity.py`.

**1. The format set.** `compare` renders
`json`/`markdown`/`sarif`/`html`/`junit`/`review`/`oneline`; a directory or
package operand renders `json`/`markdown`/`junit`/`oneline`/`html` and rejects the
rest
(`frontends/cli/commands/compare_routing.py`'s `_RELEASE_FORMATS`). The rejection is
loud rather than silent, and it is not arbitrary — the missing formats are
the ones whose renderers take a single `DiffResult`:

- `sarif` needs one result set with stable per-finding locations; a release
  has N of them and the fan-out discards each member's `DiffResult` before
  rendering (`_strip_diff_results_and_adjust_verdict`, to bound peak memory
  across a whole release). Producing a real release SARIF means either
  keeping every member's findings live or projecting SARIF per member and
  merging runs — a design choice, not a wiring gap.
- `review` is a narrative rendering of *one* comparison, a PR-comment
  digest whose aggregate shape ("which of 40 libraries broke, and how
  badly") is a product question nobody has answered yet.
`oneline` was the fourth of that list and is **closed**: it never needed a
`DiffResult` at all (it is a count summary, and the release summary already
carries every count), so `report/release_oneline.py` folds the per-library
counts through the same `format_stat_line` a single-pair `compare` renders
and the release path accepts `-o oneline=...`/`-o oneline=...` like
any other. `html` is **closed** the same way: `report/render_release_html.py`
renders the release JSON document itself (headline, per-member table,
comparison scope, release coherence findings and the recorded dependency
graph), so it needs no member `DiffResult` either. It is a release page, not
the single-pair HTML report; per-member finding detail stays in the JSON.

So library count still changes which of `sarif`/`review` are
available -- a real parity gap, stated here rather than implied by a usage
error -- and no longer changes anything about `oneline` or `html`.

**2. Scope policy at bundle level.** ADR-065's scope policy is fully
expressible for a directory/package `compare` — `--select`,
`--select-required` and `.abicheck.yml`'s `scope.on_incomplete` all apply to
a release operand, and `--select-required` naming an absent member really
does floor the exit code (verified live). What has no expression is the
*project-config* bundle surface: `buildsource/project_targets.py`'s
`BundleSpec` accepts only `targets:` and `checks:`, so a `bundles:` entry
cannot state required members beyond its own membership list, nor its own
permitted-incompleteness policy, nor a selection narrower than the whole
bundle. A project that wants "these four of the six must be present, the
other two may be missing" has to say it per invocation rather than in the
config the Action reads. That is a `BundleSpec` schema addition plus a
`check-target` input, in the ADR-047 project-targets layer — not a
`compare` change, which is why it is listed separately from the format set
above.

### Release-fan-out CLI tests JSON-parse `CliRunner.output`, which the memory-clamp note can join

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** cli_compare_release_pairwise.py:621-628 still echoes the clamp note with err=True. tests/test_release_package_inventory_cli.py:92-99 still does `json.loads(CliRunner().invoke(...).output)`, as do test_release_evaluation_config.py:712,730.


Found while verifying an unrelated change, reproduced on a clean tree, not
fixed here (it is nobody's feature and touches eight tests across two
modules that the change under review did not otherwise touch).

`cli_compare_release_pairwise.py` echoes one note to **stderr** when the
per-worker memory budget clamps the release fan-out's parallelism ("Note:
parallel release workers reduced 4 -> 3 to fit available memory ..."). Several
release CLI tests capture with a bare `CliRunner()` -- which merges stderr
into `result.output` -- and then `json.loads(result.output)`. When the clamp
fires, the note lands ahead of the document and the parse raises
`JSONDecodeError`, so the test fails for a reason that has nothing to do with
what it asserts.

It only fires under real memory pressure, which is why it is invisible most
of the time and then shows up as a cluster of unrelated-looking failures on a
loaded machine (e.g. the whole suite under `pytest -n 8` on a 16 GiB box).
Reproduced deterministically on a clean tree with
`ABICHECK_RELEASE_JOB_MEM_GIB=1000 pytest
tests/test_release_evaluation_config.py::TestReleaseFanOutAcceptsContractUnresolvedPack`.

Affected (as of this writing): `tests/test_release_evaluation_config.py`'s
`TestReleaseFanOutAcceptsContractUnresolvedPack::test_now_applies_with_contract`
and six tests in `tests/test_release_package_inventory_cli.py`
(`TestPackageArchiveInventoryProvesAbsence`, `TestSupportPromiseFindingsCli`).

The fix is per-test and mechanical: render to a file (`-o PATH`) and parse
that, the way `tests/test_one_comparison_product_parity.py` already does
where `--output-dir`'s own stdout line would otherwise interfere, or capture
stderr separately. Asserting on the note, or suppressing it, would be the
wrong fix -- it is a real diagnostic a user should see.

### Source-read licence: enforced at the one consumer, not repo-wide (2026-09-12)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Both deliberate residuals are still open: scripts/check_ai_readiness.py has no source-read licence gate (grep 'licen' finds only license-header checks), and there are no per-input content digests. The third residual is recorded as closed.


The contract — *a path recorded in a snapshot is provenance, not a licence to
re-read the current filesystem for historical facts* — is stated in
`abicheck/buildsource/source_inputs.py` and enforced in the one place that
had violated it: `workflows/pattern_preprocessor_scan.py`, plus the two
primitives beneath it (`buildsource/pattern_facts.py`'s
`find_pattern_facts`, which now refuses to stat an unlicensed root, and
`buildsource/preprocessor_facts.py`, which is simply not invoked for an
unlicensed side since `clang -E` resolves `#include`s against the filesystem
it runs on).

Two residuals are deliberately open, both recorded on the
`evidence.stored_snapshot_rederivation` bug class in
`tests/regressions/manifest.py`:

1. **No mechanical gate against a future violator.** Nothing stops a new
   consumer from reading `AbiSnapshot.source_header` (or a compile unit's
   `source`) and opening it without resolving a licence first — nor a new front
   end from calling `dumper.dump` directly and forgetting to *grant* one. Both
   halves of that already bit once: the ABICC-compatible CLI was found doing
   exactly the second (PR #1236's review round), after `service.run_dump` had
   already been fixed for the first. The shape of
   the fix is known — an AST scan in `scripts/check_ai_readiness.py`, the way
   `fact-field-readers` guards `Fact[T]` reads, with an allowlist of the
   reader sites that legitimately hold a licence — but it is a gate of its
   own, not part of this fix, and today there is exactly one such consumer to
   guard.

2. **The licence is object-level, not content-verified.** A header-derived
   live extraction grants it for the whole run; it does not digest each source
   input and re-check that digest at read time. The consequence is the conservative
   direction (a stored snapshot declines to re-derive even when the tree on
   disk genuinely *is* the one it was dumped from), which is why it is a gap
   rather than a defect. Closing it properly means persisting per-input
   content digests, i.e. an `AbiSnapshot` schema bump inside the ADR-050
   comparability contract plus a codec and migration — the "persist the facts
   at dump time" half of the original two-option brief. That is the route to
   take if stored-versus-stored pattern/preprocessor evolution ever needs to
   be answerable rather than honestly declined; do not instead widen the
   licence, which would reintroduce exactly the defect above.

**Closed, and how (one licence per evidence source).** A third residual used
to sit here: the licence required *header-derived* provenance
(`extraction_read_source_inputs`), so a dump that collected only L3 build
evidence (`--sources` with no `-H`) reported its source-derived facts as not
evaluated even though it genuinely had read its compile units, because nothing
at the grant point separated that from a *loaded* pack whose paths are as
historical as a stored snapshot's. The same coarseness had a worse, opposite
failure the same review round found: a live header dump merged with a
pre-captured `--build-info` pack was granted one snapshot-wide licence off the
header AST, and the pattern/preprocessor scans then read the *pack's* recorded
compile-unit paths from whatever occupies them on this runner — the original
fabrication, reached through the other evidence source (PR #1236, Codex P2).

Both directions are one defect: a side holds up to two source-evidence sources
with independent provenance, and one licence cannot be correct for both. So the
licence is now resolved per source — declared headers via
`snapshot_source_licence`, an embedded build pack via `build_evidence_licence`,
which asks the pack itself (`BuildSourcePack.live_source_evidence`, stamped by
`buildsource/embed.py` only for an inline collection performed in this run and,
like the snapshot flag, never serialized). A partially-licensed side reports
what it read and keeps the unlicensed roots in the expected-input account as
`not_licensed` gaps, so no *absence* is established from the half that was read.
What remains true is that the grant is *conditional*, which is the point for a
DWARF-only dump whose `DW_AT_decl_file` paths name the build machine's tree:
that side never opened them, so it gets no header licence.

Note also the *shape* of the accepted trade-off in the third fix: the
evolution fold now decides each identity from what is established for that
identity, which means `persistent` is reported from two observations even
when both sides had coverage gaps elsewhere. An earlier revision withheld it
globally. That is not a regression of the earlier fix: an incomplete scan
cannot un-see a hit, so two observations are the strongest premise the fold
has, while `introduced`/`resolved` each assert an *absence* and still require
the relevant side's sufficiency. The lexical scan cannot, however, attribute
an unread file to a particular `PatternKind`, so a gap in any declared root
leaves *every* kind's absence unestablished on that side — a per-kind answer
would need per-kind input attribution the scanner does not have, and is
recorded as a gap on `coverage.discovery_derived_completeness`.

### An informational, no-material-change reconciliation outcome gates the build under `--severity-preset strict` (2026-09-12)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** No INFORMATIONAL_KINDS partition exists anywhere under abicheck/ (grep is empty). declaration_coordinates_shifted still exists (model/change_catalog/kind_names_1.py, buildsource/graph_reconcile*.py) and still falls in quality_issues.


Raised as a triage question ("is `quality_issues` the right bucket for a
COMPATIBLE, purely-informational reconciliation outcome, or does the report
shape need an `informational, no action` category?"), it has a prior
question that outranks it: **do any consumers gate or rank on the bucket?**
They do, and that is the real defect.

`declaration_coordinates_shifted` — and now `declaration_identity_unchanged`
— are `COMPATIBLE` but not `ADDITION_KINDS`, so
`report_summary`/`policy.severity.categorize_changes` place them in
`quality_issues`. That is documented, and the partition invariant holds.
But `quality_issues` is one of the four `SeverityConfig` categories, and
`PRESET_STRICT` sets every category to `ERROR`. Measured directly:

```
declaration_coordinates_shifted   bucket=quality  default rc: 0  strict rc: 1
declaration_identity_unchanged    bucket=quality  default rc: 0  strict rc: 1
```

So under `--severity-preset strict` (or a `.abicheck.yml` `severity:` block
setting `quality_issues: error`) a finding whose whole content is "the
reconciliation proves this declaration did not change" produces a non-zero
exit code. `policy.severity.compute_gate_decision` additionally names
`quality_issues` in `blocking_categories`, and
`pr_comment_render.py`'s `_SEVERITY_ICONS` renders that category as
"⛔ Quality policy violation" on the PR comment.

This is not a presentation problem and it is not fixed by a dashboard
ranking differently. Two candidate fixes, neither taken here:

1. **Make the bucket honest.** `quality_issues` is defined as
   `COMPATIBLE_KINDS − ADDITION_KINDS`, i.e. a residue, and the residue now
   holds two populations with opposite meanings: real quality problems
   (`std` symbol leaks) and proofs that nothing happened. A third
   `COMPATIBLE` sub-partition (`INFORMATIONAL_KINDS`, alongside
   `ADDITION_KINDS`/`QUALITY_KINDS`) with no `SeverityConfig` category of
   its own would fix the gate and the report shape together. It is a
   `checker_policy` partition change, a `report/` `compute_*`/`render_*`
   pair change, and a report-schema bump — and `changekind-partition`'s
   AI-readiness gate would need to learn the fourth set.
2. **Exempt these kinds from the gate.** Narrower and worse: it leaves the
   bucket lying about what it holds, and it needs a per-kind exception list
   in exactly the place ADR-049 D8 works to avoid one.

Not attempted in the PR that found this because it changes a public
partition and a report schema — see this file's own precedent that a
partition change is an ADR-scoped decision, not a follow-up patch. The
measurement above is the evidence; the decision is open.

### Declared-header order is still load-bearing because the driver TU is sequential

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The scope-confirmed header growth waiver landed (comparability.py:845,1298 reference _header_sequence_is_scope_confirmed_growth). Still open: the sequential aggregate driver TU itself, so reordering an existing header still refuses.


`profile_fingerprint`'s `header_sequence` field records declared-header
*order*, and it does so for a real reason: `dumper.py` generates one
aggregate driver translation unit that `#include`s the declared headers in
sequence, so a header's macros/pragmas can change how every header parsed
after it resolves.

The comparability gate originally waived only a strict *trailing* append
(`_header_sequence_is_additive_reorder_free`), which is the one shape that
proves no existing header's preprocessing context changed. That is correct
for a deliberately declared `-H a.h -H b.h` order and badly mis-fitted to a
directory-discovered one, where the order is an incidental product of
sorting whatever the sweep found. Real integration evidence: a project
adding one public header named `json.h` to a set sorting as
`data.h, log.h, ...` landed it in the *middle*, the waiver declined, and an
ordinary public-header addition refused the entire comparison —
`profile_fingerprint mismatch; differing fields: header_sequence`, no
verdict, and no findings on any axis, the binary's own exported-symbol
identity included.

**What was fixed, in two rounds.** The first round changed the
*disposition*: an order-preserving insertion of headers the scope
fingerprint independently confirms as new became a non-fatal
`ComparabilityMismatch` (`fatal=False`) whose residual risk was recorded as
a bounded assurance reduction on the `declaration`/`layout` dimensions. That
removed the refusal but kept pricing the insertion — and the price is real:
`AnalysisAssurance.status` reads `partial`, which `assurance.require_complete`
gates on, so a project running that (correct, unweakened) setting still
failed on an ordinary public-header addition, now with
"extraction contexts were not provably identical" instead of a refusal.

The second round removed the price, because the hazard it named is not one
this contract prices anywhere else:

* A declared header's **content** is not part of `profile_fingerprint` at
  all — `comparability_fields._header_identities` keys a header by its
  root-relative path, never its bytes. So an existing `data.h` that gains a
  `#define` or a `#pragma pack` between two versions changes the parse
  context of every header after it and is compared at **full** assurance,
  by design: the declared surface's content is the subject of the
  comparison, not evidence about the extraction environment. Adding those
  same declarations as a new file is the same fact in a different shape.
* The added header's position is an incidental product of the project's
  discovered, sorted public-header set. Pricing it makes assurance depend
  on the new header's spelling — `json.h` costs assurance, `zzz.h` does
  not, for the same library change.
* The scope axis already treats the identical addition as ordinary
  evolution at full assurance (`_scope_field_is_additive_superset`), and
  the trailing-append waiver already accepts the added header's own parse.

So the `header_sequence` carve-out is now stated over
`comparability_sequences._header_sequence_is_scope_confirmed_growth` — the
union of the append and insertion shapes — and scope-confirmed growth is
waived outright wherever the new header sorts.
`comparability_profile._declared_header_insertion_mismatch` and
`inserted_header_entries` were deleted rather than left as a second,
unreachable path. The non-fatal (`fatal=False`) machinery itself stays:
it expresses a general rule, has no producer today, and
`analysis_assurance_comparability.py` remains the (still correct) bridge
that stops a bounded run from reporting `complete`.

**What still refuses, unchanged:** a reorder of an *existing* declared
header, growth whose extra entries the declared surface does not confirm as
new (e.g. a header fed to the L2 frontend on the new side only, whose
content the old snapshot never parsed), a duplicate/sentinel/undecodable
sequence, and any other diverging profile field riding alongside the
addition — compiler family or version, target triple, pointer width,
endianness, ABI dialect, language standard, macro ops, pass-through flags,
or an unexplained `include_sequence` (a changed `-I` topology, which decides
which dependency header an `#include` resolves to). The carve-out only ever
removes `header_sequence` from the working set, so that safety property is
structural rather than a list to keep in sync.

**What remains open:** the sequential aggregate TU itself. Parsing each
declared header in an independently scoped TU would make declared order
non-load-bearing outright — a *reorder* would then be comparable too, and
this whole carve-out family could retire — but that is a dumper change
carrying a per-header TU cost, and it was deliberately not attempted
alongside either disposition fix. `--diagnostic-comparison` is explicitly
**not** the answer to any of this: it downgrades assurance wholesale
instead of resolving the extraction question.

### A release/bundle report carries no authoritative entity-by-operation counts

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** report/pr_comment_aggregate.py:575-588 still marks the release rollup exact=False when targets cannot be itemized. The release JSON has an `uncapped` findings path (cli_compare_release_matrix.py:634), but no authoritative entity-by-operation count field was found; pr_comment._release_change_summary named in the entry no longer exists under that name.


`report/change_summary.py` can summarise a single comparison exactly,
because `compare`'s JSON carries the complete `changes` list. A release
(directory/package fan-out) report does not: the only itemized, kind-level
view of a library's findings is `cli_compare_release.py`'s `findings` list,
capped at ten per library. `pr_comment._release_change_summary` therefore
builds the rollup from that sample and marks it `exact=False` with a stated
reason, and the renderer prints "Not exact totals — …" rather than
presenting a floor as a total.

Closing it needs an authoritative per-entity/per-operation count in the
release JSON schema itself — a `cli_compare_release.py` change, not a
rendering-only one. It is the same shape of gap `pr_comment._from_release`'s
own docstring already records for the evidence-kind bucket, and it should be
closed in the same pass as that one.

### An `-I`-reached sibling library's headers yield LOW-confidence export obligations; release reconciliation and JSON confidence not yet narrowed

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The dependency include-root case is closed (extract/public_root_ownership.py) and the PVXS case is narrowed to LOW confidence via workflows/ownership_request.py:136 with_target_roots. The entry's own 'Still open' list remains: per-finding confidence is missing from JSON findings, policy/release_contract_reconciliation.py:237 still emits HIGH confidence without the narrowing, and umbrella headers get LOW-confidence findings.


> **Update (2026-10-01): the PVXS case is narrowed, per the maintainer's
> ruling.** Recording the declared `-H` set turned out to need no
> `SCHEMA_VERSION` bump: ADR-075's `extraction_scope.ownership_rules.
> target_roots` (schema v52) already persists the run's target roots. It
> took only `-H` *directories*, so a run naming files recorded no root and
> every declaration read `unresolved`/`no_root` alike.
> `workflows.ownership_request.with_target_roots` now makes every existing
> `-H` entry a root (a file covers exactly itself). A declaration no root
> covers -- reached only through `#include` and an `-I` root -- is still
> reported as `public_not_exported`, but at LOW confidence and worded as
> *not established* (`buildsource/export_obligation_ownership.py`), and the
> check's coverage detail counts them. The declared header's own missing
> exports keep HIGH confidence (the positive control), and a `-H`
> *directory* declares everything under it, so nothing changes there.
> `tests/test_include_root_export_obligation.py` pins both, with the
> reported-symbol set checked against the fixture's undefined declarations
> so nothing can be dropped.
>
> **Still open.** (1) Per-finding `confidence` is not on the JSON wire
> format, so a JSON reader sees the narrowing only in the description and
> the check detail, not as a field. (2) The release surface's obligations
> (`policy.release_contract_reconciliation`) do not apply the narrowing;
> in a multi-member release the union of exports satisfies a sibling's
> declarations anyway, but a declaration *no* member exports is still
> reported at full strength. (3) An umbrella header (`-H foo.h` including
> the library's own `foo/bar.h`) now gets LOW-confidence findings for
> `bar.h` -- the accepted cost; pass the directory as `-H` to declare it.

> **Partially closed (2026-09-16).** An `-I` root now widens public
> provenance only where the run's own *declared* public headers live
> underneath it (`extract/public_root_ownership.py`, applied by
> `provenance.public_dirs_with_owned_roots` and, identically,
> `buildsource/header_graph.py`). That is a fourth route none of the three
> candidates below considered, and it needs no new `ScopeOrigin` member, no
> `Fact` retightening and no `SCHEMA_VERSION` bump: the evidence to separate
> the two questions was already in hand at classification time, in the
> declared `-H`/`--public-header-dir`/`sources.public_headers` set the
> caller passes alongside the `-I` list.
>
> A root the rule declines is classified `UNKNOWN`, **not**
> `PRIVATE_HEADER`. That distinction is the whole safety argument and was
> not in the first version of the fix: `PUBLIC_HEADER` is what creates an
> export obligation, so a dependency's declarations must not have it; but
> `PRIVATE_HEADER` is a *confident* signal that public-surface scoping acts
> on to drop findings, so a root the run merely could not place must not
> have it either, or a library whose own public headers are split across
> include roots loses real breaking changes from its verdict. Raised as a
> P1 by Codex's security review on the PR; four attempted repros were each
> caught by some other mechanism (the undeclared-export removal exemption,
> the export-table closure, the conservative-unknown fallback), but the
> safety of a demotion rule must not rest on unrelated mechanisms
> happening to cover it.
>
> **What it closes:** a *dependency's* include tree. Intel MKL passes an MPI
> include directory solely so `mkl_cdft.h` can parse `#include <mpi.h>`; no
> MKL public header lives under it, so it no longer widens anything, and the
> 2,211 `MPI_*`/`PMPI_*`/`QMPIX_*` `public_not_exported` findings are gone.
> Regression tests: `tests/test_dependency_include_root_ownership.py`,
> including a property-style statement of the containment predicate.
>
> **What it does not close, and this is the reported PVXS case above.**
> `-H include/pvxs/iochooks.h` with `-I <pvxs>/include` is containment:
> the declared header lives *under* that root, so the root keeps its
> widening power and `version.h` beside it is still `PUBLIC_HEADER` — still
> charging libpvxsIoc for libpvxs's exports. Containment separates "a
> dependency's tree" from "the library's own tree"; it cannot separate *two
> sibling libraries sharing one include tree*, because from one snapshot's
> point of view those are the same tree. Everything below still applies to
> that case, and the three candidate fixes are still the options — the
> narrowest (recording the declared `-H` set on `AbiSnapshot`) remains the
> most promising, since "which headers this run declared" is exactly the
> evidence a shared tree makes unrecoverable at classification time.
>
> The positive control the entry demands is kept and tested: a symbol
> declared in a header that *is* in `-H` and genuinely absent from the
> binary still reports.


**Reported on a real PVXS build** (abicheck `3737f9f9`, CastXML 0.7.0, EPICS
Base 7.0, gcc 13, `-std=c++11`), and reproduced by comparing one snapshot
against *itself* — byte-identical operands, verdict `NO_CHANGE`:

```
abicheck dump lib/linux-x86_64/libpvxsIoc.so.1.5 \
  -H include/pvxs/iochooks.h \
  -I <pvxs>/include -I <epics>/include ... --depth headers
```

`-H` names exactly one file. `include/pvxs/version.h` is reachable only
because `iochooks.h` `#include`s it and `-I <pvxs>/include` makes it
findable; the symbols it declares (`pvxs::version_int()`,
`version_str()`, `version_abi_int()`) are exported by **libpvxs**, a
different library. The comparison charged **libpvxsIoc** with three
`public_not_exported` findings for them.

**Root cause, confirmed by reading both halves.**
`provenance.apply_provenance` deliberately folds the `-I` roots into the
public-*directory* set once a real `-H` set has opted classification in
(`provenance.py`, the `include_search_dirs` parameter and
`_public_dirs_from_include_roots`). That fold exists for a real defect it
fixed: without it every transitively-`#include`d header classified
`PRIVATE_HEADER`, and a genuine breaking layout change reached only through
an umbrella header silently dropped out of the compared surface. So the
declaration's `ScopeOrigin` becomes `PUBLIC_HEADER`, and
`buildsource/cross_source_checks.py`'s `_has_export_obligation` /
`_var_has_export_obligation` gate on exactly `origin == PUBLIC_HEADER`.

The two questions are being conflated:

1. *Is this declaration in the component's compared public surface?* — the
   fold's answer (yes) is defensible: an API change to it matters.
2. *Does **this binary** owe an exported symbol for it?* — the fold's answer
   is not implied by (1) at all, and for a shared include tree serving
   several libraries it is wrong.

Note the reported case used a `-H` **file**, so this is not the documented
"a directory entry tags everything under it public" behaviour; a directory
`-H` root reaches the same place through the same fold.

**Not fixed here, and why.** The evidence needed to separate (1) from (2) —
which headers the run's own `-H` set *declared*, as opposed to which the
`-I` widening admitted — is not recorded anywhere a check can read it after
serialization. `ScopeOrigin` collapses both to `PUBLIC_HEADER`, and
`ExtractionContract` keeps fingerprints rather than paths. The three
candidate fixes each carry real blast radius:

- **A new `ScopeOrigin` member** (`INCLUDE_CONTEXT_HEADER`): 52 existing
  `ScopeOrigin.PUBLIC_HEADER` sites must each decide whether they mean
  "in the compared surface" or "owned by this component", and the value is
  persisted vocabulary.
- **Tighten `in_public_contract_fact` to the declared set** (already
  persisted, schema v46): but `compare/export_transition.py` already keys on
  `is_confirmed_false(in_public_contract(...))` to decide *not* to suppress a
  removal, so a confirmed-false for widened declarations would add findings
  elsewhere.
- **Record the declared `-H` files/dirs on `AbiSnapshot`** and re-classify in
  the check: the narrowest of the three, and independently useful (a stored
  baseline records nothing about the contract it was dumped under), but it is
  a `SCHEMA_VERSION` bump inside ADR-050's comparability contract.

Whichever is taken, the fix must keep the positive control: a symbol declared
in a header that *is* in `-H` and genuinely absent from the binary must still
report. A suppression rule or a per-project carve-out is explicitly not the
answer.

### Export-loss ownership: inline-copy proxy and template-specialization owner remain open (see export-loss-ownership-root-cause.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** This is a pointer stub to export-loss-ownership-root-cause.md. It still lists two open items: is_inline is used as a proxy, and there is no Itanium type decoder for specialization owners. Not re-verified in code beyond that.


`func_removed_elf_only`'s ctor/dtor exemption resolved its owner through an
index keyed by the *unqualified* record name, and then treated "the owning
class is still declared" as a reason to drop a concrete export loss. Both were
falsified by measurement (a loader running a client built once against OLD),
and the mislabelled `-O0` vs `-O2` corpus entry the exemption was built to
satisfy was itself a true positive under its own header. Full account, the
runtime controls, the intentional semantic change and the remaining gaps:
[`export-loss-ownership-root-cause.md`](export-loss-ownership-root-cause.md).
What is still open there: the inline-definition fact is read from the
declaration's `is_inline`, which is a fact about declaration linkage, not
proof that a consumer emitted its own copy; and the recovered owner path for
a class-template specialization names only the primary template, since this
repository has no Itanium type decoder.

### Five SVS-scan report findings not addressed by the change-vs-inventory/depth/exclusion fixes (2026-09-17)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: none of the five SVS report items was checked in code (header accounting does not lower status, the not-applicable detector vocabulary, the 294 KB Markdown default, partial telemetry, the identical-binary channel). Nothing marks any of them fixed.


An external reproduction of a real PR scan (SVS, Linux ELF C++, Clang 20,
two byte-identical runtime artifacts) reported nine defects against
`abicheck`'s report. Four were fixed together, because they are one
invariant -- a report may only count, and claim coverage for, what the
comparison actually observed (`tests/regressions/manifest_report.py`'s
`report.unobserved_population_counted_as_observed`): persistent hygiene
counted as this release's risk, an implicit depth reported as unanswered,
reduced source-graph evidence reported as source depth, and unmatched
`--exclude-header` rules reported as lost coverage.

The remaining five are recorded here rather than patched narrowly. Each is
a genuinely separate piece of work with its own owner, and the run that
reported them is reproducible, so none of them needs re-discovery.

- **Header coverage cannot be audited even on a `complete` run.**
  `analysis_assurance` reported `status: complete` while requested/resolved
  header roots were empty, `translation_units`'
  selected/parsed/failed/skipped counts were all `null`, the L2 elapsed
  time was zero, and no matched-header count was recorded anywhere. Nothing
  in the document demonstrates that the intended public headers were found
  and parsed. Owner: `analysis_assurance._translation_units`/
  `_target_accounting` (which already have the shape, and are simply not
  populated on the header-AST path), and the accounting must then
  *contribute* to `status` -- an unpopulated accounting block currently
  cannot lower it, which is what let `complete` stand.
- **"Not applicable" detectors are reported beside genuine coverage gaps.**
  PE, Mach-O, kABI, Python, SYCL and the DWARF family are all listed as
  detectors "not evaluated" for a Linux ELF C++ comparison where most of
  them could never apply. The vocabulary needs at least four states --
  *applicable and evaluated*, *applicable but missing evidence*, *not
  applicable to this artifact/platform*, *not requested at the selected
  depth* -- and only the second may reduce confidence or appear
  prominently. Owner: `policy/evidence_status.py` plus whatever populates
  the detector roster; note this is a classification change, so the
  applicability rule must be derived from the artifact's own container/
  language facts rather than from a hand-maintained per-detector list that
  will drift.
- **The default Markdown report is ~294 KB for a no-change result.**
  Mostly 32 persistent findings expanded with full C++ mangled names, plus
  suppressed findings. The default CI/PR report should summarize persistent
  hygiene by family, collapse or truncate mangled symbols, cap example
  counts, and leave the complete inventory to JSON or a downloadable full
  report. The 31 `std::once_flag` guard/thunk symbols are one obvious
  grouped finding. Owner: `report/render_markdown_document.py` plus
  `report/review_groups.py` (the grouping already exists; what is missing
  is a family-level rollup for hygiene and a default cap).
- **Performance telemetry misses almost all runtime.** Wall time was ~31s;
  `extractor.duration_seconds` reported 0.457s. Header discovery, AST
  parsing, graph construction, detector execution and report generation are
  all invisible. Owner: whatever publishes `evidence_metrics` -- the point
  is one phase table covering the whole run, not another individual timer.
- **Byte-identical binaries are categorized as a coverage warning.** That
  the two artifacts match is useful provenance, not reduced coverage --
  especially on a run that still parsed headers, where
  `confidence.note_if_same_binary_compared` already words it as "any
  difference would have to come from the header/build evidence". It belongs
  in an informational observation channel, which does not exist yet; adding
  one is the actual work, and inventing it as a side effect of this fix
  would have been the wrong place for it. (The same run also suggests
  reusing/caching binary-side extraction when the digests match while still
  scanning headers -- a separate performance opportunity.)

Three further items from that report are **not** `abicheck` defects and are
recorded only so they are not re-filed: the 32 retained
`exported_not_public` findings are real SVS visibility hygiene (one
`svs::datatype_v<unsigned int>` instantiation, 31 `std::once_flag`
guard/thunk symbols from IVF/LeanVec) to be fixed there or classified by
policy; the missing DWARF/layout evidence is an artifact-build limitation;
and the configured header exclusions match nothing and should be removed
from the SVS integration.

### The release public surface is acquired once, but each member snapshot still stores its own copy of the header evidence

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The per-member header evidence is still stored once per member. abicheck/storage/import_bundle_facts.py:107 still has _BUNDLE_FACTS_SCHEMA_VERSION = 3, so it still refuses schema 4.


Recorded with the release product model (2026-09-17), measured rather than
assumed. The *output* cardinality is closed: the contract is reconciled once,
a shared finding is rendered once, and a release's report size now scales as
O(public surface + members + real findings) — 8.7 MiB -> 77 KiB on a
12-library / 480-declaration fixture. Two costs are not closed:

1. **Storage.** Every entry in `BundleFacts.per_library_snapshots` still
   carries its own copy of the header evidence its dump parsed.
   `BundleFacts.public_surface` (schema 4) gives the *product contract* one
   canonical home and records the acquisition identity it was acquired
   under, which is what lets a stored baseline be reconciled against the
   union of its members' exports — but it does not deduplicate the
   per-member header evidence beside it. Doing that is a change to snapshot
   persistence itself (a shared-surface reference plus a migration for every
   existing baseline), not to the release layer, which is why it was
   recorded rather than half-done here.
2. **One extra header parse per side.** The release-level surface is acquired
   through `header_only_dump.build_header_only_snapshot`, whose acquisition
   key legitimately differs from the member dumps' own (a header-only parse
   resolves its language mode without a binary's export evidence), so it
   does not hit the AST cache entry the member dumps share. Measured on the
   two-library fixture: 2 castxml cache entries before, 4 after — i.e. two
   parses per side rather than one, and **O(1) per side either way**, never
   O(members). The requirement it does meet is the one that mattered:
   castxml is not invoked once per library for an identical acquisition
   request, and the release acquisition ledger reports exactly one
   acquisition per side (`public_surface_reconciliation.acquisition`).
   Closing it means letting the release surface reuse a member dump's
   already-parsed header AST under the *same* acquisition key, which is a
   change to how `dumper`'s language-mode resolution keys a header-only
   parse — worth doing, and not a correctness issue in the meantime.

3. **The `ProjectSnapshot` import adapter refuses a schema-4 document.**
   `storage/import_bundle_facts.py` has no composition section for
   `public_surface`, so it keeps its own `_BUNDLE_FACTS_SCHEMA_VERSION = 3`
   and its existing "newer than this build knows how to interpret" gate
   refuses such a document outright. That is deliberate, and it is the
   fail-closed half of "no silent reinterpretation": carrying the version
   forward without the block would import members whose recorded contract
   had been discarded, leaving them reconciled against nothing. It is still
   a gap. The adapter has no in-tree production caller today (it is a
   public storage API exercised by tests), which is why teaching it a
   `public_surface` composition section -- another `storage/dto.py` section
   version -- was recorded rather than done here.
   `tests/test_release_public_surface.py::TestBundleFactsSchema::test_the_project_snapshot_importer_refuses_a_v4_document`
   states the refusal so it cannot silently become a drop.

Also open, and smaller: the JUnit projection of a release carries per-member
test cases only, so the release-level public-surface section appears in the
JSON and Markdown renders but not there. The gate is unaffected — a
release-level contract finding folds into `worst_verdict` and the exit code
before any format renders — so this is a traceability gap in one format, not
a missed finding.

### Multi-library compare memory: --bundle-facts-out still pins OLD snapshots per member

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed: JUnit uses SymbolInventory, compressed baseline streaming, and the attrs aliasing. Still open: OLD-side retention for --bundle-facts-out (no spooling handle). Graph sharing and Fact pooling were rejected as negative results, and the surface_graph ordering question is unverified.


Three retention defects were measured and closed — per-side/per-consumer
member retention, the unbounded content-keyed half of the AST acquisition
table, and the baseline write's three simultaneous full-size copies (see
[`docs/contribute/memory.md`](memory.md) for the numbers and the harness).
This entry records, deliberately, what is **not** closed, so the next
attempt starts from the measurement rather than from the same three places.

**The OLD side is still retained in full, per member — but only for
`--bundle-facts-out` now.** *Half of this is closed:* JUnit was the second
consumer and turned out to read four attributes off the snapshot, so it now
takes the compact `model.symbol_inventory.SymbolInventory`
and the snapshot is released when the member's comparison finishes.
`--bundle-facts-out` genuinely needs the whole document and still pins it.
Bounding *that* means *spooling completed members* — writing each member's
OLD snapshot to the storage codec as its comparison finishes and reading it
back when the folds run — rather than deciding retention. What makes it a
real change and not a tweak: `--bundle-facts-out` additionally resolves
*stranded* libraries that never produced a pair. The archive writer
(`storage/bundle_facts_archive.py`) already spools encoded blobs and is the
mechanism to reuse; what is missing is a bounded, lazy per-member handle
that consumer can take instead of a live object.

**A compressed baseline write no longer joins.** *Closed.*
`storage/incremental_encode.py` compresses the same fragment stream chunk
by chunk, and the determinism story the previous note asked for was
supplied rather than assumed: gzip's frame is assembled here with `mtime=0`
and `OS=0xFF` pinned explicitly and a raw-deflate payload from one
`zlib.compressobj`, which is **byte-identical** to the previous
`gzip.compress` output for every chunking down to one byte; zstd is
byte-identical whenever the decoded size is known up front. One residual:
the bundle-facts producer cannot state that size (it is streaming), so its
zstd frames omit the declared content size. The frame is legal, round-trips,
and `validate_zstd_frame_completeness` already handles `CONTENTSIZE_UNKNOWN`
— but it loses the declared-size cross-check that catches a frame truncated
mid-header, and its bytes differ from the one-shot encoder's for the same
content. A caller that *can* cheaply state the total should pass
`decoded_size=`.

**Three attrs dictionaries per graph entity: closed, and worth reading for
the shape of the next one.** Every `GraphNode`/`GraphEdge` held
`facts[0].attrs`, `resolved` and `attrs` with equal contents in the
single-producer shape a real graph is almost entirely made of (a measured
six-library release: 10,277 producer facts across 2,771 nodes and 7,506
edges, exactly one fact each). `model.graph_facts.resolve_entity_attrs`
returns the fact's own dict for that case and `ensure_facts_and_resolve`
aliases `attrs` to it — measured at −2.43 MiB and −13,851 objects on a
graph of that shape, and multiplied by the member count while
`--bundle-facts-out` retention holds. The aliasing is sound only because
`attrs`/`resolved` were *already* derived views that
`ensure_facts_and_resolve` overwrites on every call, which is why a direct
`entity.attrs[k] = v` was already documented as silently dropped and no
production call site performs one. **Do not extend the aliasing to the
multi-fact case**: that dict is a genuine merge result and sharing it would
make one producer's backfill rewrite another's evidence.

**Whole-graph sharing across release members was investigated and not
shipped.** The audit's observation — that all 10,277 serialized node/edge
payloads matched by content hash across six OLD-side member graphs — is a
statement about *that fixture's* shared headers, not a licence to share the
objects. `build_header_only_graph(snap, ast_root, ...)` takes the member's
own snapshot, so a correct sharing key would have to cover every
declaration that snapshot contributes, not just the header/compiler/macro/
target context the brief lists; and the graph is *mutated* after
construction (`augment_graph_with_includes`, `degraded_passes`,
`extractor_passes`, `finalize()`), so a shared instance would need a
copy-on-write or freeze boundary that does not exist today. The raw AST
*is* already shared (`dumper_cache`'s in-process memo plus the disk cache),
so what would be saved is construction time and per-member graph storage,
not re-parsing. Reducing the per-entity storage was the sound half and is
what shipped; sharing the graph itself needs the same
inventory-of-every-mutation work the shared-header-declaration item below
already calls for, and must not be justified by one fixture's content-hash
coincidence.

**Scalar `Fact` pooling was measured and deliberately not shipped.**
Pooling immutable scalar `Fact` values on `Function`/`Param` after
construction reduced a whole six-member scan's parent peak by **4.4%**
(127.1 MiB against 132.9 MiB on a small fixture) — a poor return for a
change that must preserve the `UNKNOWN`/`ABSENT`/`PARTIAL`/`FAILED`/
`PRESENT` distinctions exactly and must never share a mutable list/dict
payload. In a *narrower* controlled experiment (twelve 5,000-function
models, 116.37 MiB of Python allocations) the same pooling reached 57.77
MiB, which is the reason not to drop the idea: the gap between the two says
the win is real in the model objects and is being swamped by everything
else resident during a real scan. The right next step is therefore **not**
to pool after construction but to avoid the duplicate construction — and
per `AGENTS.md`'s own guidance, the larger follow-on (shared immutable
header declarations plus per-member bindings; two shared surfaces plus
member overlays measured 22.89 MiB in the same experiment) requires an
inventory of every mutation of a `Function` after construction and an
explicit ownership boundary first. Sharing today's mutable `Function`
objects by reference is not that work and must not be presented as it.

**Concurrency was not re-tuned.** The per-worker memory clamp
(`workflows/release_jobs.py`) sizes off a budget described as "each holding
up to two full snapshots resident". That description is still accurate:
retention now keeps at most one full snapshot per member *after* a
comparison, but both sides are live *during* one, which is what the clamp
budgets. Changing the constant without measuring a real fan-out's
co-resident set would repeat the gap that constant already carries (see
`perf.release_admission_ignores_co_resident_set`'s own known gaps). A
process pool is separately ruled out on evidence: four processes on a fixed
Python-heavy workload took process-tree PSS from ~57 MiB to ~226 MiB.

**The matcher work is a CPU change, not a memory one, and is out of scope
here.** Token-indexed and trie prototypes preserved nesting, ordering and
range semantics and cut synthetic scanning time substantially, but a Python
trie *retained* 6.91 MiB against a regex's 1.39 MiB. If it is taken up, it
needs adversarial common-prefix and randomized differential tests against
the current matcher as oracle, and it must not be justified as a memory
fix. `id()`-keyed match caching was measured and rejected: warm production
lookups are ~0.60 µs, stable string keys already benefit from cached
hashes, and interning inside each lookup was worse.

**No oneDAL operand was available.** `/mnt/cached_oses/napetrov/tmp-abi/l2b6/`
does not exist in this workspace, so the 35:47.83 / 17.82 GiB six-library
figures could not be reproduced, the exact command/frontend/cache state
behind them could not be established, and **none of the reductions recorded
here is a measured oneDAL reduction**. The fixture used is a real compiled
six-member C++ release, which is the right *shape* and a far smaller
*scale*. Whether the whole job fits a nominal-16-GB runner is therefore
untested; the next experiment is to run
`scripts/bench_release_memory.py --keep` against the real oneDAL tree with
`--trace`, and read `release.ast_scope` and `release.member.retained`
against the sampled cgroup peak to see which of the two remaining owners
(OLD-side member retention, or one member's own working set) dominates
there.

**Unverified: `surface_graph` member ordering across runs.** The
before/after baseline documents in the measurement above were equal on
every field except the ordering within `surface_graph.nodes`/`edges` (equal
multisets, identical `graph_id`). Nothing on that branch touches
surface-graph construction and the streaming encoder is byte-identical to
the eager one over a fixed document, so pre-existing run-to-run
nondeterminism is the likely cause — but the check that settles it (run the
*unchanged* base revision twice, diff the two baselines) was started and
lost when the workspace's `/tmp` was cleared, and is therefore **not**
claimed. Anyone touching baseline determinism should run it first:
`python scripts/bench_release_memory.py --members 3 --apis 120 --records 10
--variants bundle-facts --repeat 2` and `cmp` the two `baseline.json`
files. If they differ, the ordering is genuinely unstable and that is its
own (pre-existing) gap, not a property of the streaming write.

### Spelling alternation matcher scales linearly with vocabulary (nested same-offset bug fixed)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The under-report half is fixed and pinned by tests/test_spelling_nested_same_offset.py. The entry itself says the alternation-scaling half (linear in vocabulary size) remains open.


**Status: fixed** in its own change, as this entry required (the
under-report half only -- the scaling half below remains open). Present at
`950efbc64` through `e9d820797`. `finditer_allow_nested` now probes every
boundary-valid offset and, at each one, enumerates candidates longest first
by re-matching with a lowered `endpos`, re-checking the right boundary
against the real text so the `endpos`-as-end-of-string trap described below
cannot accept `Foo` inside `Foobar`. It still uses the compiled alternation,
so it needs no second index and no change to any caller.
`tests/test_spelling_nested_same_offset.py` pins the table below and checks
the new matcher against an independent brute-force oracle (every substring,
both boundaries judged on the real text), plus the strict-superset property
against the previous implementation. The history below is kept for why the
fix is shaped this way.

`compare/spelling_pattern.py`'s `finditer_allow_nested` finds nested matches
by re-searching the window `(m.start() + 1, m.end())` after each match. That
window excludes `m.start()` by construction, so a **shorter registered
spelling beginning at the same offset as a longer match is never reported**.
The alternation is ordered longest-first, so the longer one always wins the
position and the shorter one has no second chance.

Reproduced on entirely realistic spellings (not randomized inputs) — in each
case the second vocabulary entry is the only match returned, and the first is
a boundary-valid occurrence that is lost:

| vocabulary | text | reported | lost |
|---|---|---|---|
| `Foo`, `Foo<int>` | `Foo<int>` | `Foo<int>` | `Foo` |
| `dal::Table`, `dal::Table<float>` | `const dal::Table<float>& x` | `dal::Table<float>` | `dal::Table` |
| `std::vector`, `std::vector<int>` | `std::vector<int>` | `std::vector<int>` | `std::vector` |
| `Node`, `Node*` | `Node* next` | `Node*` | `Node` |
| `A`, `A&&` | `A&& r` | `A&&` | `A` |

This is reachable in production rather than theoretical: `type_reachability`
registers record spellings and typedef targets into one vocabulary, so a class
template and an instantiation of it routinely co-occur there. The consequence
is a lost reachability edge, which can mean a lost finding.

**The obvious in-place repair is unsound.** Re-searching a *narrowed* window
to find the shorter alternative makes `re`'s `endpos` look like
end-of-string to the right-boundary lookahead, so `Foo` would be accepted
inside `Foobar` — trading an under-report for an over-report.

A correct fix scans each boundary-valid start position and tests the
candidates registered there. A prototype of exactly that (bucket spellings by
a fixed-length prefix; scan positions whose left neighbour is not a boundary
character) was differentially tested against `finditer_allow_nested` over
2,400 randomized vocabulary/text pairs: **70 divergences, every one a strict
superset** — 0 subsets, 0 incomparable. It never missed anything the current
matcher finds.

**Why it is not landed here.** Adding those occurrences adds reachability
edges, which can change findings and the release obligation counts. It
therefore needs its own isolated change and its own transparent rebaseline.
Bundling it into a performance patch would make that patch silently alter
results and would invalidate the semantic-equivalence check the performance
claim rests on — see `AGENTS.md`'s "Validate the user-facing result" and the
instruction to isolate a correctness change rather than ship it as an
optimization.

The same prototype is also the standing answer to the *scaling* half of this
area, measured on this host (microseconds per lookup, 34-character subject):

| vocabulary | 1,000 | 5,000 | 20,000 | 60,000 |
|---|---|---|---|---|
| alternation, diverse spellings | 5.7 | 25.0 | 204.3 | 917.6 |
| indexed scan, same inputs | 1.7 | 1.8 | 1.8 | 1.7 |

Build cost at 60,000 spellings: 1.44 s for the alternation (1.74M pattern
characters) against 0.024 s for the index. The alternation is flat only when
the vocabulary factors to a shared literal prefix or the subject fails on its
first character; for the diverse-namespace shape a real C++ vocabulary has,
a miss is linear in the vocabulary. `compile_spelling_pattern`'s docstring
claimed the scan was "independent of candidate count" and has been corrected.

Full measurements, the environment they were taken in, and an explicit
statement of what they do *not* establish are in
[`measurements/spelling-cache-admission.md`](measurements/spelling-cache-admission.md).
Real oneDAL acceptance is no longer pending: it was measured on six matched
members assembled from published wheels — −19.8% wall (under the ≥20% target)
with bit-identical findings, and **no memory improvement**.

### A snapshot is not byte-reproducible across processes

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: no cross-process determinism check was run. The entry says the producer of the surface_graph.nodes ordering drift was never identified.


**What was observed.** Dumping the *same* library twice, with the *same*
code, in two separate processes produces snapshots whose
`surface_graph.nodes` differ in order: 9 positions out of 108,142 on oneDAL's
`libonedal_parameters`. The node *sets* are identical (0 added, 0 removed),
so no ABI conclusion moves; only the ordering does.

**Why it matters anyway.** It defeats comparing two snapshots by digest, and
any content-addressed caching or reproducibility claim built on one. It is
also a trap for anyone measuring a change by hashing output: a cross-process
A/B will report a difference that the change did not cause. The
haystack-deduplication measurement hit exactly that and had to be re-checked
in one process (where the snapshot *is* byte-identical) before the "findings
unchanged" claim could be made honestly.

**Not investigated further here.** The shape — a small number of adjacent
positions permuted, stable within a process — points at set/dict iteration
over values whose hashes vary with `PYTHONHASHSEED`, but the specific
producer was not identified, and this is not a defect the surrounding
performance work introduced: it reproduces with that work reverted.

### A member's peak memory is the clang JSON AST, not the header graph (2026-09-19)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The 'currently ungated' clause is closed: scripts/check_header_graph_perf.py:267 defines MEMORY_METRICS attach_peak_rss_mib/attach_end_rss_mib. The peak from materialising the clang JSON AST is still open, and the empty-list reduction is a recorded negative result.


Full measurement, with the per-point RSS table and the method:
[`measurements/header-graph-attach-memory.md`](measurements/header-graph-attach-memory.md).
Recorded here because it refutes two plausible fixes that were each tried and
reverted, and because the obvious reading of the numbers is wrong.

On `libonedal_core.so.3` (default `castxml` backend), one `run_dump` reaches
**1968 MiB before the header graph is built at all** — the clang AST parse
alone is ~1.4-1.5 GiB, and the graph build adds 271 MiB on top. The attach
disables the streaming pruner on purpose (`suppress_streaming_prune()` in
`service_header_graph_attach`, because `parse_clang_ast_calls` walks the raw
AST for `DECL_CALLS_DECL` edges, Codex review PR #840), so the whole tree is
materialised; the cached document for these headers is 1.3 GiB of JSON.

**Do not read "dropping the graph frees 1.2 GiB" as "the graph costs
1.2 GiB".** The graph's own objects are 251.7 MiB across 2,830,703 objects by
a full `gc.get_referents` walk. Dropping the *AST* returns only 222 MiB; the
rest comes back only when the graph is dropped, because the graph's
long-lived objects sit in pymalloc arenas the AST parse dirtied and an arena
cannot be returned while anything in it is live. The graph pins that memory,
it does not spend it.

**Attempted and reverted: making the graph's objects cheaper.** The graph
allocates 959,016 lists of which ~958,000 are empty (711,436 `conflicts`
lists hold **6** `FactConflict` objects in total; node `attrs` averages 0.0
entries). Defaulting the empty ones to the singleton empty tuple removed
603,290 objects — 21% of the graph's entire object count — and saved
**40 MiB**. The prediction had been ~240 MiB, from dividing RSS by object
count and applying that average as a marginal cost; empty lists are among the
cheapest objects in the population, and 603,290 x ~64 B is exactly the 40 MiB
observed. Reverted: not worth changing a model field's type and seven test
expectations for 3.7% of the graph's retention. The same average-as-marginal
error is what produced the withdrawn "empty-result prefilter" estimate
recorded earlier in this file — check a marginal cost against the specific
population being removed, never against a population-wide average.

**Also ruled out, each by its own measurement:** the allocator is not holding
it (`malloc_trim(0)` returns 96 MiB of 2138), it is not file-backed or shared
(99.4% of RSS at peak is anonymous private-dirty), there are no garbage cycles
(`gc.collect()` frees exactly zero), the attrs mapping is not duplicated
(clearing `facts` and `resolved` frees zero — `attrs`/`resolved`/
`facts[0].attrs` are one object), and the `BuildSourcePack` holds nothing
besides the graph (35 objects, 0.0 MiB).

**A model stated and refuted.** The six-member release peak was explained
during this work as `admitted workers x per-member peak`; the arithmetic fit
(2 x 5.7 GiB plus the parent is ~7 GiB, against 7077.8 MiB measured). Running
the same comparison at **one** worker peaks at **7080.6 MiB** -- unchanged, so
concurrency is not what produces it. Whether it is accumulation across
members, one large member costing it alone, or the once-per-side acquired
public surface held for the run is not established. A fitting arithmetic is
not a mechanism.

**Still open.** Reducing a member's peak means not materialising the AST as a
Python dict tree — either building the graph from a streaming parse, or
pruning the AST to what the graph needs first. Neither is attempted here, and
the existing `check_header_graph_perf.py` gate measures the attach's *time*
only, so this dimension is currently ungated.

### MinGW toolchain headers are not classified as dependencies (2026-09-23)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/provenance.py:270 _is_toolchain_compiler_include_dir still recognises only lib/gcc/<triple>/<ver>/include, lib/clang, and castxml layouts. provenance.py has no handling for MinGW's <prefix>/<triple>/include.


Found by `tests/test_header_only_dump.py::TestHeaderOnlyDependencyScoping` on
the `windows-latest` integration lane. After #1347 made header-only dumps apply
the default toolchain-declaration exclusion, a castxml dump on MinGW still kept
`_mingw.h`'s `__debugbreak`. castxml reports that header as
`C:/mingw64/bin/../lib/gcc/x86_64-w64-mingw32/15.2.0/../../../../x86_64-w64-mingw32/include/_mingw.h`,
which collapses to `<prefix>/x86_64-w64-mingw32/include/`: GCC's per-target
sysroot include directory. `provenance._is_toolchain_compiler_include_dir`
recognises `lib/gcc/<triple>/<version>/include` but not this layout, so it
isn't treated as a system header. This affects binary+header dumps on MinGW too,
not only header-only ones.

Not fixed in #1347 because the obvious pattern, `<prefix>/<triple>/include`, is
not safe on path shape alone. `_TARGET_TRIPLE_RE` accepts any 2-4 hyphenated
components, so an ordinary project directory like `my-lib/include` would match
and the project's own API would be dropped: exactly the failure this
classifier must never cause. A sound fix needs positive evidence that the
directory belongs to the toolchain. Options: a sibling `lib/gcc/<triple>/`
under the same prefix, or the compiler's own reported search list
(`-print-search-dirs` / `-v`).

On the same lane, clang cannot parse MinGW's libstdc++ `<cstdio>` for a
header-only dump (`__STRICT_ANSI__` warnings, then errors). This is an
environment limitation of clang with MinGW headers, not something abicheck
causes. That test is skipped on Windows until both are resolved.

### Destructive test-helper resets are guarded one helper at a time (2026-09-30)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** No gate guards against rmtree of a caller-supplied path: 40 rmtree call sites remain under tests/ and scripts/, and the entry says the /tmp canary detector is not wired into CI. Not further verified.


The "something prunes `/tmp` on these runners mid-job" behind `ci.yml`'s
`TMPDIR=$RUNNER_TEMP` step was a unit test: it handed
`scripts/check_l2_cli_perf.py`'s `_reset_cache` the path `/tmp`, and that
helper was a bare `rmtree`. It now empties only a cache root the harness
created and marked (`prepare_cache_root`), refusing anything else
(`tests/test_l2_cli_perf_cache_reset.py`; bug class
`test_harness.destructive_reset_of_a_caller_supplied_path`). What remains
open: no gate stops another helper under `tests/` or `scripts/` from
`rmtree`-ing a caller-supplied path, and the detector that found this one
(a canary file under `/tmp`, checked before and after every test by a
`pytest_runtest_protocol` hookwrapper, per-worker timestamps to name the
overlapping test) is not wired into CI. The `TMPDIR=$RUNNER_TEMP` step stays:
it is cheap isolation and would contain the next such helper.

### The clang JSON header backend records no resolved type identities, so a same-leaf record stays `UNKNOWN_UNRESOLVED` under `--contract public` (2026-10-01)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for type_identities under abicheck/extract/headers/clang/ finds nothing, so the clang backend still records no identities.


Schema v54 closed `public-contract-default.md` Phase 6's
`ambiguous_namespaced_leaf` defect at extraction. castxml resolves every
type slot to one element of its type graph, and
`extract/headers/castxml/type_resolution.type_identities` records that
element's qualified name (`Function.return_type_identities`,
`Param`/`Variable`/`TypeField.type_identities`). The exact public-surface
walk and the contract evaluator then know that `api()` returning the bare
`Cache *` reaches `ns1::Cache`, not `ns2::Cache`.

**What is still open:** the clang backend (`--ast-frontend clang`) writes
those fields as `None`, meaning not captured. `clang -ast-dump=json` gives a
declaration's type only as a `qualType` string (`"Cache *"`), with no
reference to the declaration it names, so the JSON carries no identity to
record. Reconstructing one from C++ name lookup (enclosing namespaces plus
using-directives) would be a heuristic. That is exactly the kind of guess
the exact walk exists to refuse, so it was not attempted. DWARF-sourced
snapshots and every pre-v54 baseline are in the same position. For all of
them the answer is the pre-v54 one: a break on a record whose leaf another
record shares stays `UNKNOWN_UNRESOLVED` under `--contract public`. It
raises the coverage floor (exit 1), not the ABI gate.
`scripts/check_fp_rate.py`'s `ambiguous_namespaced_leaf_spelling_only` case
pins that shape as the one explained `public` loss in
`scripts/measure_contract_shadow.py`.

**What would close it:** a clang-side source of declaration references. The
optional facts plugin (`contrib/abicheck-clang-plugin`) runs inside clang's
semantic analysis and could emit the same per-slot identity. DWARF's
`DW_AT_type` references a DIE whose scope chain is known, so the DWARF
snapshot path could populate the fields too. Either is additive: the
consumer side already reads the fields from any producer.

### A library's own `_Float128` API has no correct mangled name from castxml

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: castxml mangling was not run. The entry records the defect as an unresolved castxml limitation: _Float128 functions read as not exported.


Found while fixing castxml on binary128 `long double` targets (`extract/
castxml_header_compat.py`). The **type** spelling is right on every target
(`_Float128`), but castxml's `mangled` attribute for a function taking one is
not the Itanium name the compiler emits (`_Z2qfDF128_P1Q`): on x86-64 castxml
reports the bare name (`qf`), on AArch64 a name embedding the stand-in record
(`_Z2qf19__abicheck_Float128P1Q`). Either way the export join misses, so such a
function reads as not exported on both architectures. A token substitution is
not a fix (Itanium substitution numbering differs between a class name and a
builtin type), so it was not attempted. glibc's own `*f128` declarations are
system headers and unaffected.

### `compare --no-baseline` has no typed Python request, scalar or directory (2026-10-03)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for 'NoBaseline.*Request' in abicheck/ finds nothing, so there is still no typed no-baseline request.


Neither shape of the single-build audit -- `compare --no-baseline FILE` nor
the N-library `compare --no-baseline DIR` (one-comparison-product F-23) --
has a typed request in `abicheck.service` the way two-sided `compare` has
`CompareRequest`/`run_compare_request`. Both are reachable from Python only
through the framework-free workflow functions the CLI itself calls:
`workflows.no_baseline_compare.audit_no_baseline_candidate` (one candidate,
given a `NoBaselineAuditInputs`), and `workflows.no_baseline_set.
resolve_no_baseline_set_plan` + `run_no_baseline_set` (a set), rendered by
`report.no_baseline.render_no_baseline` / `report.no_baseline_set.
render_no_baseline_set`. What is missing is the front-end half: resolving
`.abicheck.yml` (`scope.public`, `scope.on_incomplete`, `release.*`,
`deployment:`, `assurance.require_complete`, the suppression acceptance
checks) and the policy documents into those inputs, which today only
`frontends/cli/commands/no_baseline_invocation.py` does -- so a Python
caller has to restate it, and could resolve a config differently from the
CLI. Deferred deliberately when F-23 landed: the request type should be one
decision covering both cardinalities (ADR-061's "one model, any
cardinality"), not a directory-only addition.

### The L2 header parse never captures a dependency file (2026-10-03)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** depfile_resolved_paths is referenced only inside abicheck/comparability.py:563, and no production caller passes it.


Also from Stage E: `compute_extraction_contract`'s `depfile_resolved_paths`
and `generated_driver_path` are never passed, because no castxml/clang L2
invocation requests `-MD -MF`. ADR-050 D1 describes `include_sequence` as
hashing the content of every file the parse actually read under each external
`-I` root, plus a system/toolchain bucket; with no depfile both are always
empty. Two dumps that read different dependency headers (a newer
`/opt/dep/include`, a libstdc++ update changing an ABI-relevant macro) get
identical `profile_fingerprint`s, the under-counting this digest exists to
prevent. `buildsource/include_graph.parse_depfile` already parses the format.
Building the capture changes fresh fingerprints the same way the target
platform entry above does, so it needs the same unrecorded-side carve-out.
Owner: ADR-050; until then ADR-050 D1 overstates what is fingerprinted.

### A snapshot's `build_mode` is captured only for ELF, and only from the main image

**Status: PARTIAL (2026-10-10).** ELF dumps now record `build_mode` from `DW_AT_producer`/`DW_AT_language` and `.comment` (`extract/build_mode_capture.py`, wired in `workflows/snapshot_factory.finish_binary_dump`); this also fixed a GCC version regex and a wrong `DW_LANG` table in `build_mode.py`. Still open: PE and Mach-O get no `build_mode`; a separate debug file (`debug_info_path`, detached debuginfo) is not read, so capture falls back to `.comment`; nothing yet compares compiler family across snapshots.


From Stage E: `build_mode.build_mode_from_signals` takes `raw_producer`
(`DW_AT_producer`), `raw_comment` (ELF `.comment`) and `dwarf_language`, and
no production call passes any of them. Its one caller,
`diff_stdlib_impl`, passes mangled symbols only, so the stdlib dimensions
(family, libstdc++ dual ABI, libc++ ABI version) are inferred at compare time
while `compiler_family`, `language_std` and the provenance strings are always
`UNKNOWN`/empty. No dump path sets `AbiSnapshot.build_mode` either: the field
is only read back from a stored document that already carries one.
`detect_compiler_family` and `detect_cxx_standard` are tested but have no
production producer feeding them.

The parameters are kept rather than removed, because removing them would
leave both detectors reachable only from tests. The evidence exists
elsewhere: the L3 compiler record (`buildsource/compiler_record.py`) parses
the producer string, and the DWARF parse reads each CU's attributes. Proposed:
carry `DW_AT_producer`/`DW_AT_language` out of the DWARF parse (and `.comment`
out of `elf_metadata`), populate `build_mode` at dump time, and decide which
detector reads it, with a test over {GCC, Clang, ICX} x {producer present,
stripped} against the compiler's own banner. Owner: the build-mode work
(`abicheck/build_mode.py`).

### A stored package's generation drift is reported only for `ProjectSnapshot` packages, and generations are bumped by hand

**Status: PARTIAL (2026-10-10).** `EXTRACTOR_GENERATION`/`RESOLVER_GENERATION` now exist (`storage/versioning.py`), every package writer stamps them, `project_snapshot_store` passes them to `check_reader_compatibility`, and drift appears in `coverage_warnings` without changing verdict or exit code; an unrecorded generation is unknown. Importers stamp the generations of the build that extracted the facts: this build's only for facts it just dumped (`bundle variants` capture from binaries), a caller-supplied pair when known, else unstated (`StorageVersions.written_by_this_build`'s `generations`). Still open: the `BundleFacts` reader passes them but has no report surface; nothing enforces a bump when extraction or resolution semantics change.


From Stage E: `storage.versioning.check_reader_compatibility` reports
`semantics_differ` only when the caller passes its own
`reader_extractor_generation`/`reader_resolver_generation`, and neither caller
(`project_snapshot_store`, `storage/bundle_facts_package`) does. No build-side
generation constant exists to pass, and nothing reads
`ReaderCompatibility.semantics_differ`. So ADR-062 D2's non-fail-closed half
is unbuilt: a package produced under an older resolver is read as if its
derived results were today's, with no notice. The two fail-closed axes
(package format, comparison contract) are wired and unaffected.

Proposed: define the reader's current extractor and resolver generations next
to `PACKAGE_FORMAT_VERSION`, bump them when extraction or resolution semantics
change, pass them from both readers, and surface `semantics_differ` in the
report that loaded the package. Owner: ADR-062.

### The clang backend's guard retry still maps an aggregate line to a header (2026-10-03)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/headers/clang/error_header_retry.py:69-79 still maps the aggregate line index to the header. The entry notes it is latent: not broken unless a preamble is added. The writers are now in extract/headers/clang/backend.py:304 and clang_layout_tool.py:203.


`extract/unparseable_header_fallback.py` attributed a castxml diagnostic to
header `N-1` from the aggregate frame's line `N`. When the castxml aggregate
gained a preamble include on its first line, that rule dropped the healthy
neighbour of a rejected header and the directory dump failed. It now names
the input by the file the aggregate frame includes, which does not depend on
the aggregate's layout (bug class `extraction.aggregate_layout_inverted_by_line`).

The clang backend's direct-inclusion-guard retry
(`extract/headers/clang/error_header_retry.py`, `_headers_failing_in_aggregate`)
still uses the line rule. It is correct today: both of its writers
(`dumper.py`'s `_write_agg`, `clang_layout_tool.py`'s `_write_agg`) put one
include per line with nothing before them. A preamble added there would
reintroduce the defect; attributing by the included file, as the castxml
fallback now does, closes it. Not changed here because nothing is broken and
the function's test suite encodes the line layout in every case.

### Scalar and release `compare` still fold the exit code in two places (2026-10-07)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** There are still two folds: policy/exit_decision.py:610 resolve_compare_exit_decision and policy/exit_decision_precedence.py:358 resolve_release_exit_decision.


`tests/test_compare_cardinality_invariance.py` pins, for cardinality 1..4,
that a release member's findings, `disposition_audit` and verdict equal the
scalar `compare` of the same pair, and that the release exit code is the
worst member's. Both paths now share the per-member primitive
(`workflows/member_compare.compare_member` -> `run_compare_request`), but the
exit fold is still two implementations: the scalar one in
`policy/exit_decision.py::resolve_compare_exit_decision` (driven from
`cli_compare_fold`/`cli_helpers_compare`) and the release one in
`policy/release_exit_decision.py` -> `exit_decision_precedence.resolve_release_exit_decision`.

Axes only the release fold has, which a single fold must keep and which have
no scalar input to exercise them:

- exit 8 for a proven-removed required library, whose precedence depends on
  the scheme (severity mode: above the verdict, coverage and operational
  axes; legacy mode: below any nonzero verdict);
- a member's operational `ERROR` floored to 4 (the scalar path aborts instead,
  with its own codes: 16 not comparable, 5 budget, 7 evidence contract);
- the release-global verdict (bundle and probe-matrix findings) folded with
  `max` into the legacy code;
- ADR-065's incomplete-scope and no-comparison-completed contributions;
- the lockstep-SONAME suppression pass, which needs a BREAKING sibling and is
  inert at N=1.

Also still scalar-only: `cli_compare_helpers.run_compare` resolves its own
inputs (strict suppressions, packs via `resolve_and_apply`, probe matrix,
`--build-info`, force-public allowlist, `stated_contract_mode`) and calls
`compare_snapshots` directly rather than `run_compare_request`. The evidence
fold between resolution and classification (build-source diff, abi3 audit) is
already shared: `workflows/pair_evidence.fold_pair_evidence`. Unifying the
fold means generalizing the release fold so N=1 reduces to the scalar one;
any exit-code difference that exposes at N=1 must be decided before it
changes (ADR-064). Owner: ADR-063/065, lane A stage A2(b).

### A stored snapshot keeps no record of a partial export-table read

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The entry says closed, but it also says the partial read is still open. Fixed: FAILED import reads; tests/unit/workflows/test_failed_consumer_read.py exists. Open: a stored snapshot cannot record a partial export-table read (needs a schema field and ADR).


**Closed (2026-10, Lane C stage 5).** `workflows.consumer_scope.read_consumer_facts`
now treats a `FAILED` consumer-import fact exactly like an unrecognised
format: a REQUIRED consumer raises `ConsumerUnreadableError`, an ADVISORY one
yields `unreadable=True`. Regression tests:
`tests/unit/workflows/test_failed_consumer_read.py`. The library side
followed in Lane C stage 6: `parse_{elf,pe,macho}_metadata` still return what
they read, but each site that loses export-table facts records it
(`extract/parse_failures.py`), and `read_library_export_facts` reads such a
library -- or a stored block that records no parse
(`model.export_index.platform_block_parsed`) -- as `FAILED`. The scoping
workflow then raises `LibraryExportsUnreadableError` (`compare --used-by`:
exit 1, `Error: --used-by library: ...`). Regression tests:
`tests/unit/extract/test_library_export_read_failures.py`. Still open: a
stored snapshot keeps no record of a *partial* read (a skipped `.dynsym` with a
parsed header) -- persisting it needs a snapshot-schema field and its ADR;
`dump` rejected every such binary tried (ELF and whole-file PE/Mach-O
failures), so in practice only a snapshot produced elsewhere can carry one.

## Negative results — do not re-attempt

Approaches that were tried, measured or reviewed and did not help. Their value is stopping a re-attempt.

### Linkage-blind removal — attempted twice, reverted twice. The evidence keeps proving something adjacent to the invariant

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records two reverted demotion attempts, which should not be re-attempted. The comdat sub-item is still open: COMDAT collection remains opt-in (build_evidence.py:426, env_flag ABICHECK_COLLECT_COMDAT), and adapters/compile_db.py has no output/-o handling (grep finds no 'output').

A symbol vanishing from
the export table is reported as `func_removed` (and, on the same symbol,
`func_deleted_elf_fallback`) regardless of its *linkage*, so a weak
vague-linkage export reads as a hard break. The demotion's invariant is
*"every consumer already emitted its own copy"*, and both attempts
established something else:

1. **`Function.is_inline`** proves the declaration's inline *linkage*, not
   that a definition exists. Verified against real clang: `inline int f();`
   yields `inline=True, has_body=False`. This stays true after the
   implicit-inline fix (`extract/headers/clang/inline_semantics.py`), which
   widened the field to cover `constexpr`/`consteval`, in-class definitions
   and in-class `= default` so the two backends agree: those are all still
   *linkage* facts about the library's own declaration, and none of them
   says a consumer emitted a copy — which is the shared shape this entry
   exists to name. Read it as "may have vague linkage", never as "every
   consumer already has its own definition".
2. **COMDAT-group membership** proves the *library* used vague linkage, not
   that its *consumers* did. `extern template` is the counterexample, and it
   is ordinary code: a public header carrying `extern template struct
   Box<int>;` tells every consumer TU **not** to instantiate, while the
   library's own explicit instantiation still emits a weak COMDAT
   definition. Verified against g++ — the library object has
   `_ZNK3BoxIiE3getEv` inside a COMDAT group while the consumer object has
   it as `NOTYPE GLOBAL UND` with an empty COMDAT set. Dropping that export
   breaks the consumer, and the predicate demoted it (Codex review).

The shared shape is worth naming, because it is what a third attempt will
hit too: a fact about the **library's own build** was read as a fact about
**its consumers**. Nothing in two library snapshots, and nothing in one
library's object files, distinguishes "the consumer emitted a copy" from
"the consumer holds an undefined reference". Only consumer-side evidence, or
a header fact recording `extern template` (which castxml does not expose —
checked through 0.7.0), can separate them.

**Kept, because it is sound and answers a real question:**
`buildsource/comdat_groups.py` — an ELF `SHT_GROUP`/`GRP_COMDAT` parser
(byte-order correct, ELF-only, degrading to diagnostics on unreadable
objects) and its `BuildEvidence.comdat` collection. It answers "did *this
build* emit this symbol vaguely", which is genuinely useful and was already
reserved vocabulary in `graph_facts.py` (`comdat_group`) for a future
linker-artifact extractor. It is simply not sufficient for the demotion.

**Separately open, and independent of the above:** the L3 collection path
does not deliver usable object paths. `CompileUnit.output` is a label
normalized *for persistence*, not a path — home paths redacted to `~/...`
(ADR-032 D7), Ninja/Make/Bazel outputs relative to `CompileUnit.directory` —
and `CompileDbAdapter` never assigns it at all, discarding the compile
database's `output` field and `-o` alike. **Half-closed on the reading
side:** `build_evidence._resolved_object` now expands `~` and joins a
relative label onto the unit's own `directory`, and skips a label naming no
file that exists, so an adapter that *does* record an output is readable
from outside the build directory. That also makes the scan conservative
where it used to be destructive: a build whose objects resolve to nothing
leaves `comdat` untouched rather than replacing a scan loaded from an
existing pack with an empty, unresolvable one, and a fresh scan that
established nothing never displaces one that did. **Still open:** the
producing side — `CompileDbAdapter` recording an output at all, and every
adapter keeping the raw resolved path alongside the redacted label, so a
pack collected on one machine can be scanned on another. Until then the
scan is opt-in (`ABICHECK_COLLECT_COMDAT=1` at `inline.py`'s call site):
parsing every object's symbol table is real I/O, and no detector consumes
the result.

### A third instance of the same shape (code-review report item 3): demoting a stdlib closure instantiation as "unnameable" — attempted, reverted

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records a reverted attempt to demote stdlib closure instantiations as unnameable. The same consumer-side evidence gap as entry 13 remains, so do not re-attempt.

A stdlib/runtime template instantiated over a caller-
supplied lambda (e.g. `std::once_flag::_Prepare_execution<...Widget::
run()::{lambda()#1}...>`, from a real `std::call_once` guard) mangles to
a symbol whose closure-ordinal encoding is per-translation-unit and
compiler-ordering dependent, so it seemed unconditionally safe to demote
in `surface.classify_change_surface`: "no consumer's *source code* could
ever name this exact template argument, so there is no possible
external caller to break." That reasoning is the identical mistake
the linkage-blind-removal entry above already names, just one layer
removed: *source-level nameability* is not *binary/ABI compatibility*.
A consumer's own object code never has to name the symbol in source —
the SAME template, instantiated from the SAME public header over its
own local lambda, produces the IDENTICAL mangled symbol in the
consumer's own translation unit via vague/weak linkage, and that
consumer can depend on the library's copy being the one that resolves.
A two-snapshot comparison has no way to rule that out, for exactly the
reason the entry above states: "nothing in two library snapshots...
distinguishes 'the consumer emitted a copy' from 'the consumer holds an
undefined reference'." Reverted rather than shipped (Codex review,
two findings — the unsoundness above, and separately that the fix was
dead code for its own ELF-only motivating case:
`post_processing.FilterNonPublicSurface.run` returns unmodified changes
before ever calling `classify_change_surface` when neither side's
surface is resolvable). Closing this for real needs the same
consumer-side evidence the linkage-blind-removal entry says is missing
— not a cleverer read of the mangled name alone.

### Evidence-provider model — investigated, found not to reproduce as described; no fix applied

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Investigated and found not to reproduce. The modules it relies on still exist (dumper_layout_backfill.py, policy/classification.py:619 evidence_status_for_result).

A status-review follow-up asked whether
`evidence_status_for_result`'s report-level downgrade (kind-level
`ARTIFACT_PROVEN` → `UNATTRIBUTED` only when `DiffResult.evidence_tiers`
is header-only for the *whole* comparison) can let an individual
header-derived `BREAKING_KINDS` finding read as artifact-proven merely
because *some other, unrelated* part of the same report had binary
evidence. Traced this for the highest-stakes family it could apply to —
layout findings (`TYPE_SIZE_CHANGED`/`TYPE_ALIGNMENT_CHANGED`,
`diff_types.py`) — and it does not hold up: (1) the direct-clang L2
backend's `RecordType.size_bits`/`alignment_bits` are populated **only**
when `dumper_layout_backfill.backfill_dwarf_layout()` actually
corroborates them against real DWARF (`model.py`'s own
`dwarf_layout_coherence` docstring) — with no DWARF to backfill against,
those fields stay `None` and `_append_type_size_and_alignment_changes`'s
own `is not None` guard means no finding is even emitted, so an
"unconfirmed clang-derived layout finding" cannot occur; (2) the castxml
backend computes struct layout itself, via its own bundled real compiler
targeting the resolved ABI — `model.py` already documents this as
deliberately treated as sufficient L2 evidence ("trivially self-consistent
by construction", not needing DWARF corroboration), a prior, intentional
design decision this pass would have to *overturn*, not merely patch.
The one place this class of risk is genuinely live is exactly the
already-tracked toolchain-identity-probe gap above (castxml/clang invoked
with compiler/ABI flags that don't match the real build) — not a separate
evidence-status bug. A **real** per-finding provider model (recording,
per `Change`, which of L0–L5 actually produced/corroborated it) would
need new provenance plumbing through all ~45 `Change(...)` construction
sites across `diff_*.py`/`buildsource/*.py`, each individually verified
against the FP-rate/mutation-score gates — a multi-day project on its
own, not attempted here.

### L2 performance bottlenecks found by the full-CLI harness (measured, not fixed)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Parent section for measurement-only perf findings from scripts/check_l2_cli_perf.py (exists). Its subsections hold the actual status; this one only frames them.


Found while building `scripts/check_l2_cli_perf.py`. Recorded here rather than
acted on: that work was explicitly measurement-only, and each of these is a
production behaviour change needing its own design and its own evidence that the
change is worth it. Every number below is a real local measurement (gcc 13.3.0 /
castxml 0.7.0 / clang 18.1.3, 4 CPUs, Linux), reproducible with
`python scripts/check_l2_cli_perf.py --suite extended`.

### A set of libraries pays full cost per library, and a shared header context does not reduce it

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Recorded measurement: a shared dependency header does not reduce the extraction count (20 vs 20), and the entry explicitly declines to claim cross-library sharing should be built. Keep it as a measured result.


Five libraries compared as five per-library `compare` invocations (the only
supported shape — there is no declarative L2 bundle comparison), cost ~1.15–1.50 s
each and **4 header extractions each**, for a set total of ~6.3 s. That figure is
the same whether the five libraries share one dependency header or each have
their own:

| arm | resolved dependency headers | extractions (5 libraries) |
|---|---:|---:|
| shared context | 1 | 20 |
| distinct contexts | 5 | 20 |

The mechanism, which is the actually useful part: the header-frontend invocation
count is driven by the **top-level** headers, not by their dependencies, so
sharing a dependency header cannot reduce it. Each library still needs its own
parse of its own public header, and that parse pulls the shared dependency in
regardless of whether another library already parsed it.

**This claim was previously unsupported and is worth flagging as such.** The
first version of the fixture gave every library its own byte-identical copy of
`detail/core.h` under its own `libN/include/` path — and the AST cache keys on
each header's *resolved path*, so the "shared" arm shared nothing. The two arms
were both distinct-path workloads, and comparing them could only ever have
produced "no difference". The fixture now resolves the shared arm through one
physical file at a common include root, `header_contexts` is counted from the
resolved paths actually built rather than from the flag that requested them, and
the numbers above are from that corrected fixture. The conclusion happens to be
the same; the evidence for it did not exist before.

Recorded as a measurement of current behaviour, deliberately not as a claim that
cross-library sharing *should* be implemented: whether a cross-library
header-AST cache is worth its invalidation complexity is a real design question,
and `scripts/l2_real_profiles.py`'s oneDAL profile (five header-bearing
libraries across two compile contexts) is the realistic case to judge it
against.

### Snapshot digest/save amplification is in the sectioning layer, and four attempts to reduce it bought nothing (2026-09-17)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records four measured digest/save reductions, three of them reverted, plus a measurement-method error. The remaining peak in canonical_form needs a storage-format ADR.


Measured while fusing `snapshot_to_dict()`'s two encoding passes into one. The
digest path peaks at roughly **16x the live model** and the encoder is not why.
Stage peaks for `_uncached_snapshot_content_digest()` over a synthetic
20,000-function snapshot whose live model is 36.35 MiB, with the source dict
already allocated and *not* counted:

| stage | peak |
|---|---|
| `snapshot_to_dict()` (after the fusing change) | 102.2 MiB |
| `to_sectioned_document(...)` | **474.0 MiB** |
| whole function | 576.1 MiB |

`to_sectioned_document` repackages the already-encoded document through
`import_legacy_snapshot` -> `InMemoryObjectStore.put`, which builds a section
DTO, a `to_dict()` of it, a `strip_capture_metadata`/`canonical_form` copy, and
a canonical-JSON string for hashing -- several more whole-document copies on
top of the one the encoder just produced. Within one `put` of the largest
section: `canonical_form` 120.3 MiB, the JSON string 46.9 MiB, its `.encode()`
44.2 MiB, `copy_of_canonical_form` (what `store.get` pays) 120.3 MiB.

**Four reductions were implemented, measured, and three reverted.** All are
recorded because each looks obviously worthwhile from the source and is not:

1. **`iterencode` into a running sha256** instead of `dumps(...).encode()` in
   `_uncached_snapshot_content_digest`. Exactly equivalent
   (`"".join(iterencode(o)) == dumps(o)`), removes a 318 MiB string and a
   396 MiB bytes copy -- and moved the measured peak *not at all* (576.1 MiB
   either way), because both allocations happen strictly after sectioning has
   already peaked above them. ~20% slower. Reverted; the reasoning is in that
   function's own docstring.
2. **The same, one level down**, inside `semantic_digest_of_canonical_form`,
   where the string is built *during* sectioning rather than after. Also no
   change (474.0 / 576.1 unchanged), also ~15% slower. Reverted.
3. **`InMemoryObjectStore.detach`** -- `get` returns a defensive deep copy, and
   `to_sectioned_document` builds a store, fills it, reads it all back, and
   discards it, so that copy guards an object about to become garbage.
   Removing it eliminates a real 120.3 MiB copy. It changed neither the peak
   (474.0 MiB before and after) nor the time (45.61 s vs 45.69 s). Reverted.
4. **`hashlib.new(algorithm, domain + payload)`** -> two `update()` calls.
   This one was **kept**: the concatenation materialises a complete second copy
   of the payload (44.2 MiB on this fixture, 0.0 MiB after) purely to prepend
   five bytes, and removing it costs nothing and adds no API. It does *not*
   move the end-to-end peak either, and is kept as waste removal rather than
   as a measured improvement.

**A measurement error worth not repeating.** Attempt 3 was first reported as
576.1 -> 474.0 MiB, an apparent 18% win. It was an artifact: the "before"
figure came from a script that allocated the source dict *after*
`tracemalloc.start()` and the "after" from one that allocated it before, so the
two differed by exactly the 102 MiB dict and not by anything the change did.
Running the *same* script against the unmodified base revision showed 474.0
both ways. Any before/after here must come from one script run against two
revisions -- a `git worktree` of the base is the cheap way -- never from two
scripts.

**What actually remains.** The peak is the DTO -> `to_dict()` ->
`canonical_form` chain, each link of which is a whole-document container tree,
and a live-allocation attribution at the end of sectioning confirms the residue
is `canonical_form`'s own rebuilding (`storage/canonical.py:198-202`). Reducing
it means changing how sections are built and addressed, and the digests
involved are persisted content addresses -- a storage-format decision needing
its own ADR and migration, not a serializer patch. ADR-063 Phase 8's
`project-snapshot-dto-no-asdict` gate already points at the same DTO files,
which is the natural home for that work.

Note this is the *digest and single-file save* path specifically. The
multi-library bundle writer does not go through it -- it encodes each member
with `snapshot_to_dict()` directly, and its own whole-bundle retention was
fixed (see `storage/bundle_facts_archive.py`). **No claim is made here that
large-library OOM is solved.**

### The release fan-out's future map is not a last owner, so dropping it saves nothing (2026-09-17)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** A negative result: dropping futures saves nothing because results_by_key holds the same objects. abicheck/cli_compare_release_pairwise.py still exists.


Checked while looking for lifetime wins after the encoder/archive work above,
and recorded as a negative result so it is not "fixed" speculatively later.

`cli_compare_release_pairwise.py`'s parallel fan-out builds
`futures = {executor.submit(...): key for key in matched_keys}` and keeps the
whole mapping alive until the `with ThreadPoolExecutor(...)` block exits. That
looks like per-member results being retained for the length of the run, and it
is a real retention — but not of the results. `results_by_key[key] =
future.result()` stores *the same object* the completed `Future` holds in its
own `_result` slot, so the dict and the future are two references to one
result. Releasing the future frees the `Future` wrapper and nothing else; the
per-library result dict stays owned by `results_by_key`, which the function
returns.

So the available saving here is a few hundred bytes per member, not a member's
worth of findings, and any patch that deletes futures as they complete while
claiming a memory reduction would be measuring its own wrapper objects. A real
reduction on this path has to shrink or stream what `results_by_key` holds —
the per-library result payloads themselves — which is a change to what the
release fan-out returns to its caller, not a lifetime tweak inside the loop.

### Releasing the clang AST before the graph build does not reduce the peak (2026-09-19)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records that releasing the AST before the graph build did not move the 2215 MiB peak. Key-whitelist pruning during the parse is measured but not attempted.


The follow-up to the entry above, recorded because it **did not pay** in the
way its own premise predicted, and because measuring it reattributes the peak
one level further down.

`_attach_header_graph` now projects the AST into the four compact values
`build_header_only_graph` actually reads
(`buildsource/header_graph_ast_projection.py`) and releases the tree before
the graph is allocated, so the two are never resident together. That part
works exactly as intended. On the real reference library — oneDAL 2024.7
`libonedal_core.so.2`, conda-forge `dal`/`dal-devel`, default `castxml`
backend, cold AST cache, one `daal.h`, three fresh processes per side — the
graph build's own residency cost falls from **+147/+148/+143 MiB to
+25/+21/+25 MiB**, because the graph now lands in arenas the AST parse has
already freed instead of taking fresh ones. The projection itself is
23.6 MiB against a 1044 MiB tree (2.3%).

**And the member's peak does not move at all: 2215.4 / 2218.0 / 2215.4 MiB
before, 2215.2 / 2215.3 / 2215.3 after — 0.05%.** The graph/AST overlap was
simply never where the peak was.

**The retained figure also comes out slightly worse, and stayed that way.**
Only three runs per side made it visible: every *after* run sat above every
*before* run. One cause was a real bug in the change — the projection is a
local, so holding it to function exit kept its indexes alive past the graph
build, their only consumer; freeing it at the build's end moves the graph
build from *adding* ~24 MiB to *subtracting* ~27 (1302.5 / 1306.9 against a
1332.5 AST-parse level). But the steady-state figure survives that fix at
**~8-12 MiB above baseline** (1289.8 / 1295.2 vs 1275.9 / 1286.1 / 1287.6),
most likely the inverse of the pinning effect above: freeing the AST early
returns its arenas, and the graph then faults in fresh pages rather than
reusing ones the parse had dirtied.

**So on this library the change improves neither number a release fan-out's
per-member budget is sized from** — the peak is unchanged and steady-state
retention is marginally worse. It is kept for the mid-attach residency
(~175 MiB lower at the graph-build point) and because the projection is the
executable statement of what a pruned tree would have to preserve, not
because it reduced oneDAL's memory. It did not. Two method points worth
keeping: a single run per side would have read the regression as noise, and
"free it earlier" is not automatically "hold less" once the allocator is in
the picture.

**Where the peak actually is.** The attach's high-water mark occurs *inside*
`json.load`, before the graph exists: `dump.header_graph.clang_ast` ends at
1332 MiB, while `VmHWM` for the same window is 2215 MiB. The difference is
the JSON document itself, held as one `bytes`/`str` while the tree is built
from it. So the peak is `document + tree`, not `tree + graph`. Checked
rather than assumed that this is reducible by decoding more carefully: on a
247 MB AST, `json.load(fh)` and an explicit read/decode/`del raw`/`loads`
sequence both peak at **640.9 vs 641.0 MiB** — CPython already drops the
source buffer, and there is no stdlib spelling that avoids holding one full
copy of the document during the parse.

**What that leaves, with a number.** The remaining lever is the *tree*, not
the document: prune it during the parse to the keys the four readers touch
(`kind`, `inner`, `name`, `id`, `qualType`, `file`, `type`, `mangledName`,
`range`, `loc`, `referencedDecl`, `ownedTagDecl`, `bases`, … — a bounded,
auditable set). Measured ceiling on the same 247 MB AST: a recursive
key-whitelist copy is **224.9 MiB against 359.8 MiB, i.e. 62%**, so ~38% of
the tree is fields nothing reads. Applied to oneDAL's 1044 MiB tree that is
roughly 400 MiB off a 2215 MiB peak (~18%). Not attempted here: it must run
as an `object_pairs_hook`, which the existing streaming pruner measured at a
13-30% wall-time cost on *every* object in the document, and the gate for
`attach_ms` allows 50%; and a whitelist that is wrong in one key silently
drops edges rather than raising, which is exactly the failure mode
`tests/test_header_graph_ast_projection.py`'s differential invariant would
have to be extended to cover before it could be trusted.

**Shipped anyway, on its own merits, not as a memory fix:** the reordering is
strictly non-worse, evidence-identical (verified through the real `compare`
CLI on a real pair: every finding, section, verdict and exit code byte-equal
apart from one `extractor.duration_seconds` field), and it is the
precondition for any pruning work, since the projection is the thing that
defines what a pruned tree would have to preserve. The `attach_ms` gate is
unchanged by it (13.1s → 13.6s on the STL fixture, within run-to-run
spread). Do not cite it as having reduced oneDAL's memory; it did not.

**Now gated.** `check_header_graph_perf.py` measures `attach_peak_rss_mib`
and `attach_end_rss_mib` alongside the three time metrics, each in a fresh
subprocess over an STL-bearing fixture (the pre-existing fixture retains
0.1-2.8 MiB, far too little to gate on). The `performance.yml` PR-vs-base job
gates both. This closes the "currently ungated" clause of the entry above —
a change that holds one more copy of the AST costs no measurable time and
would have passed every gate that existed before.

One design note worth not relearning, because CI caught it and a unit test
now does: both memory metrics are **absolute** RSS, never a delta against
the pre-attach reading. `perf_measurement.is_gateable` is written for
wall-clock durations and rejects anything `<= 0`, while a retained-memory
delta is legitimately negative whenever the attach releases more than it
allocates — which is exactly what the clang backend does, since its AST
comes from the in-process memo the primary pass wrote. The first version
failed CI with `measured attach_retained_mib=-28.4 is not a gateable
value`, i.e. on a *better* result than the baseline's. A metric whose
domain does not match its gate's predicate cannot be gated at all.

### m1360 performance round: the asks not taken, and why (2026-09-24)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Lists the m1360 performance asks deliberately not taken, with reasons (scan rewrite, union vocabulary, loc strip, forked child needs an ADR). Kept so they are not re-derived.


The m1360 analysis (577d856a4 → ff29268fc, oneDAL castxml/clang legs) listed
ten upstream asks. Seven landed in the PR that added this entry: compacted
ASCII clang AST cache entries (`storage/json_compact.py`), AST-intake trace
boundaries plus a `VmHWM` field on every sample, the `/proc/*/stat` fallback
for `tree_processes`, the `find_by_value_types` leaf hoist, the single-name
demangle cache read, the template-names index shared with the defaults
builder, and the note that a castxml run still builds its header graph from
a clang AST (`docs/learn/graph-coverage.md`). The same PR fixed the
constants/typedefs dependency-scoping defect (`extract/
flat_map_dependency_scope.py`). The remaining asks, recorded so they are
not re-derived:

- **One stdlib-reference scan for both `exclude_export_only_roots`
  variants** (4 scans, 6-9 s). Not a pure memo: the two callers also differ
  in `committed_roots` and in full-scan vs early-exit, and the export-only
  exclusion changes which records the closure walk reaches, not only which
  seeds it starts from. Serving both from one scan needs a walk that carries
  two provenance labels per reached record through the typedef-alias
  provenance tiers — a rewrite of `_StdlibReferenceScan`, not a cache. The
  cheap special case (no `EXPORT_ONLY` declaration in the snapshot, so both
  seed sets coincide) does not apply to the measured library, which carries
  ~10k export-only functions.
- **"Share the 3 fixed reachability regexes across snapshots"** (8
  compiles, 4-5 s). The patterns are not fixed: `_StdlibReferenceScan`
  compiles each from the snapshot's own spelling vocabulary, and
  `VOCABULARY_CACHE` already serves a repeated vocabulary (the two flag
  variants of one snapshot hit it). The misses are OLD and NEW having
  different vocabularies. Sharing needs a comparison-scoped union
  vocabulary with per-snapshot filtering of matches. That is exact —
  `finditer_allow_nested` enumerates every valid candidate at every offset,
  so filtering a superset's matches to a subset vocabulary yields exactly
  the subset's matches — but it needs the scan to be constructed with both
  sides' vocabularies, which today it never sees. Worth doing together with
  the item above, since both restructure the same scan.
- **Keep-list strip of `loc`/`range` at store time** (another 64.8% of the
  compacted document). Not done: the header-graph call-graph pass reads
  `file`/`id` from those objects (`service_header_graph_attach`,
  `parse_clang_ast_calls`), and clang's location encoding is *sticky* (a
  `file` is stated once and inherited by following nodes;
  `extract/headers/clang/locations.py`), so dropping a subtree's `loc`
  silently re-attributes every later node. A correct strip must first
  materialise locations, which means parsing — not a streaming re-encode.
- **A derived form for the primary clang pass**, like the header-graph
  projection sidecar. The primary pass *is* the declaration extractor
  (`_ClangAstParser`'s eleven `parse_*` passes over id-keyed whole-document
  indexes), so its "derived form" is the snapshot itself, which the
  whole-snapshot disk cache already stores. A projection narrower than the
  AST but wider than the snapshot is the streaming-parse design (gap e),
  blocked on those parser indexes.
- **Confining each side's dump to a forked child** (measured −66% post-run
  residency at 45 roots, because the plateau is pymalloc arena
  fragmentation, not retention). A real design option — there is no
  process isolation anywhere in the tree today — but it changes how
  results, caches, memos and trace state cross a process boundary, and it
  needs its own ADR.

**Transition effect of the scoping fix.** A snapshot dumped after this
change drops dependency-header typedefs the kept declarations do not name
(482 libstdc++ internals on a small `std::vector`/`std::map` library). A
comparison against a baseline dumped *before* it lists them as
`typedef_removed`; under the default public-header scoping they are
filtered as non-public, stay visible in the disposition list, and do not
move the gate. Regenerate a stored baseline to remove the noise.

## Design notes and deliberate limitations

Deliberate rulings and scope limits, not defects. Changing one needs the decision the entry names (usually an ADR or maintainer ruling).

### `dump -H <dir>` changed which channel carries the directory when the ELF `dump` CLI migrated onto the shared typed executor — recorded, deliberately not reverted (ADR-063 Track 1, 2026-09-05)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** service_dump_pipeline.py:347 splits -H and passes directories as public_header_dirs, which enables provenance tagging. scope_header_dirs is now set only at the dumper.py level (dumper.py:411), as the entry says. The entry records this as deliberate convergence with compare, not a defect.

Found while retiring
`cli_dump_helpers.perform_elf_dump`: the one assertion in its test suite
that could not be rehomed, because the behaviour it pinned is no longer
the behaviour. `perform_elf_dump` split its `-H` list with
`header_utils.split_public_header_inputs` and routed the *directory* half
into `dumper.dump`'s `scope_header_dirs` — folded into the extraction
contract's scope, with ADR-015 declaration-provenance tagging
deliberately left off (`tests/test_dumper_contract_wiring.py::
test_scope_header_dirs_does_not_enable_provenance_tagging` still pins that
distinction at the `dumper` level, which is where it lives). The typed
request performs the identical split in `service_dump_pipeline.
resolve_dump_request` but passes the directory as a real
`public_header_dirs` entry, so a plain `dump -H include/` now *does* tag
provenance for everything under it. `scope_header_dirs` has no typed-request
field populating it at all any more — `api_types.py` says as much, where it
excludes the field from the `dump_manifest` mutual-exclusivity set.

**Why it stands.** This is convergence, not drift: `compare` has always
treated its own `-H` list this way, and `dump`'s own
`--public-header/--public-header-dir` pair — removed earlier precisely for
saying the same thing a second way — is what the new behaviour matches. The
original comment on `perform_elf_dump`'s fold gave "so a `dump --header
<dir>` baseline and a live `compare --header <dir>` candidate of the
identical header set agree on `scope_fingerprint`" as its whole purpose;
both commands now reach that agreement through one shared split rather than
two hand-maintained ones. Reverting would mean re-introducing a `dump`-only
channel and a `dump`-vs-`compare` provenance asymmetry to preserve a
behaviour whose own stated goal the convergence already serves.

**What is *not* established.** Nobody has measured whether the added
provenance tagging changes any real snapshot's classification for a
directory operand that contains genuinely private siblings — the analogous
hazard the `public_include_search_dirs` rule exists for (an auto-derived
umbrella directory holding a private sibling header). The difference is
that `-H <dir>` is an *explicit operand*, not an auto-derived one, and
declaring a directory public is exactly what typing it means; that is the
reasoning, not a measurement. If a real case turns up where it is wrong,
the fix is at the split (`split_public_header_inputs`' contract, shared by
both commands), not by restoring a per-command channel.
`tests/test_dump_cli_execution_behaviors.py::
test_dump_header_directory_reaches_the_extraction_scope` pins the current
channel with this reasoning attached, so the next reader finds the decision
rather than re-deriving it from a diff.

### Action pinning is deliberately partial, not a full sweep

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Still accurate. Elevated workflows (ci/publish/security/pages/agentready) and action.yml pin SHAs, while other workflows still use tags such as actions/checkout@v6 (grep of .github/workflows).

Third-party
GitHub Actions in `.github/workflows/agentready.yml`, `ci.yml` (the
`id-token: write` jobs), `pages.yml`, `publish.yml`, `security.yml`, and
`schedule-check-project-failure-path.yml` (its one `dispatch` job) are
pinned to a full commit SHA (with a `# <tag>` comment) rather than a
mutable tag/branch — those carry `security-events:write`,
`pull-requests:write`, `contents:write`, `actions:write`, or
`id-token:write` (OIDC/PyPI Trusted Publishing), so a re-pointed tag there
is a real supply-chain risk.
The root `action.yml` (the composite Action third-party repos consume
directly) is pinned the same way, for the same reason: its final step
conditionally runs `github/codeql-action/upload-sarif` under whatever
`security-events: write` permission the *consuming* workflow grants it, so
it carries the same blast radius as the elevated-permission workflows
above even though this repo's own CI doesn't invoke it with that scope.
Other workflows (`test-action.yml`, `eval-suite.yml`, `performance.yml`,
`realworld-validation.yml`, `dependency-review.yml`, and any future ones)
still use tags — deliberately deferred, since they only run with
`contents: read` and don't touch secrets/publishing/security-event write
access, so the blast radius of a compromised tag there is far smaller.
Extend the same pinning to a workflow only when it gains elevated
permissions, not preemptively.

### CODEOWNERS risk tiers currently all resolve to one person

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** .github/CODEOWNERS assigns all 19 rules to @napetrov.

The file is
structured by risk tier (CRITICAL/HIGH/STANDARD) so a second maintainer
can be slotted into CRITICAL/HIGH without restructuring, but there is
only one maintainer today — don't read the tiering as "these are reviewed
by different people," it isn't, yet.

### Toolchain-profile compiler-family rendering — audited, `args` trust boundary hardened; the `-stdlib=`/`--target=` "fix" itself was wrong and has been reverted

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Audit record: the wrong fix was reverted and the args trust boundary was hardened. _compose_gcc_options still exists (buildsource/run_plan_profile_fields.py:21-25).

An external audit found `run_plan.py`'s
`_compose_gcc_options()` composing `-stdlib=`/`--target=` unconditionally
for any `profiles.<id>.compile` overlay, even when
`compile.compiler_family: gcc` — both are Clang-driver-only spellings a
real GCC binary rejects (confirmed against GCC 14.2), so an early pass
dropped both whenever `compiler_family` resolved to a GCC family name. A
later review round found that fix backwards: the composed string this
function returns is **never actually fed to a literal GCC binary
anywhere in this pipeline** — `--ast-frontend` only has
`auto`/`castxml`/`clang`/`hybrid` (no `gcc`); castxml's own frontend is
always its internal bundled Clang (`--castxml-cc-<id>` selects an
*emulation* mode, not a literal execution path); and the direct-clang
backend's `_resolve_clang_bin` (`dumper_clang.py`) explicitly rejects a
`gcc-path` that isn't clang-family and falls back to host
`clang`/`clang++`. Since the real consumer is always Clang, dropping
`--target=` actively broke cross-compilation-target correctness for the
direct-clang backend — it was the *only* signal available there to steer
parsing away from the host architecture (no "probe the real compiler"
auto-discovery step exists on that path the way castxml has one), so a
GCC-family profile with an explicit `target:` would silently have its
headers parsed for the runner's architecture instead. Reverted:
`_compose_gcc_options()` emits `-stdlib=`/`--target=` unconditionally
again, same as before the original audit, with both the change and the
reasoning for reverting it recorded in the function's own docstring so a
future reader doesn't rediscover and re-"fix" the same false positive.
The same original audit flagged a real trust-boundary gap in
`profiles.<id>.compile.args`, which is unaffected by this revert and
stays fixed: the existing whitespace-
smuggling check (`_safe_profile_atom`) rejected one YAML scalar expanding
into multiple argv tokens, but not a single, whitespace-free dangerous
atom. `_DANGEROUS_ARG_PREFIXES` (`project_targets.py`) now blocks four
families of these: direct code-loading flags (`-Xclang`, `-load`,
`-fplugin=`, `-fpass-plugin=`), file/argv re-expansion (`@response-file`,
Clang's `--config`/`--config=`), driver command-line substitution
(`-specs=`/`--specs=`, `-wrapper`), and — added across two follow-up
review rounds on the same PR, since each is the same underlying
"opaque subprocess-forwarding" mechanism as the others — GCC's
`-Wa,`/`-Wp,`/`-Wl,` (comma-joined payload passed straight to the
assembler/preprocessor/linker; `-Wp,-fplugin=./evil.so` reaches cc1 the
same as a bare `-fplugin=`, `-Wl,-plugin=./evil.dso` loads an LTO linker
plugin) and Clang's `-Xpreprocessor`/`-Xassembler`/`-Xlinker`
(separate-argument equivalent of `-Xclang`). A third review round found a
deeper issue than another missing flag spelling: every `compile.*` atom
(not just `args`) now also rejects quote (`'`/`"`) and backslash (`\`)
characters, since `_compose_gcc_options` space-joins every field into one
string that `dumper.py`'s `--gcc-options` handling later re-splits with
`shlex.split()` — an atom like `"'-fplugin=./evil.so'"` starts with a
quote, not `-fplugin=`, so the prefix denylist alone accepted it, but
POSIX shlex quote-removal reconstitutes the exact blocked flag on
re-split (confirmed with an actual `shlex.split()` round-trip). Two more
review rounds each found a flag real for the mechanism it names but
empirically NOT exploitable through abicheck's actual pipeline —
verified rather than taken on faith, and blocked anyway since doing so
is free: `--castxml-cc-` (a second occurrence naively looks like it
could replace the trusted `--castxml-cc-<id> <path>` pair
`dumper_ast_config.py` composes ahead of `args`, but real castxml
0.6.3 hard-rejects any repeated `--castxml-cc-*` occurrence at
argv-parse time instead of silently substituting the compiler); and
`-B<dir>`/`-B <dir>` (GCC's compiler-component search path override
really does let a planted `cc1`/`cc1plus` run instead of the real one,
confirmed against real GCC — but every consumer of this composed
string is Clang, not GCC, and Clang re-execs itself via `-cc1` rather
than spawning a separate, `-B`-discoverable one; confirmed neither
castxml's internal bundled Clang nor the direct `--ast-frontend clang`
backend ran a planted `cc1` with `-B` set). A fifth review round found a
flag family that IS actually exploitable through this pipeline, unlike
the two immediately above: clang-cl's (Clang's MSVC-compatible driver
mode — reachable via a `compile.binding` whose path stem contains
"clang", e.g. `clang-cl`/`clang-cl.exe`, which
`dumper_clang._is_clang_family_binary` recognizes as clang-family)
`/clang:<arg>` escape hatch forwards an argument straight to the
underlying clang driver, bypassing clang-cl's MSVC-shaped option parsing
entirely — empirically confirmed exploitable: `clang
--driver-mode=cl "/clang:-fplugin=./evil.so" -c t.h` really does load and
run the planted plugin. `/link <options>` (clang-cl's documented
"forward options to the linker") is blocked alongside it on the same
LTO-linker-plugin grounds as the already-blocked `-Wl,`, without a
from-scratch empirical repro of that specific sub-case. A sixth review
round found a different shape of finding again: `-cc1`/`-cc1as`, Clang's
internal frontend mode, only activates when `-cc1`/`-cc1as` is literally
the *first* argument after the program name (confirmed empirically:
`-cc1` anywhere else is rejected as "unknown argument", including right
after a leading `-I`) — but `dumper.py`'s `_build_clang_header_command`
builds argv as `[cc_bin, *-I dirs, --sysroot, -nostdinc, *gcc_options
tokens, ...]`, so a scan with no `extra_includes`/`sysroot`/`nostdinc`
lets a leading `-cc1` in `compile.args` genuinely land in that
first-argument slot. Once in cc1 mode, `-load`/`-fpass-plugin=` were
already blocked, but cc1 mode exposes an entirely different, much larger
argument namespace this denylist was never designed to enumerate — Codex
found `-fcas-plugin-path` (a cc1-only flag not present in every Clang
build) doing the identical thing. Rejected the mode switch itself rather
than chasing individual cc1-only flags, the same reasoning as `--config`.
This denylist is necessarily reactive to the delivery *mechanism*, not exhaustive over
every dangerous flag a mechanism could carry — a real fix for the
whack-a-mole shape of this (an allowlist of known-safe ABI flags instead
of a denylist of known-dangerous ones) was suggested during review but
deliberately not done here: `args` is documented as a general escape
hatch for ABI-relevant flags this codebase cannot enumerate a priori
(GCC/Clang/MSVC each have their own vocabulary), and a strict allowlist
would need that vocabulary built out first — its own scoped project, not
a reactive expansion of this fix. (A fourth review round briefly caught a
correctness gap in a since-reverted sentinel the family-aware
`_compose_gcc_options()` fix needed — moot now that the fix itself is
reverted, see above; not detailed here since it no longer applies to any
code that ships.) Still **not** implemented, and out of
scope for that fix (each needs its own
scoped design, not a drive-by extension of the same narrow correction):
a real toolchain-identity probe that validates a resolved `binding`'s
actual compiler family/version/target against the profile's declared
constraints (`compiler_version` is still parsed but never checked against
anything); a profile-specific AST frontend (there is still only one
global `--ast-frontend`); and a genuine family-specific argv resolver —
in particular MSVC `/std:`/`/D` spellings, which this fix does not
attempt (no `compiler_family: msvc` caller/test exists yet to validate
against, and a wrong guess here is worse than the pre-existing gap).

### Deferred entirely, not attempted this pass

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Deferred-scope note. .devcontainer/ still does not exist (ls fails).

(heavier structural
changes, each needing its own scoped design rather than a drive-by
addition):
- *Devcontainer image* — a maintained `.devcontainer/` needs a decision on
  which system tools (castxml, libabigail, abi-compliance-checker,
  compilers) ship baked-in vs. installed on first use, and upkeep as those
  pins drift; `pixi` (see CONTRIBUTING.md) already solves the "one command
  gets you a working dev environment" problem this would target, without
  the image-maintenance burden.
- *Trend-reporting database* — persisting `scripts/check_tier_accuracy.py`
  /`check_fp_rate.py`/mutation-score history across runs (rather than each
  CI run only gating against a static baseline) needs a storage decision
  (artifact-based vs. external DB) and a retention/access policy before
  it's worth building.
- *Full behavioral baseline* — `skills-src/evaluation/agents/` (this pass, M1-5) is a real
  but minimal harness with one task; a "full behavioral baseline" implies
  a broad task suite plus a scoring/leaderboard story, which should grow
  from real usage of the one-task harness rather than being speculatively
  built out now.

### `run-plan.json`'s `gate` block is unprotected against an already-shipped, version-skewed reader — investigated end-to-end, confirmed structurally infeasible to close within the document's own shape, not fixed (Codex review, PR #779)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Records a structural infeasibility: run_plan.py:172 still uses the RUN_PLAN_SCHEMA_GATE discriminator, and old readers cannot be retrofitted.

`aggregate_manifest.py`'s `gate` block gained real
protection against an old `aggregate` binary misapplying a new manifest's
policy: `_check_manifest_version` already existed *before* this PR with
real MAJOR-rejection logic (`major > supported` raises), so bumping
`aggregate_manifest_version` from `1.0` to `2.0` gives an old, already-
installed reader a real, working rejection point — it already knows how to
say "too new for me." `run-plan.json`'s new `RUN_PLAN_SCHEMA_GATE`
(`abicheck.run-plan/v2`) discriminator was modeled on the identical
pattern but does **not** give the same protection, and cannot: traced the
full old (pre-PR779, commit `90e1813`, the last shipped state) `aggregate
--run-plan` pipeline directly. (1) Old `RunPlan.from_dict()`/
`RunPlanCheck.from_dict()` use exclusively total, defensive conversions
(`str()`, `bool()`, `isinstance`-guarded comprehensions, `.get()` with a
default for everything) — by design, matching this package's own
documented forward-compat convention ("every dataclass carries
`to_dict()`/`from_dict()` with defensive `.get()` parsing so a newer/
hand-edited pack never aborts a load" — `abicheck/buildsource/CLAUDE.md`).
Nothing in either function can raise regardless of input shape, so an
unrecognized `schema` string and an unknown `gate` key are both silently
ignored, not rejected. (2) Old `aggregate --run-plan` doesn't validate the
parsed plan directly at all — it projects it via `to_aggregate_manifest
(plan)`, which stamps `"aggregate_manifest_version": AGGREGATE_MANIFEST_
VERSION` using **the old binary's own hardcoded constant, imported fresh
at call time** — never read from or influenced by the run-plan.json's own
`schema` field. The one place a version-rejection check *does* fire
(`ExpectedTargets.from_manifest_data`) is therefore always checking the
old reader's own version against itself, and can never observe a
"too new" value regardless of what the source run-plan.json declares.
**Consequence:** a genuinely version-skewed setup — an already-installed
old `abicheck aggregate --run-plan` reading a `run-plan.json` a newer
`project plan --gate-missing-required`/`--gate-unexpected-target` wrote —
silently drops the requested gate policy and falls back to the old
binary's hard-coded `fail`/`include` defaults, exactly the "silently wrong
gate decision" class of bug the manifest-side `2.0` bump exists to
prevent, just unreachable to prevent here. **Not fixable within the
document's own shape**: every value in both dataclasses is consumed via a
total, defaulting conversion, so no crafted field value can force old,
already-shipped code to raise — this is a property of code that has
already been released and cannot be retroactively patched, not a
correctness gap in this PR's own new code. Closing it for real would need
a mechanism outside the JSON document itself (e.g. a CI-level convention
that `project plan` and `aggregate --run-plan` are always the same
abicheck version/pinned together, enforced or documented at the tooling
level) — out of scope for a schema-shape fix and not attempted here.

### `DEFAULT_SYSTEM_PROVIDERS` (`bundle_models.py`) is a hand-maintained soname allow-list, not a real topology model for bundle-level system-provider classification -- a tactical fix that has grown, not a designed feature (Codex review on #791, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** bundle_models.py:58 DEFAULT_SYSTEM_PROVIDERS is still a hand-maintained frozenset allow-list, a known tactical design limitation.


`bundle.compare_bundle()`'s unresolved-import check
(`ChangeKind.BUNDLE_INTRA_DEP_REMOVED`, `_detect_intra_dep_removed()`)
does not exempt an import merely for being "against any" soname in this
union -- it suppresses a consumer's finding only when that consumer's
remaining non-intra `DT_NEEDED` edges are all non-empty AND every one of
them matches `set(DEFAULT_SYSTEM_PROVIDERS) | set(bundle_system_providers)`
(or the `_looks_system` heuristic), AND EITHER no in-bundle sibling
version-compatibly provided the symbol and was reachable from *this*
consumer OR the user explicitly named one of the remaining sonames via
`explicit_providers` -- `ever_provided_in_bundle` is `compatible_old &
_old_reachable(consumer.library)`, scoped to what this specific consumer
could reach, not "any" in-bundle sibling regardless of reachability; an
unreachable sibling exporting the symbol does not, by itself, block the
allow-list suppression (Codex review, PR #910, fresh evidence, correcting
this exact paragraph's own prior "no in-bundle sibling ever" wording). A
consumer with a mixed intra-bundle/external dependency set can still
produce the finding, and a match against the allow-list alone is not
sufficient. Correct for the ordinary libc/libstdc++/libpthread
runtime set the
constant started from, but each addition since (oneTBB's `libtbb.so.*`
and its allocator-proxy libs, oneMKL/Intel-runtime and Level Zero
entries, PR #883) was a real, reported false positive fixed by naming one
more vendor runtime rather than by asking what "system-provided" actually
means for a bundle -- and a growing vendor runtime not yet on the list
still reproduces the exact false positive it exists to prevent. **Already
designed, not a fresh gap to sketch a fix for here**: `docs/contribute/
plans/g42-check-identity-environments-and-provider-resolution.md` names
this exact limitation as one of its own three motivating problems and
lays out the real fix ("Environment-aware system-provider resolution") --
resolving each external dependency edge against a declared environment's
sysroot/runtime matrix (presence, SONAME, export, symbol version, runtime
floor), with the static allowlist demoted to a fallback for a project
that doesn't opt into a declared environment. Route any work on this to
that plan rather than reinventing a provider-classes registry here.
**This gap is bundle-wide, not just `compare_bundle()`'s.** `audit_bundle()`
(`scan --artifact-set`'s one-sided entry point) unions the identical
`set(DEFAULT_SYSTEM_PROVIDERS) | set(bundle_system_providers)` allow-list
and feeds it to its own predicate, `_detect_unresolved_intra_dependency()`
-- not a call into `_detect_intra_dep_removed()`, since an audit has no old
side to diff against. That predicate's own docstring documents three real
differences from the diff-driven one (version-aware and reachability-
constrained provider matching, a suppression path that additionally
requires the consumer have zero intra-bundle `DT_NEEDED` edges, and a
`COMPATIBLE_WITH_RISK`-not-`BREAKING` verdict), so a G42 fix landing only
on the `compare_bundle()` path would leave `scan --artifact-set` audit-mode
classification on the static allow-list with its own, differently-shaped
false-positive exposure (Codex review, PR #910, fresh evidence). Any work
on this gap needs both call sites in scope.

### The composite Action's single `--write` slot can leave its unconditional coverage/assurance/severity floors without a structured report

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The SARIF/text fallback part is superseded. The remaining non-JSON --write blindness is an explicitly accepted limitation per action/AGENTS.md 'Known, accepted limitation'.


**Superseded** (ADR-063 Phase 6, Track T8): the SARIF-fallback/HTML-gap
account this entry used to carry is retired along with the rendered-prose
readers it described. `_report_compat_verdict`/`_severity_gate_exit`/
`_coverage_gated`/`_assurance_gated` no longer have a "rendered markdown/
text" fallback tier at all (`_text_report_content` is deleted), nor a
SARIF `runs[0].properties.abiVerdict` branch — every one of them is
JSON-only now (`run_outcome`, then the legacy per-field JSON, then
nothing), per `action/AGENTS.md`'s "How `run.sh` resolves the verdict it
publishes". A `format: html` primary was never coherent evidence for this
group of readers, so its own absence is no longer a special case either.

What remains, narrower than before: the CLI's `--write` option is still
single-valued (`secondary_output.py`'s `--write FORMAT=PATH`, not
`multiple=True`), and `scan`/`compare`'s own internal `--write
json=$PR_JSON` sidecar injection (unconditional since Track T8 — no longer
gated on `pr-comment`, closing the gap this same track's own Codex review
found in the `pr-comment: false` case) is still skipped whenever the
step's own `extra-args` already supplies a `--write` of any format
(`_extra_args_has_write_flag`), since `extra-args` is appended after the
injection and Click keeps only the last occurrence of a repeated
non-multiple option — a second, JSON-targeted `--write` this script
appended would always lose to the user's own later one and never execute.
When that user-supplied `--write` targets `json=`, `_extra_args_write_json_
path` already recovers it, so `_json_report_src` still finds structured
evidence. When it targets a **non-JSON** format (`-o text=...`,
`-o markdown=...`, ...), there is no JSON anywhere, and ADR-049's
unconditional contract-coverage/analysis-assurance floors plus the
severity-category gate genuinely go blind for that one run — accepted, not
fixed, per `action/AGENTS.md`'s own "Known, accepted limitation"
paragraph, since closing it would mean either silently discarding the
user's explicit `--write` choice or unconditionally re-running the
analysis a second time purely to obtain JSON (a materially larger,
separate trade-off `_maybe_post_pr_comment` only accepts today for the
sticky-comment feature, under its own narrower guards). If this becomes a
real reported problem rather than a review-found edge case, the honest fix
is the same one this entry always named: make `--write` accept multiple
`FORMAT=PATH` operands (a real CLI capability change touching
`secondary_output.py` and every command that declares the option, not
just this Action script) — not another per-reader special case.

### Behavioral regressions behind an unchanged declaration

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** case208 ground truth records behavioral_break with truth_scope declared-interface and no detector expected. An L4 definition/declaration restrict replay is an unimplemented enhancement, not a regression.


Catalog case208 (restrict added to a function *definition* only) breaks a
valid overlapping consumer at runtime under GCC and Clang, while the
declared interface — everything a binary/header comparison reads — is
identical. Ground truth records it as `behavioral_break: true` with
`truth_scope: declared-interface`; no detector is expected to infer it.
An L4 source-ABI replay *could* see `restrict` on the definition's
parameters and report a definition/declaration contract mismatch; that is
not implemented.

### A member that fails to extract exits `4` from a directory `compare` but `1` from a single-pair one

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The entry records a maintainer ruling 'keep both' (exit 1 scalar vs 4 release for an extraction failure); both are documented in docs/reference/exit-codes.md, and changing either needs an ADR-064 amendment.


**Recorded, not fixed (lane A, A2(b), 2026-10-09; maintainer ruling: keep both).**
A single-pair `compare` whose operand cannot be dumped (for example a
truncated ELF) aborts with a CLI error and exits `1`. The same pair as the only
member of a directory/package release produces a member `ERROR` entry and the
release exits `4` with `exit.reasons: ["operational_error"]`. Both behaviours are
documented in `docs/reference/exit-codes.md`. Reproduce: put the same corrupt
`libm0.so` in `old/` and `new/`, then run `compare old/libm0.so new/libm0.so`
(exit `1`) and `compare old new` (exit `4`).

This is the one axis where a release of one member and a scalar `compare` of that
member disagree. The release fold takes each member's compatibility
contribution from the scalar resolver (`resolve_compare_exit_decision`, stamped
by `cli_compare_release_pairwise._member_exit_decision`), and
`tests/test_compare_cardinality_invariance.py` pins the agreement on every axis
where a comparison completed. A member that never compared has no scalar
decision to fold, so it falls to the release's own operational-error axis.
Making the two agree means changing a public exit code in one direction: either
a release exits `1` for a failed member, or a scalar dump failure exits `4` and
reads as an ABI break. Either change needs an ADR-064 amendment.

### ADR-071's release assurance fold: what it deliberately does not do

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The entry records deliberate ADR-071 scope exclusions (per-library override, per-member block, fold convergence). Items 4-5 (no exit block on the stored BundleFacts JSON; depth not projected onto the stored/live driver) are residual defects described as open.


ADR-071 gave `assurance.require_complete` real semantics for a
directory/package (release) `compare` and for a stored `BundleFacts` operand,
and retired the four guards that existed only to stay ahead of the missing
semantics. Three adjacent things were in scope to consider and deliberately
left out, each for a stated reason rather than for effort:

1. **No per-library `assurance.require_complete` override.** The setting stays
   project-wide (ADR-071 D4), like `gate.fail_on_removed_library` and the
   `release.*` keys. A per-member override is a real config-surface question
   (where does it live — a `release.libraries.<key>` block? a selector? — and
   how does it interact with the D7 precedence resolver), not a fold detail,
   and nothing in the reported gap asked for one.
2. **No per-member `analysis_assurance` *block* in the release report.** Each
   `libraries[]` entry carries its own `analysis_assurance_status` and the
   fold names the short members and their notes, but the full per-pair block
   (target/TU/export accounting, the five context statuses, layout-unverified
   detectors) is not embedded per member. Doing so would multiply a
   substantial sub-object across every member of every release document — a
   report-schema sizing decision of its own. A member that needs the full
   block is reachable today by comparing that library individually, and the
   fold's notes name which member to compare.
3. **The three orthogonal `0`/`1` release axes still fold in three places.**
   ADR-049's contract-coverage floor, ADR-065 D6's incomplete-scope floor and
   ADR-071's assurance floor now have the same shape — resolve a decision per
   run, `max` it across members, carry it to the exit and the report — and
   each has its own resolver, its own `*_terms` projection and its own
   parameter threaded through `_format_release_summary`/
   `_finalize_release_output`/`_exit_compare_release`. That is three near-
   identical threadings, and the `no_growth` baseline bumps ADR-071 needed in
   five files are the visible cost of adding the third. The convergence target
   is the duplication-and-convergence plan's own P0
   `EffectiveGate`/`EffectiveEvaluationConfig` work — one object carrying every
   orthogonal axis's decision, threaded once — not a fourth copy of the
   pattern. **Do not add a fourth `0`/`1` release axis by copying this one;**
   land that convergence first.

4. **The stored-`BundleFacts` operand's JSON carries no `exit` block.** It
   folds the axis, exits on it, and — since a Codex P1 on this PR — publishes
   the canonical top-level `analysis_assurance_exit_contribution` and the fold
   section in its own report and in every `--output-dir` file, so the gate is
   no longer lost to `aggregate` or a deferred gate. What it still lacks is an
   `exit` block of its own, unlike the live release document: a consumer
   reading `exit.code` from this driver's JSON finds nothing, and has to read
   the verdict and the individual axis keys instead.

   An earlier revision of this entry used that missing `exit` block to excuse
   omitting the fold entirely, on the grounds that the section had "nowhere to
   hang". That was wrong about the half that mattered: the gate-bearing key is
   a plain top-level scalar and needs no `exit` block at all, which is exactly
   why the omission was a real gate bypass rather than a cosmetic gap. Giving
   that driver a real `exit` block remains its own change, and the right one —
   it would close several adjacent asymmetries at once.

5. **`--depth binary` is not projected onto a stored-`BundleFacts` OLD side,
   so that operand is stricter than a live one.** `compare_bundle_facts.
   dispatch` threads `depth=` into the **stored/stored** driver
   (`workflows.bundle_stored_pair_compare.compare_stored_bundle_facts_pair`)
   but not into the **stored/live** one
   (`bundle_side_input.compare_release_against_bundle_facts`, which has no
   depth parameter at all). At `--depth binary` it clears NEW's headers while
   the stored OLD snapshot keeps whatever L2/L5 facts its capture recorded, so
   the comparison is asymmetric in a way live-vs-live at the same depth is not.

   Measured on a three-library fixture, same pair both ways: live-vs-live at
   `--depth binary` reports `analysis_assurance: partial` with one note
   (`scope_resolved is False`), while stored-vs-live reports `partial` with
   three (`header context asymmetric: the new side carries no header/API-level
   evidence`, `graph completeness unknown`, `contract_coverage is 'partial'`).

   ADR-071 did not cause the asymmetry — it pre-dates this axis and affects
   *findings* too, not only assurance — but it is what makes the asymmetry
   gate, so the divergence is worth stating plainly: under
   `assurance.require_complete` a stored-OLD release at `--depth binary` can be
   floored where the live equivalent is not. The `partial` is **not** a false
   finding: the evidence really was asymmetric, and `AGENTS.md`'s "weaker
   evidence narrows conclusions" says to report that rather than hide it. What
   is wrong is only that the user asked for binary depth and the stored side
   did not honour it.

   The fix is to give that driver a real `depth` and project both sides to it
   before comparing — which moves findings, not just assurance, and so belongs
   with the stored/live driver's own evidence handling rather than bolted onto
   this axis. Until then, compare a stored OLD side at its captured depth (omit
   `--depth`), or use a live OLD tree when you need `--depth binary` to be
   symmetric. Found by Codex review on PR #1237.

The companion test gap is recorded in `tests/regressions/manifest.py` under
bug class `gate.per_member_axis_fold`: the order-independence and monotonicity
properties are stated and generated for the assurance axis only. The coverage
and scope axes have example-level tests but no property suite of their own, so
a regression in *their* fold would not be caught by it. Generalizing one
property harness over all three axes is the real remaining work there, and it
pairs naturally with the convergence above.

### The compatibility percentage counts findings against symbols

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The formula is unchanged at report_summary.py:235 `(old_symbol_count - breaking_count) / old_symbol_count * 100`. The entry records a deliberate 'not fixed' decision (ABICC heuristic, public contract), documented in the CompatibilityMetrics docstring.


`report_summary.compatibility_metrics` computes
`binary_compatibility_pct = (old_symbol_count - breaking_count) / old_symbol_count * 100`,
where `breaking_count` is a count of **findings** whose effective verdict is
`BREAKING` and `old_symbol_count` is a count of the old side's **exported
symbols**. The two quantities are not the same unit, so the percentage is
not what it reads as. A library with ten exports and two breaking findings
about one symbol reports 80% binary compatibility while nine of its ten
symbols are untouched and one is broken — verified with a synthetic probe.

When `old_symbol_count` is unknown the numerator is divided by
`len(changes)` instead — a third unit — and `affected_pct` degrades to
`0.0`, which is *missing denominator information* rather than a claim that
nothing is affected. A consumer must not reconstruct a symbol count from
either percentage: they are lossy in both the rounding and the unit.

**Not fixed, deliberately.** It is an ABICC-compatible heuristic, it is part
of the JSON report's public data contract, and redesigning it is a change to
the shared semantic owner (`report_summary.py`) with consistent behaviour
required across JSON, HTML and Markdown — not something a reporting-surface
change may do for one format. What the PR-comment work *did* do is refuse to
propagate it: the comment states explicit counts (breaking / needs review /
safe, plus the entity-by-operation rollup in `report/change_summary.py`) and
carries no percentage at all, so it cannot be read as a confidence score or
as "N% of symbols are compatible". The semantics, the limitation and this
prohibition are recorded in `CompatibilityMetrics`' own docstring, next to
the formula.

### Local-exec TLS in an AArch64 shared object leaves no binary evidence

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Records a toolchain limitation: local-exec TLS in an AArch64 shared object leaves no binary evidence. tests/test_elf_static_tls.py asserts the fact stays False.


`has_static_tls` is read from `DF_STATIC_TLS` *or* a TP-relative dynamic
relocation (`extract/elf_static_tls.py`), which covers initial-exec on every
architecture, including AArch64, where GNU ld writes no `DF_STATIC_TLS`. One
case stays invisible: `-ftls-model=local-exec` in a **shared object** on
AArch64. GNU ld (binutils 2.42) links it with the TP offset fixed at link time,
emitting neither a TLS dynamic relocation nor the flag, so nothing in the
binary records the requirement (`tests/test_elf_static_tls.py` asserts the
fact stays `False` there rather than fabricating a positive). Local-exec is
only valid for executables, so this is toolchain misuse rather than a
supported configuration. Closing it would need instruction-level evidence
(the `tprel` relocations in the object files, i.e. L3 build evidence).
