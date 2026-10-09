---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# Known gaps — closed and moot

History for [Known gaps](../known-gaps.md): entries confirmed fixed, and
entries whose defect lived only in code that has since been deleted (the
`scan` family removed by ADR-068 Phase 6). Each entry carries its
re-verification status line (2026-10-09, `456989f`). Do not re-open an
entry from here without reproducing it on current `main`; record a new
gap on the main page instead.

### `--build-target` silently does nothing when combined with a pre-captured Bazel `--build-info` (an `aquery`/`cquery` jsonproto), on both `dump` and `scan` — investigated, not fixed (Codex review, fresh evidence, P0.2 follow-up). Fixed (ADR-063 Phase 4, "option 2" below): the combination now raises a clean usage error instead of silently collecting an unscoped graph

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry says it was fixed (ADR-063 Phase 4), and the dump --build-target flag and scan were later removed. Only removal comments are left: cli_buildsource.py:335 and frontends/cli/options/rulings.py:452. The scoping check lives in workflows.plan.bazel_target_scoping_failure.

**Status note, added once this was closed:** `scan` (including
every `scan`-specific mechanism this entry narrates —
`scan_bazel_scoping_failure`, `ScanRequest`, `cli_scan.py`) was later
removed outright (ADR-068 Phase 6), and `dump`'s own `--build-target` CLI
flag was removed after it (once `scan`'s removal resolved the routing
hazard that had deferred it). `.abicheck.yml`'s `build.targets` is now the
*only* front-end-reachable source of root-target scoping for `dump`/
`compare`; the historical narrative below (written while both the flag and
`scan` still existed) is kept for the reasoning it records about the
underlying `bazel_target_scoping_failure`/`_discovered_config_build_targets`
mechanism, which is unchanged. `abicheck.workflows.plan.bazel_target_scoping_failure()` is the
one check both `dump`/`compare` (via `AnalysisPlanner`, wired into
`service_dump_pipeline.resolve_dump_request`/`service_compare_pipeline.
resolve_compare_request`) and `scan --against`'s own candidate resolution
(which has no `CompareRequest`/`DumpRequest` of its own to resolve through
`AnalysisPlanner` and so calls the same free function directly) now run
before collecting anything — `dump`/`compare`/`scan` alike raise
`PlanningError` (framework-neutral) from the engine layer, translated to
`click.UsageError` at each CLI boundary (`cli_resolve.py`/
`cli_buildsource.py` for `dump`/`compare`; `cli_scan.py`'s own `scan_cmd`,
around its `run_scan_core` call, for `scan`). `scan_engine._build_new_
snapshot` originally raised `click.UsageError` directly instead — cheaper
in lines, but it leaked a Click-specific exception type out of the same
engine function the typed `run_scan(ScanRequest(...))` API calls, with no
Click context to catch it in (a third Codex review round, fresh evidence).
Fixed by raising `PlanningError` there instead and adding the translation
at `cli_scan.py`'s boundary, matching the `dump`/`compare` pattern. **A
fourth Codex review round found that placement itself was still wrong**:
`_build_new_snapshot` runs *after* `run_scan_core`'s own S3 pattern-scan and
points-of-interest work, so a typed `run_scan()`/`run_scan_subprocess()`
caller — which has no `cli_scan.py` pre-flight ahead of `run_scan_core` the
way the CLI does — paid for that (cheap but real) work before the
rejection fired. Fixed by moving the check to the very top of
`run_scan_core`, guarded on the same `collection_for_ci_mode(collect_mode)`
emptiness `workflows.plan._check_bazel_target_scoping` already uses for its
`depth=binary` exemption, and removing it from `_build_new_snapshot`
entirely rather than leaving a second, independently-maintained copy —
which is exactly what the removed copy was: **testing the move found the
old `_build_new_snapshot`-only check had no `depth=binary` exemption at
all**, a real, separate bug the earlier "scan's own path was already
immune" note only verified for the CLI (whose `_normalize_depth_inputs`
prunes `build_info` to `None` at that depth) and never checked for the
typed API (`service_scan.run_scan` does not prune it). `tests/
test_bazel_root_targets_scan.py`'s `test_run_scan_typed_api_raises_
planning_error_not_click_usage_error` (raises `PlanningError`, not
`click.UsageError`), `test_run_scan_rejects_before_wasted_pattern_scan_
and_poi_work` (fires before the S3 pass, monkeypatch-verified), and
`test_run_scan_depth_binary_exempts_the_early_bazel_scoping_check` (the
exemption holds at the new call site) pin all three properties together.
All three CLIs still name the mismatch and the documented workaround, exit
64, not a silent, unscoped collection. **The same fourth round also found
`scan --artifact-set`'s own pre-flight check (`cli_scan.py`'s
`_run_artifact_set`) had never had the `depth=binary` exemption at all** —
unlike the single-binary path, whose `_normalize_depth_inputs` prunes
`build_info` to `None` at that depth before its own copy of the check runs,
`_run_artifact_set` checked the raw, unpruned inputs directly. Fixed by
adding the same `(depth or "").lower() != "binary"` guard used elsewhere;
`tests/test_bazel_root_targets_scan.py::
test_scan_cli_artifact_set_depth_binary_exempts_the_bazel_scoping_check`
pins it.
**A second Codex review round found this only covered `InputSpec.
build_targets` (the `--build-target` CLI flag/typed-API field) — a root-
target scope declared in `.abicheck.yml`'s own `build.targets:` instead
reaches `BuildConfig.targets` through `embed_build_source`'s own CLI-
overrides-config merge, a value none of the request-level pre-flight
checks above can see (no config is discovered yet at that point).** Fixed
at the one place both sources are already unified into a single value:
`buildsource.inline._maybe_collect_bazel_build_info()` itself, which every
`embed_build_source` caller (`dump`/`compare`/`scan` alike) goes through —
it now raises `ValidationError` when given a non-empty `configured_targets`
and a pre-captured jsonproto. Note this closes the *silent* half
everywhere, but the *exit code* still varies by front end because of a
pre-existing, deliberate design choice one layer up:
`workflows.artifact.execute.embed_side_build_source` (the shared
primitive `dump`'s typed request, `compare`, and `scan` all resolve
through) already flattens any `ValidationError` from `embed_build_source`
into `SnapshotError` ("this surface has always flattened both onto
SnapshotError" — see that function's own comment), so those three paths
now fail loudly with a correctly-typed error at exit 1, not exit 64;
only the `dump --sources <tree>` (no `SO_PATH`) source-only path, which
calls `cli_buildsource.embed_build_source`'s own adapter directly and
was never routed through that flattening primitive, gets the full exit
64. Changing the shared primitive's flattening behavior to recover
exit-64 uniformly is a separate, riskier change (that comment's own
"has always" — other `ValidationError`s reaching it likely already rely
on the flattened exit 1) and was not attempted here. See
`abicheck/workflows/plan.py`'s own module docstring and
`docs/contribute/plans/one-semantic-pipeline.md`'s Phase 4 "Landed"/
"Still not landed" notes (ADR-063 itself no longer carries a per-phase
status entry — its duplicated status block was removed by PR 0,
2026-09-02) for what changed and what this fix deliberately does not
attempt (option 1 below, teaching the adapter to filter an already-parsed
graph, remains unimplemented).
**A third Codex review round found a remaining dry-run/execution parity
gap for this same `.abicheck.yml` case, investigated and deliberately left
open rather than fixed reactively.** `dump --dry-run` (`cli_dump_helpers.
render_dump_dry_run`) never calls `collect_inline_pack`/
`_maybe_collect_bazel_build_info` at all — a dry-run resolves and renders
purely from `resolve_dump_request`'s `ResolvedDumpRequest`, which stops
well before `execute_dump_request` ever reaches `embed_build_source`. So
when the root-target scope comes *only* from `.abicheck.yml`'s
`build.targets:` (no `--build-target` flag, the case the fix above
covers), the dry-run preview reports success for a request the real run
then rejects — the same shape of parity gap the earlier `scan --dry-run`/
`scan --artifact-set --dry-run` fix (above, in the first Codex round)
closed for the CLI-flag case, just not yet closed for the config-sourced
one, and not yet checked on `compare --dry-run`/`scan --dry-run` either
(neither discovers `.abicheck.yml`'s build config during their own dry-run
preview today).
**Fixed in a later session, closing this gap for `dump`/`compare`/`scan`
alike.** Neither of the two designs originally floated here turned out to
be necessary in the form stated: `AnalysisPlanner`'s own `SidePlan`
already carries `sources` (exactly what auto-discovery needs), and
`scan`'s `ScanRequest` already carries both `sources` *and*
`build_config` (the explicit `--config` override — a seam `dump`/
`compare` don't have at the request level at all, so only their
auto-discovery half closes). `workflows.plan.bazel_target_scoping_failure`/
`scan_bazel_scoping_failure` gained two new, defaulted keyword parameters
(`sources`, `build_config`): when the request's own `build_targets` is
empty, the check now falls back to whatever an explicit `build_config` or
an auto-discovered `.abicheck.yml` at `sources` declares under
`build.targets:` (`_discovered_config_build_targets`), reproducing
`embed_build_source`'s own `targets=list(build_targets) if build_targets
else cfg.targets` precedence exactly — never running the P0.3
compile-context fold itself, just a pure, deterministic config read, so
it fits `AnalysisPlanner`'s own side-effect-free constraint. A malformed
`.abicheck.yml` degrades to "no config found" here (`except ValueError:
return ()`) rather than raising a second, independently-worded error,
since `embed_build_source` already raises a correctly-typed
`ValidationError` for it at real-execution time. Every existing caller
keeps passing neither parameter, so this is additive. `dump`/`compare`'s
`--dry-run` resolve through the identical `resolve_dump_request`/
`resolve_compare_request` chokepoint the real run does, so widening the
check there closed their dry-run parity for free, with **no change to
either renderer**; three of `scan`'s four pre-flight call sites
(`scan_engine.run_scan_core` and both of `cli_scan.py`'s direct call
sites — the single-binary pre-flight and `_run_artifact_set`'s own, each
already running ahead of both their real-run and `--dry-run` branches)
were each updated to forward their own already-in-scope
`sources`/`build_config` locals. **The fourth, `service_scan.
run_scan_set`, was deliberately left unwidened for one session**:
`service_scan.py` sat exactly at the AI-readiness 2000-line hard cap, and
the widened call didn't fit `ruff format`'s column budget on one line —
the resulting explosion would have pushed the file over. Adding it to
`LARGE_FILE_ALLOWLIST` was rejected (that allowlist is reserved for
pre-existing `scripts:`/`tests/` debt, not a fresh production-file
exemption for an unrelated fix); trimming unrelated content in that
already-densely-reviewed file to buy back the budget was rejected too. So
this one call site kept its pre-fix behavior for a while: a direct
`run_scan_set(ScanRequest(...))` typed-API call with no CLI in front of
it wouldn't see the `.abicheck.yml`-only scope pre-flight (it still
failed, just later and less cleanly, inside real embedding) — `scan
--artifact-set`'s own CLI path was unaffected throughout, since
`cli_scan._run_artifact_set`'s pre-flight (already widened) already ran
ahead of `run_scan_set` and caught the mismatch first.
**Closed in a later session, by splitting `service_scan.py` rather than
raising its baseline.** `_descendant_pgids`/`_kill_process_tree` — the
process-group kill machinery behind `run_scan_subprocess`/
`run_scan_set_subprocess`, with zero dependency on anything scan-specific
— moved to the new `abicheck/workflows/scan_subprocess.py`, re-exported
from `service_scan.py` for backward compatibility (mirroring
`cxx20_pair_dialect.py`'s own precedent: a genuine one-directional edge,
since the worker/harness functions that actually call back into
`run_scan`/`run_scan_set` stayed put, avoiding the mutual-dependency
shape a full move of the subprocess harness would have created). That
bought back the room `run_scan_set`'s own `scan_bazel_scoping_failure`
call needed to forward `sources=`/`build_config=` too, landing at a new,
lowered `architecture/debt.yaml` baseline (2000 → 1933) rather than
exactly at the hard cap. See
`docs/contribute/plans/one-semantic-pipeline.md`'s Phase 4 "Landed"
notes ("Second slice" / "Third slice" — ADR-063 itself no longer carries
a per-phase status entry, its duplicated status block having been
removed by PR 0, 2026-09-02) for the full accounting, and
`tests/test_analysis_plan.py::TestBazelBuildTargetScoping`/`tests/
test_bazel_root_targets.py::test_dot_abicheck_yml_build_targets_dry_run_parity`/
`tests/test_bazel_root_targets_scan.py::
test_run_scan_depth_headers_config_sourced_target_scope_raises_planning_error`/
`tests/test_bazel_root_targets_scan.py::
test_run_scan_set_config_sourced_target_scope_raises_planning_error`
for the regression coverage (the third-to-last of these also replaces this
paragraph's earlier pinning of the *pre-fix* `click.ClickException` leak
from the typed `run_scan()` API with the now-clean `PlanningError`,
raised earlier too, from `run_scan_core`'s own pre-flight check rather
than leaking out of `_build_new_snapshot`'s pre-existing `except
AbicheckError` wart — that wart itself is unrelated and stays open, see
this entry's earlier notes on it). **Phase 4 is now complete: all four
pre-flight call sites forward `sources=`/`build_config=`.**
**A later Codex/CodeRabbit review round found the `.abicheck.yml` fix
itself had a second, distinct gap — not deferrable the way the dry-run
parity gap above was, since this one had a direct, bounded fix.** At
`--depth headers`, `embed_build_source`'s own real check never runs at all
(that depth's `collect_mode` is `"off"`, the identical condition the
`depth=binary` exemption elsewhere in this entry relies on) — but
`buildsource.l2_seed`'s three L2-seed/compile-context helpers
(`derive_l2_include_dirs`, `derive_l2_compile_context`,
`seed_includes_and_fold_compile_context`) each run their *own*,
independent `collect_inline_pack` call regardless of `collect_mode` (they
exist to seed useful `-I` dirs and fold build context into the header
parse even for a headers-only scan), each wrapped in a broad best-effort
`except Exception` that swallows any failure and degrades to "no seeded
context" — by design, documented in `_l2_seed_config`'s own docstring ("a
malformed/invalid config surfaces loudly elsewhere ... this is a
best-effort include-dir hint"). That documented assumption is false for
this one input: at `--depth headers`, this L2-seed call is the *only*
place the Bazel-scoping `ValidationError` can fire at all, so the broad
catch silently swallowed it, with the run proceeding with no diagnostic
and without the build-derived context it should have used. Fixed by
extending the file's own pre-existing carve-out for
`HeaderCompileContextAmbiguousError` (raised for the identical reason — a
deliberate, fail-closed error is not "best-effort collection failed") to
cover `ValidationError` too, in all three helpers. `tests/
test_bazel_root_targets_l2_seed.py::
test_seed_includes_and_fold_compile_context_raises_on_bazel_scoping_mismatch`
reproduces it end-to-end with a real (unmocked) pre-captured jsonproto and
a `.abicheck.yml`-only (no `--build-target`) target scope, pinning that it
now raises instead of degrading silently. **Verified against `scan`
specifically (a sixth review round asked for it by name, for the typed
`run_scan(ScanRequest(depth="headers", ...))` shape)**: the silent
`COMPATIBLE`/exit-0 outcome is gone — the request now fails loudly with the
same clear diagnostic — but the exit code inherits the identical front-end
variance already noted above for the sibling `embed_build_source` case, via
a different, independently pre-existing mechanism: `scan_engine.
_build_new_snapshot`'s own `except AbicheckError: raise click.
ClickException(...)` (documented in this module's own header as a
pre-existing wart predating ADR-063 entirely, deliberately left alone) maps
the `ValidationError` to exit 1 for the CLI, and — since `click.
ClickException` is not itself caught anywhere further out — leaks that same
Click-specific exception type to a typed `run_scan()` caller too. Fixing
that leak means touching the same pre-existing, out-of-scope `except
AbicheckError` clause the module docstring already flags for a future
cleanup, not a new regression this phase introduced — left alone here for
the same reason.
**A seventh review round found that "verified" claim itself rested on a
false premise, and had a real, bounded fix — unlike the sibling
`click.ClickException` leak just above.** `run_scan_core`'s own early
pre-flight check (added for the "run scan planning before pattern and POI
extraction" fix elsewhere in this entry) exempted `--depth headers` the
same way it exempts `--depth binary`, on the theory that both resolve to a
`collect_mode` with no collection layers. That theory is only half
right: `--depth binary` *also* clears the header list to empty
(`service_scan.run_scan`'s `eff_headers = [] if eff_depth is
EvidenceDepth.BINARY else ...`, mirrored by the CLI's own
`_normalize_depth_inputs`), which is the actual reason `build_info` is
never consulted anywhere for that depth — `--depth headers` keeps real
headers, so the L2-seed's own independent `build_info`-consuming pass
(the one this whole round's fix concerns) still runs, and the early check
was wrongly skipping it. Concretely: `run_scan(ScanRequest(depth=
"headers", build_targets=("//:lib",), build_info=<precaptured jsonproto>))`
reached the `.abicheck.yml`-only code path's own `click.ClickException`
leak instead of the framework-neutral `PlanningError` an *explicit*
`build_targets` should get. Fixed by widening the exemption from
`collection_for_ci_mode(collect_mode)[1]` alone to `headers or
collection_for_ci_mode(collect_mode)[1]` — exempt only when *neither*
consumer (`embed_build_source` nor the L2 seed) can reach `build_info` at
all. This closes the gap for every case `AnalysisPlanner`/`run_scan_core`'s
own inputs can see (an explicit `--build-target`/`ScanRequest.
build_targets`); the `.abicheck.yml`-only case above is unaffected by this
fix and remains the already-documented `click.ClickException` leak, since
neither `run_scan_core` nor `AnalysisPlanner` can see a value that
isn't discovered until deep inside `collect_inline_pack`.
`tests/test_bazel_root_targets_scan.py::
test_run_scan_depth_headers_with_explicit_build_target_raises_planning_error`
pins the fixed (explicit `build_targets`) case; the pre-existing
`test_run_scan_depth_headers_still_rejects_bazel_scoping_mismatch` was
re-verified to still exercise the unaffected (`.abicheck.yml`-only) case
correctly.
**An eighth review round found the same class of gap on the plural entry
point.** `service_scan.run_scan_set` (`scan --artifact-set`'s typed API)
had no Bazel-scoping pre-flight of its own: an unsupported request
reached each member's own `run_scan_core` check only *after*
`discover_artifact_set()`, `check_artifact_set_soname_collisions()`, and
`artifact_set_member_exports()` had already run for every member, wasting
real discovery/parsing work before the request was ultimately rejected
anyway. Fixed by adding the same pre-flight check to the top of
`run_scan_set`, right after `_reject_comparison_only_fields(req)` and
before the shared budget clock starts or `discover_artifact_set()` runs.
Rather than duplicate `run_scan_core`'s exemption logic (the depth=binary
header-clearing rule two findings above) a second time, both call sites
now share one function, `workflows.plan.scan_bazel_scoping_failure()`, so
a future refinement to the exemption rule has exactly one implementation
to change instead of two independently-maintained copies.
`tests/test_bazel_root_targets_scan.py::
test_run_scan_set_rejects_bazel_scoping_mismatch_before_discovery` pins
the fix by asserting `discover_artifact_set()` is never even called for a
mismatched request; `test_run_scan_set_depth_binary_exempts_the_early_
bazel_scoping_check` pins the sibling depth=binary exemption for the
plural entry point.
**A ninth review round found that raw depth alone is not the whole story
for `dump`/`compare`'s own `AnalysisPlanner` check either.**
`_check_bazel_target_scoping` exempted `depth="binary"` purely by reading
the request's raw, requested depth — but `DumpRequest.resolved_collect_mode`
(a private CLI hook: `compare`'s own implicit-dump path resolves collect
mode from the *pair* and forwards it in, so the real run doesn't re-derive
a possibly-different mode from `depth` in isolation), when set, overrides
what `depth` alone would resolve to, and `resolve_dump_request_evidence`
honors that override. A `DumpRequest(depth="binary",
resolved_collect_mode="build", ...)` therefore still runs
`collect_inline_pack` for real at execution time — the override, not the
raw depth, decides whether `build_info` is ever consulted — so the
pre-flight check's exemption on raw depth alone let an unsupported request
reach `resolve()`, then fail later inside `collect_inline_pack` as a
flattened `SnapshotError` instead of the promised `PlanningError`. Fixed
by adding `resolved_collect_mode` to `SidePlan` (populated only for `dump`
sides — `CompareRequest` has no such field, so every `compare` side keeps
`None` and this changes nothing for `compare`) and checking it first: when
set, it alone decides the exemption (`"off"` exempts, anything else
doesn't, regardless of raw depth); only when unset does the check fall
back to the pre-existing raw-depth-only rule. `tests/test_analysis_plan.py::
TestBazelBuildTargetScoping::test_resolved_collect_mode_override_defeats_the_binary_exemption`
pins the fixed case; its sibling
`test_resolved_collect_mode_off_override_is_exempt_even_at_other_depths`
pins the converse (an explicit `"off"` override exempts even at a depth,
e.g. `"build"`, that the raw-depth-only rule would otherwise reject).
**A tenth review round found the eighth round's fix (moving `run_scan_set`'s
own check before discovery) had a CLI-level sibling gap.**
`cli_scan._run_artifact_set` (`scan --artifact-set`'s own CLI entry point)
has its own pre-flight check, separate from `run_scan_set`'s — but it ran
only *after* `_resolve_artifact_set_paths()`/`discover_artifact_set()` had
already traversed a directory and statted/format-validated every explicit
member. An invalid member's own error could therefore mask the request's
intended `PlanningError`/usage error, and a mismatched request paid for
real discovery work before `run_scan_set`'s own (already-fixed) check
rejected it anyway. Fixed by moving the check to the very top of
`_run_artifact_set`, before any discovery work starts — every value the
check needs (`depth`, `build_info`, `build_targets`) is already a raw
function parameter, so no reordering of the rest of the function was
needed. `tests/test_bazel_root_targets_scan.py::
test_scan_cli_artifact_set_rejects_bazel_scoping_mismatch_before_discovery`
pins it, the CLI-level sibling of the eighth round's own
`test_run_scan_set_rejects_bazel_scoping_mismatch_before_discovery`.
**An eleventh review round found the ninth round's own fix (the
`resolved_collect_mode` override) was itself incomplete.** Even a genuine
`"off"` collect mode — whether from raw `depth="binary"` or from an
explicit `resolved_collect_mode="off"` override — does not by itself mean
`build_info` is never consulted: the L2 seed's own independent
header-seeding pass (`_seeded_includes_and_compile_context`/
`collect_inline_pack`) still runs whenever real headers are present,
regardless of collect mode — the identical class of gap the seventh round
above already fixed for `scan_bazel_scoping_failure` (`headers or
collection_for_ci_mode(...)[1]`), just not yet ported to `dump`/`compare`'s
own check. Fixed by adding the side's raw `headers` to `SidePlan` and
exempting `"off"` only when there is no header-seeding consumer:
`depth="binary"` still clears headers to empty independent of any
override (`service_compare_evidence._headers` keys off raw depth alone),
so that clearing is folded into the effective-headers computation rather
than re-derived from collect mode. `tests/test_analysis_plan.py::
TestBazelBuildTargetScoping::test_resolved_collect_mode_off_does_not_exempt_real_headers`
pins the fixed case (an explicit `"off"` override with real headers and a
scoped pre-captured Bazel jsonproto); its sibling
`test_resolved_collect_mode_off_with_no_headers_stays_exempt` pins that the
headers check doesn't over-reject a genuinely headerless request.
**A twelfth review round found the eleventh round's own fix introduced a
new false positive it didn't have before.** The no-override branch only
ever equated collect mode `"off"` with `depth="binary"` -- but
`depth="headers"` resolves to `"off"` too (`_resolve_depth_collect_mode`'s
mapping: only `"build"`/`"source"` resolve to something else). A
headerless `dump`/`compare` request at `depth="headers"` combined with an
explicit `build_targets` and a scoped pre-captured Bazel jsonproto was
therefore wrongly rejected: neither `embed_build_source` (collect mode
`"off"`) nor the L2 seed (nothing to seed, no real headers) would ever
have consulted `build_info`, yet the planner still raised `PlanningError`.
Fixed by adding `_depth_implied_collect_mode()` -- mirroring
`service_compare_evidence._resolve_depth_collect_mode`'s explicit-depth
mapping (duplicated, not imported, for the same leaf-module reason that
function's own docstring states) -- and using it for any explicit depth
in the no-override branch, rather than special-casing `"binary"` alone.
`depth="headers"` with *real* headers still correctly stays rejected,
since only `"binary"` clears headers to empty before this check runs;
`"headers"` keeps them, so the L2 seed still runs.
`tests/test_analysis_plan.py::TestBazelBuildTargetScoping::
test_headerless_depth_headers_is_exempt` pins the fixed case; its sibling
`test_depth_headers_with_real_headers_is_still_rejected` pins that real
headers at that same depth still correctly reject. The pre-existing
`test_other_depths_still_rejected_alongside_binary` was narrowed from
`("headers", "build", "source")` to `("build", "source")` accordingly --
`"headers"` alone (headerless) is no longer part of the always-rejected
set, which is the corrected behavior this round establishes, not a
weakening of the test.
**A thirteenth review round found `scan --artifact-set`'s own pre-flight
(`cli_scan._run_artifact_set`) had a second, narrower version of the same
false-positive/false-negative pair, specific to an *unset* `--depth`.**
Two intermediate designs were tried and rejected in this same round before
landing on the fix below: a bespoke
`workflows.plan.artifact_set_bazel_scoping_failure` that treated an
unset `--depth` as always non-`"off"` (matching every prior caller's
shape) false-positive-rejected a genuine no-op artifact-set request whose
real per-member risk scoring would resolve to `"off"`; a second attempt
that instead treated an unset `--depth` as always exempt
false-negative-accepted a seeded, high-risk request (e.g. a public-header
edit) that `run_scan_core`'s own later, correctly-resolved per-member
check would still reject with exit 64 -- recreating the exact dry-run/
execution parity defect this whole known-gap entry exists to close, just
one level narrower (real-run vs. real-run instead of dry-run vs.
real-run). Both were symptoms of approximating a value `AnalysisPlan`'s
own design (`workflows/plan.py`'s module docstring) deliberately excludes
from a pre-flight check: an unset `--depth` only resolves to a real
`collect_mode` via risk scoring over the request's own seeded change
(`service_scan._resolve_member_scan_level`, the identical primitive
`estimate_artifact_set`'s own `--dry-run` cost totals already resolve
through). The fix drops the approximation entirely: `_run_artifact_set`
now builds the same probe `ScanRequest` `estimate_artifact_set` would and
calls `_resolve_member_scan_level` on it to get the real `eff_depth`/
`collect_mode` before discovery, then hands those to the existing,
already-shared `scan_bazel_scoping_failure` -- no bespoke artifact-set-
shaped guard needed. `_resolve_member_scan_level` reads only
request-level fields (`depth`/`changed_paths`/`seeded`/`risk_rules_path`/
`mode`), none of them derived from discovery, so this probe needs no
`binaries` and costs nothing discovery-shaped to build. The now-dead
`artifact_set_bazel_scoping_failure` was deleted from `workflows/plan.py`
rather than left as an unused second copy.
`tests/test_bazel_root_targets_scan.py::
test_scan_cli_artifact_set_unset_depth_low_risk_seed_config_scope_is_unaffected`
pins the no-op case (a low-risk `--changed-path`, e.g. a docs file, seeds
`S0`/`collect_mode="off"`, so a config-sourced scope stays unenforced);
its sibling
`test_scan_cli_artifact_set_high_risk_seed_config_scope_still_rejects`
pins the risky case (a high-risk `--changed-path`, e.g. a public header,
seeds a non-`"off"` `collect_mode`, so the same config-sourced scope is
still enforced and rejects with exit 64).
**A fourteenth review round found the thirteenth round's own fix had no
error translation of its own.** `_resolve_member_scan_level()` raises a
plain `ValueError` for a malformed `--risk-rules` profile (via
`_load_risk_rules_for_service`, which converts the single-binary path's
`click.ClickException` into exactly that so a direct typed-API caller
never sees a Click-flavored exception) -- but the thirteenth round's new
resolution call sat ahead of both of `_run_artifact_set`'s existing
`try`/`except (ArtifactSetError, ValueError)` blocks, so that `ValueError`
now leaked past them (exit 1, an unhandled traceback) for both a real run
and `--dry-run` alike, instead of the established clean usage error (exit
64) `TestArtifactSetMalformedRiskRules::
test_malformed_risk_rules_yaml_is_usage_error` already pinned for this
exact class of input (a ninth-round-era regression, per that test's own
docstring). Fixed by wrapping the new resolution call in its own
`try`/`except ValueError`, translating to `click.UsageError` the same way
the two pre-existing blocks below it already do.
Historical analysis retained below for the record.
`BazelAdapter.collect()`'s `self.targets` scoping is applied
in exactly two places: gating whether a *live* `bazel query` subprocess
runs at all (`_resolve`/`_run_bazel`, only reachable when `workspace` is
given and no pre-captured `aquery=`/`cquery=` path was supplied), and
populating the `TargetScope` report (`requested`/`resolved`/
`transitive_count`) on the returned `BuildEvidence`. Neither path filters
`ev.compile_units`/`ev.targets` themselves once a pre-captured file is
parsed — `_collect_aquery`/`_collect_cquery` walk the *entire* captured
action/target graph unconditionally. `buildsource/inline.py`'s
`_maybe_collect_bazel_build_info` (the function `collect_inline_pack`
routes a `--build-info` recognized as `bazel_aquery`/`bazel_cquery` through)
doesn't even accept a `targets` parameter, so `--build-target` isn't merely
unenforced here — it's never threaded to this call site at all, and no
diagnostic or `TargetScope` records that a scope was requested. Confirmed
by reading the code (no live Bazel repro run in this pass): `scan
--build-info saved-aquery.json --build-target //:lib` (or the identical
`dump` invocation) collects every TU in the captured workspace, with no
error, warning, or `TargetScope` entry showing the mismatch between what
was requested and what was actually scoped — unrelated targets can pollute
L3 evidence and any detector built on it. **Not fixed here, and the two
candidate fixes are not equally easy:** (1) *Actually filter* the captured
graph to the transitive closure of the requested roots. Feasible in
principle for `cquery` data, whose `Target.dependencies` already carries a
real label-to-label dependency edge list (`attrs.get("deps", ...)` in
`_collect_cquery`) a BFS could walk — but a `cquery`-only capture produces
no `compile_units` at all (only `_collect_aquery` does), so this alone
doesn't scope the TUs that actually matter. `aquery` data has no equivalent
target-level `deps` list — only a flat action list, each tagged with its own
`targetId` (`CompileUnit.target_id`) — so a correct closure would have to be
reconstructed from the action graph's own input-artifact edges
(`_AqueryGraph`'s depset walk), a materially different and more involved
algorithm than the `cquery` case, not a shared implementation. (2) *Reject*
the combination with a clear usage error instead — architecturally cleaner,
but `collect_inline_pack`/`_maybe_collect_bazel_build_info` sit in a shared
Tier-2 module used by both CLI and typed-API callers (`embed_build_source`,
in turn called from `cli_dump_helpers.py`, `scan_engine.py`, and
`service_input_resolution.py`), and raising there can only be a plain
`ValueError`/`AbicheckError` (per this codebase's Tier-1/Tier-2 separation —
Click-specific exceptions belong to the CLI layer only). Neither `dump_cmd`
nor `scan_cmd` currently wraps its `embed_build_source`/`run_scan_core` call
in a catch for that error class (confirmed by reading both), so today such
an error would propagate as an unhandled Python exception with a raw
traceback instead of a clean `click.UsageError`/exit 64 — fixing that
requires adding (and testing) a catch-and-reraise at every one of those call
sites, not a single choke point. Either option is a real, multi-call-site
feature needing its own dedicated design and test coverage, not a
same-session reactive patch under continued review pressure — per this
file's own "known gaps over risky reactive patches" convention. Until then,
the safe workaround is to only combine `--build-target` with a *live* query
(`--sources <workspace>` and no `--build-info`, letting `BazelAdapter` run
`bazel query deps(...)` itself, which does scope correctly) or to
pre-capture the `aquery`/`cquery` jsonproto already scoped to the desired
targets before passing it as `--build-info`.

### The native ELF `abicheck dump` path never applies L3 build context to its own L2 header parse, and this is now confirmed by an external end-to-end reproduction, not only by the pre-existing module-map note about the `DumpRequest` migration (2026-08-15, `napetrov/abicheck-bazel-lab` audit against `5b52989`)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry itself records the ELF dump L3->L2 fold as closed through the shared `seed_includes_and_fold_compile_context` (now in abicheck/buildsource/l2_seed.py, used by workflows/artifact/resolve.py). The later residual items say 'Closed by PR D'. The bazel duplicate-include shape is pinned by tests/test_dump_cli_typed_api_parity.py. The one leftover sub-item, a missing extra_hash_dirs on the PE/Mach-O path, now looks handled: abicheck/service_header_scoped.py:177 and :195 pass `extra_hash_dirs=deferred_dirs`. The scan_engine parts are moot because scan_engine.py was deleted. The entry is a 1200-line closed history and could be collapsed.

The "Module map"'s `service_dump_pipeline.py` entry
above already named the cause — "the native `dump` CLI does not build a
`DumpRequest` yet" — but that note described a *migration gap*, not a
concrete, reproduced *symptom*. Traced here to the exact call graph: P0.3's
L3→L2 fold (`service_input_resolution._seeded_compile_context`, wired via
`buildsource/l2_seed.derive_l2_compile_context` and
`buildsource/header_compile_context.resolve_header_compile_context`) is
real and already correct — `resolve_side_snapshot()`, the function that
calls it, is shared by *both* `service_compare_pipeline.
resolve_compare_request` (the path `compare`'s own implicit-dump operand
takes) *and* `service_dump_pipeline.run_dump_request`. But
`cli.py`'s `dump_cmd` (the ELF path) never calls `run_dump_request` at
all — it calls `cli_dump_helpers.perform_elf_dump()` directly, a separate,
older code path whose `CompileContext` is built only from explicit
`--ast-frontend`/`--compiler*`/`--sysroot`/`--nostdinc` flags (confirmed by
grep: `cli_dump_helpers.py` contains no reference to
`resolve_side_snapshot`, `_seeded_compile_context`, or
`derive_l2_compile_context` anywhere). The external repro: a fresh
`abicheck dump lib.so --sources . --build-info compile_commands.json
--depth source` snapshot's own `parsed_with_build_context` reads `false`
and `language_standard` reads `""` even though real L3 evidence was
supplied and is embedded in the snapshot — the evidence is collected and
stored, but never routed to the L2 header-AST invocation, because that
invocation runs through a path P0.3's fold was never wired into.
Consequence: a `dump`-produced baseline and a `scan --against`
live-binary comparison of the *same* project, given the *same* L3
evidence, resolve to genuinely different `CompileContext`s
(`profile_fingerprint` mismatch on `include_sequence`/
`language_standard`), so `scan` correctly (per ADR-050 D2) refuses the
comparison as `NOT_COMPARABLE` — not because the evidence was
insufficient, but because the two commands extracted under
non-comparable recipes for reasons neither command's own diagnostics
name. **Not fixed here**: migrating `dump_cmd`/`perform_elf_dump`
(`cli_dump_helpers.py` is already at 1914 of its 2000-line hard cap —
real headroom for an inline fix is tight) to route through
`run_dump_request` the way `compare`'s implicit-dump path already does
is a genuine, cross-cutting architecture change — the "what that
migration needs first" the module-map note already flags — not a
same-pass reactive patch. The PE/Mach-O `dump` paths (`service.py`'s
`_dump_pe`/`_dump_macho` mirrors) were not independently checked for the
identical gap in this pass.

**Closed, additively, without the `run_dump_request` migration this entry
originally called for.** Full migration would have meant rewriting
`perform_elf_dump`'s already-large, carefully-ordered pipeline (header
parse, ADR-039 build-context harvest, G14/G23/G26 Python/NumPy attach,
the header-graph and clang-layout-tool second passes) around a
fundamentally different call shape — real, but out of proportion to the
actual gap, which is narrowly "the L3→L2 fold never runs on this path,"
not "this path's whole architecture is wrong." The fix is additive
instead: `buildsource/l2_seed.fold_l3_compile_context()` — a new, shared
wrapper around the already-correct `derive_l2_compile_context()` +
`_merge_l3_compile_context()` pair (the latter *moved* into `l2_seed.py`
from `service_input_resolution.py`; see that function's own docstring for
why — leaving it in place would have closed an
`l2_seed -> service_input_resolution -> cli_dump_helpers -> l2_seed`
import cycle the AI-readiness gate correctly rejects once
`cli_dump_helpers` needed it directly) — called from both
`perform_elf_dump` (ELF) and `handle_non_elf_dump` (PE/Mach-O, which
shared the identical gap: confirmed by reading `service.run_dump`'s own
`compile` parameter never receiving anything beyond the CLI/config-
resolved context either). Both call sites now fold the real L3
`CompileUnit`-derived context (`-std=`, ABI-relevant `-D`/`-U`, target,
sysroot) into the *same* explicit context CLI flags/`.abicheck.yml`
already built, exactly mirroring what `_seeded_compile_context` already
does for `compare`'s implicit-dump path via `resolve_side_snapshot` — so
`dump` and `compare` now fold under one shared primitive, even though
`dump`'s own CLI pipeline still doesn't route through `run_dump_request`.
`perform_elf_dump`'s two independent second-pass clang re-parses (the
header-graph attach and the clang-layout-tool attach) also now receive
this same fully-merged context via `effective_compile_context =
l3_effective_ctx`, closing a narrower, previously-undocumented sibling
gap where those passes' own `gcc_options`-string-only re-derivation never
looked at `gcc_option_tokens`/`sysroot`/`nostdinc`/deferred include roots
at all — so a real disagreement on any of those between the primary
parse and the second passes could previously have gone unnoticed even
without any L3 evidence in play. `AbiSnapshot.parsed_with_build_context`
is now stamped from either this fold or the older `-p`/`--compile-db`
mechanism (OR'd, matching `resolve_side_snapshot`'s own rule), on both
the ELF and PE/Mach-O paths.

**A third, independent instance of the same gap, found only by testing
the actual end-to-end repro rather than trusting this entry's original
framing: `scan`'s own candidate resolution never applied the fold
either.** This entry's first draft claimed `_seeded_compile_context`
"already does" this for `scan` — false. `scan_engine._build_new_snapshot`
calls `service.resolve_input` directly, not `resolve_side_snapshot`, so
`compare`'s/`dump`'s fold never ran on `scan`'s candidate side. A
same-project `scan --against` a freshly-fixed `dump` baseline still
returned `NOT_COMPARABLE` on `language_standard` even after the two
`dump`-path fixes above — confirmed by actually running the repro end to
end (a real `g++`-compiled library + `compile_commands.json`), not by
re-reading the code. Fixed the identical way: `_build_new_snapshot` now
calls `fold_l3_compile_context` immediately before `resolve_input`, and
stamps `parsed_with_build_context` the same way. The same real repro now
produces `NO_CHANGE` end to end (`dump` baseline → `scan --against`),
which is the actual acceptance criterion this whole entry exists for.

**A fourth finding, from a Codex review of this same change, on the
*shared* `_merge_l3_compile_context` primitive itself — pre-existing in
the `compare`/`scan`-implicit-dump fold this PR reused, not introduced by
it, but real and now fixed alongside it.** "Derived leads, explicit
wins" (last-flag-wins) is the right rule for a macro/std/sysroot switch,
but `header_compile_context._context_flags` also renders a matched
`CompileUnit`'s own `include_paths`/`system_include_paths` as `-I`/
`-isystem` tokens, and an include search path is *first*-match-wins —
so putting a derived `-I` ahead of an explicit one (the pre-existing
order) silently let the build's own header win over a caller's explicit
override for a colliding basename. Fixed with a new
`_split_include_tokens()` helper that carves derived's own include-search
entries (`-I`/`-isystem`/`-iquote`/`-idirafter`/MSVC `/I`/`/imsvc`/
`/external:I`, spaced-pair-aware) out of the leading last-flag-wins group
and appends them *after* explicit instead — every other derived token
keeps its original leading, overridable position.

Verified end-to-end against a real `compile_commands.json` fixture
through the actual `perform_elf_dump`/`handle_non_elf_dump`/
`_build_new_snapshot` entry points (not a hand-built `CompileContext`) —
see `tests/test_cli_dump_helpers_coverage.py::
test_perform_elf_dump_folds_l3_compile_context_into_header_parse`,
`tests/test_non_elf_dump_l2_seed.py::
test_non_elf_dump_folds_l3_compile_context_into_header_parse`,
`tests/test_scan_l2_cleanup_ordering.py::
test_scan_candidate_folds_l3_compile_context_into_header_parse`, and
`tests/test_header_compile_context.py`'s
`test_merge_l3_compile_context_explicit_include_search_wins_first_match`/
`test_merge_l3_compile_context_attached_include_form_stays_paired` —
confirming the derived `-std=`/`-D` flags reach the primary header-AST
parse, `parsed_with_build_context` is stamped, and an explicit `-I`
outranks a derived one. Full test suite (28k+ tests) green; no
import-cycle or file-size regression (`cli_dump_helpers.py` stayed under
its 2000-line hard cap by moving the shared fold logic into `l2_seed.py`
rather than duplicating it inline for both call sites).

**Two more findings on the same change, both fixed, one left as a
narrower residual gap.** (1) A derived `-I`/`-isystem` reaches the header
parse only as an opaque `gcc_option_tokens` string, which the AST cache
key's own directory-mtime hashing (`extra_includes`/`extra_hash_dirs`,
`dumper_ast_config.py`) never inspected — editing a header under a
derived include dir could reuse a stale cached AST. `fold_l3_compile_
context()` now also returns the derived include directories
(`_include_operand_dirs()`), threaded into `perform_elf_dump`'s existing
`extra_hash_dirs`. **Not closed for `handle_non_elf_dump` (PE/Mach-O) or
`scan_engine._build_new_snapshot`**: neither `dump_native_binary`→
`service.run_dump` nor `service.resolve_input` exposes a public
`extra_hash_dirs` hook the way `perform_elf_dump`'s own direct `dump()`
call does — and `service.py`'s own internal cache-key computation for
those paths already has the identical, broader, pre-existing gap for
*any* `CompileContext.gcc_option_tokens` include entry (not just an
L3-derived one), so a scoped fix here would still leave the general case
open. Threading a real `extra_hash_dirs` channel through `resolve_input`'s
public signature is a genuine, separate change affecting every caller of
that function, not a follow-up to this one. (2) `scan`'s own fold call
hard-coded `lang_explicit=False`; since `scan --lang c` is never the
Click default (only `"c++"` is), it is always a genuine explicit
request, and the hard-coded `False` let a matched C++ compile unit's own
`-std=c++20` reach a parse `scan --lang c` was explicitly forcing into C
mode. Fixed by treating `lang == "c"` as explicit, mirroring `perform_
elf_dump`'s identical squash-guard rule elsewhere in this same area.

**A fifth finding, on the fold's own call shape rather than its logic: the
include-dir seed and the L3→L2 fold each independently collected L3
evidence, and a caller genuinely needing the inferred build query (no
existing compile database) could self-deadlock.** All three call sites
(`perform_elf_dump`, `handle_non_elf_dump`, `scan_engine._build_new_
snapshot`) called `seed_l2_includes()` and then, immediately after,
`fold_l3_compile_context()` — each independently calling
`buildsource.inline.collect_inline_pack()`. Harmless when at most one call
can trigger the zero-config *inferred* build-system query (cmake/make/
bazel, gated by `allow_inferred_build_query`/`collect_mode`), but a real
inferred query is a `flock`-protected, deterministic per-source-tree temp
build dir (`build_query._claim_inferred_build_dir`) held until its own
*cleanup* runs — deliberately deferred until after the header parse
consumes the seeded dirs, i.e. long after the seed call returns. The
second call's own inferred-query attempt then contends on the identical
lock the first call is still holding, and — being the *same process*
reopening the same lock file, not a different one — `flock`'s
per-open-file-description semantics mean this is genuine self-contention,
not a race with some other process: it blocks for up to
`INFERRED_QUERY_TIMEOUT_S` (600s) before falling back to a throwaway
sibling dir (Codex review). Fixed by collapsing the two into one
collection: `buildsource.l2_seed.seed_includes_and_fold_compile_context()`
runs `collect_inline_pack()` exactly once and derives both the include-dir
seed (mirroring `seed_l2_includes`'s own gating and directory derivation)
and the compile-context fold (mirroring `derive_l2_compile_context`'s
resolution + merge) from that single `BuildEvidence`, so only one inferred
query — if any — ever runs per call. All three call sites now call this
one combined function instead of the two separate ones; `seed_l2_includes`
itself is unchanged and still used independently elsewhere (well-defined
on its own — the bug was specifically two independent *collections* of
the same evidence in one logical operation, not either function
individually). Verified against the same real `compile_commands.json`
fixtures the fourth finding's own regression tests use, now exercised
through the combined entry point. `fold_l3_compile_context` itself —
the standalone wrapper this combined function was built to replace — is
**not** still used independently: once all three call sites moved to
the combined function, nothing called it anymore, so it was removed
entirely as dead code rather than left orphaned alongside its
replacement (found while writing this entry's own closure notes; no
call site or test referenced it directly by the time this was checked).

**A seventh finding, from writing direct unit tests for the combined
function itself (not through any of the three call sites), on the
combined function's own body — a real regression relative to both
siblings it was assembled from.** `derive_l2_include_dirs`'s and
`derive_l2_compile_context`'s own identical comments both state "pack
resolution stays inside this protected section... a corrupt/unreadable
pack must degrade best-effort, not raise" — but
`seed_includes_and_fold_compile_context`'s own `_resolve_l2_seed_pack_args`
call was placed *before* the `try:` block, not inside it, so a corrupt
pack (a `manifest.json` present but unparseable, `is_pack_dir`'s own
documented "still a pack" case) raised a bare `JSONDecodeError` straight
out of the function instead of degrading to the pre-existing "nothing to
apply" no-op every other caller relies on. Caught immediately by writing
`test_seed_and_fold_corrupt_pack_degrades_to_empty` (mirroring the two
siblings' own `test_derive_l2_*_corrupt_*_pack_degrades_to_empty`
tests) — it failed with the raw `JSONDecodeError` before the fix, not
the intended empty-degrade result. Fixed by moving the
`_resolve_l2_seed_pack_args` call inside the `try:`, matching both
siblings exactly. The four new direct tests (no-inputs no-op, no-match
returns none, ambiguous raises and drains pending cleanups, corrupt pack
degrades) live in `tests/test_non_elf_dump_l2_seed.py` (not the more
natural `test_header_compile_context.py`, which is near its own
1500-line soft/2000-line hard cap) as a
"seed_includes_and_fold_compile_context branch coverage" section.

**A sixth finding, on `_split_include_tokens` itself, documented as a
residual gap rather than fixed here (Codex review).** The split
distinguishes include-vs-non-include tokens and preserves relative order
within each group, but not GCC/Clang's distinct include-search *buckets*
(`-iquote` > `-I` > `-isystem` > `-idirafter`, each a separate search
class the compiler consults in that fixed order regardless of argv
position). An explicit `-isystem` therefore still searches ahead of a
derived `-iquote`/`-I` after the split, even though a real compiler
would consult the quote/regular buckets first regardless of flag order.
A correct fix needs the merge to track bucket membership, not just
include-vs-non-include — a real, if narrow, redesign of the function's
output shape, not a follow-up to the explicit-vs-derived ordering fix
(the fourth finding above) this function exists for. See the function's
own docstring for the same note.

**An eighth and ninth finding, both from a fresh Codex review round on the
P1/P2-labeled commit that added the combined function above, both real
and both fixed.** (8, P1) `scan_engine._build_new_snapshot`'s own L3->L2
fold only updated its *local* `compile_context` variable — `run_scan_core`
still held the caller's original, un-folded `compile_context` and
forwarded *that* to `_run_baseline_compare`, so a `scan --against` a
native library could fold real L3 context into the *candidate*'s header
parse while the *baseline*'s own native-library header parse never
received it — silently recreating the exact `NOT_COMPARABLE`/false-ABI-
difference risk this whole P0.3 fold exists to close, just moved from the
dump-vs-scan pairing to the candidate-vs-baseline pairing within one
`scan --against` invocation. Fixed by widening `_build_new_snapshot`'s
return from `(snapshot, effective_includes)` to `(snapshot,
effective_includes, effective_compile_context)` — mirroring the existing
`effective_includes` precedent for exactly the same reason — and having
`run_scan_core` forward that third value to `_run_baseline_compare`
instead of its own original. Regression test:
`tests/test_cli_scan.py::test_baseline_compare_receives_l3_folded_compile_context`
(mocks both `_build_new_snapshot` and `_run_baseline_compare` at the
`cli_scan.py`/`scan_engine.py` module boundary, confirmed to fail against
the pre-fix code with the un-folded context forwarded instead of the
sentinel folded one). (9, P2) `perform_elf_dump`'s ADR-039 build-context
collector (`_attach_build_context`/`_user_define_flags`) has its own
long-standing, explicit rule — stated in both functions' docstrings —
that the auto-derived, per-header-matched build context must never be
unioned snapshot-wide, since doing so would mark one TU's `-D` active for
every scanned header. The L3->L2 fold's `l3_context_applied`
reassignment folds that same derived context into the *identically-named*
`gcc_option_tokens` local variable used for the primary header parse,
which silently defeated that rule once `_user_define_flags` was called
with the (by-then-merged) `gcc_option_tokens` rather than the caller's
original tokens. Fixed by capturing `_user_gcc_option_tokens =
gcc_option_tokens` before the fold's reassignment and passing that
captured value to `_user_define_flags` instead. Regression test:
`tests/test_non_elf_dump_l2_seed.py::
test_perform_elf_dump_keeps_l3_derived_flags_out_of_build_context_collector`
(confirmed to fail against the pre-fix code, asserting the L3-derived
`-DL3ONLY=1` reaches `_attach_build_context`'s `extra_flags` when it must
not). `handle_non_elf_dump` (PE/Mach-O) was checked and does not call
`_attach_build_context` at all — the ADR-039 collector is ELF-only — so
finding 9 has no PE/Mach-O counterpart.

**A tenth finding, from the same Codex review round (P1), on
`service._attach_header_graph`'s own second, independent
`_clang_header_dump` pass — real and fixed.** That pass has its own AST
cache key, computed from its own `deferred_dirs`, which only covered
`resolve_inferred_header_roots`'s own deferred roots — never any
include-search directory riding in `compile.gcc_option_tokens` itself
(an explicit `--gcc-options`/`--compiler-option -I`, or — since the
P0.3 fold — a compile-DB-derived one). A directory the primary snapshot
pass already hashes into its own cache key was therefore invisible to
this second pass's key, so editing a header under it could silently
reuse a stale cached graph even though the primary snapshot re-parsed
correctly and picked up the change. Fixed by extracting
`l2_seed._include_operand_dirs`'s logic into a new, shared
`header_utils.include_operand_dirs()` (a leaf module both `l2_seed.py`
and `service.py` already import from) and folding its result into
`_attach_header_graph`'s own `deferred_dirs` — closing the gap
generically for *any* include-search token the merged context carries,
not just an L3-derived one, since `_attach_header_graph` has no way to
distinguish the two once they're both flattened into
`gcc_option_tokens`. `l2_seed._include_operand_dirs` itself is now a
thin alias forwarding to the shared function, so existing callers/tests
needed no changes. Regression tests:
`tests/test_service_unit.py::TestAttachHeaderGraphHashesIncludeSearchTokens`
(both confirmed to fail against the pre-fix code — the positive case
asserting a `gcc_option_tokens`-carried `-I` dir reaches
`_clang_header_dump`'s own `extra_hash_dirs`, the negative case pinning
the no-tokens baseline stays `()`).

**Several smaller findings from the same CodeRabbit review round, all
fixed alongside the above.** (1) The changelog fragment's early entries
still named the now-removed `fold_l3_compile_context()` wrapper as the
shipped fix — corrected to the combined function's real name (this
file's own narrative-history style is left as-is, since later
paragraphs already record the removal). (2) `seed_includes_and_fold_
compile_context`'s own guard (`(sources is None and build_info is
None) or (not want_seed and not headers)`) had a redundant second
clause — `not want_seed and not headers` always reduces to `not
headers`, since `want_seed = bool(headers) and ...` is already `False`
whenever `headers` is empty — leaving a genuinely unreachable `if not
headers:` guard a few lines further down; both simplified/removed. (3)
The same function's `pending_cleanups.extend(cleanups)` ran before
`_merge_l3_compile_context`/`_include_operand_dirs`, so either raising
would have the `except Exception` branch's `_run_cleanups(cleanups)`
remove the same already-handed-off thunks a second time — not fatal
(`_run_cleanups` logs rather than raises on an already-closed handle),
but a real, avoidable double-removal; moved the extend to after every
remaining fallible step succeeds. (4) A genuinely weakened test
assertion in `test_scan_l2_cleanup_ordering.py`
(`test_scan_l2_seed_cleanup_runs_before_embed`): `assert seed_kwargs.
get("pending_cleanups") is not None` also passes for the outer scan
list itself (`defer_cleanup=[]` is never `None` either), so it would
not have caught a regression that reused it — fixed to assert identity
against the outer list directly. (5) Two cosmetic-only cleanups: a
redundant local `import json` shadowing the same module-level import,
and an unparenthesized chained `and`/`or` (ruff `RUF021`, not in this
repo's enabled rule set but still real and fixed) in `perform_elf_dump`'s
`parsed_with_build_context` stamp condition. (6) The three fake
`seed_includes_and_fold_compile_context` implementations in
`test_scan_l2_cleanup_ordering.py` each hand-built an identical
`CompileContext` from the same eight kwargs — extracted into one shared
`_CC_FIELDS`/`_explicit_ctx_from_kwargs()` helper, mirroring the
identical pattern `test_cli_dump_helpers_coverage.py` already used.

**An eleventh finding, from a further Codex review round (P1), on
`_existing_include_dirs`'s own selection scope — real, but a
pre-existing, cross-cutting design shared with `compare`'s implicit-dump
path, documented as a known gap rather than fixed here.** When no
explicit `-I` is given, `_existing_include_dirs` gathers directories
from *every* `CompileUnit` in the build evidence, not only the unit(s)
`resolve_header_compile_context` actually matched to the headers being
parsed. Those directories reach the AST command as `extra_includes`,
emitted ahead of the matched context's own include tokens — so in a
multi-TU build, an unrelated TU's own colliding generated header (e.g.
a stray `config.h`) can shadow the header the matched TU would have
resolved, while the snapshot may still get stamped
`parsed_with_build_context=True` from the (separate) successful fold,
reading as more authoritative than the seed actually was. Confirmed
**not** new to this PR: `service_input_resolution._seeded_includes`/
`_seeded_compile_context` — the pre-existing pair `resolve_side_snapshot`
already uses for `compare`'s implicit-dump operand, and the reference
implementation this PR's own fold was modeled after — combines the
identical broad-seed-plus-matched-fold shape already, unchanged by this
PR. A correct fix needs `HeaderCompileContextResolution` to expose which
`CompileUnit`s actually matched (today it exposes only a
`matched_unit_count`), then both `_existing_include_dirs`'s caller here
*and* `_seeded_includes` in `service_input_resolution.py` to restrict
the seed to that set — a genuine, cross-cutting widening of a
well-tested module's return shape and two independent call sites, not a
scoped fix reactive to one review comment on one PR. Documented in
`_existing_include_dirs`'s own docstring alongside this entry.

**Closed by PR D (plan "PR 3B", build-context completeness), and the
cross-cutting worry above turned out to be obsolete rather than
addressed.** The entry says the fix needs restricting "both
`_existing_include_dirs`'s caller here *and* `_seeded_includes` in
`service_input_resolution.py`" — two independent call sites. That was
true when written; PR C (#795) since merged those two into one, so
`service_input_resolution._seeded_includes_and_compile_context` and all
three CLI-side resolvers now reach the seed through the single
`l2_seed.seed_includes_and_fold_compile_context`. There was one call site
left to restrict, not two. `HeaderCompileContextResolution` gained
`matched_units` (the tuple; `matched_unit_count` stays as a derived
property, so the two cannot drift), and the combined primitive now
resolves the compile context *before* seeding and passes
`resolution.matched_units` to `_existing_include_dirs` — falling back to
every unit only when nothing matched, which is the case the seed was
built for in the first place (a public header the compile DB does not
cover, reaching into a dependency SDK) and where there is no narrower set
to prefer. Reordering is otherwise unobservable: the one path that skips
the seed is the fail-closed ambiguity raise, which aborts the call either
way. Regression coverage:
`tests/test_build_context_completeness.py::TestIncludeSeedIsRestrictedToMatchedUnits`
(the positive case, the no-match fallback, and the `matched_units`/
`matched_unit_count` consistency), verified to fail against the pre-fix
`seed_units = units`.

**A twelfth finding, from a further Codex review round (P1), on
`run_scan_core`'s own forwarding of the folded context to the baseline
parse — real and fixed.** The eighth finding above fixed `run_scan_core`
forwarding its *original* `compile_context` to `_run_baseline_compare`
unconditionally; that fix itself was too broad in the other direction.
The fold is derived by matching the *candidate*'s own headers against
the *new* build's compile units, so its `-D`/`-U`/`-std`/include flags
describe the new side specifically — but `_run_baseline_compare`'s own
`_resolve_baseline_header_scope` parses a side-aware `-H old=PATH`
baseline through its own, different old headers, whose macros/standard/
generated-header paths may genuinely differ from the new build's.
Forwarding the new side's folded context there unconditionally risked a
bad parse or a false ABI diff on exactly the old-side-headers path the
eighth finding's own fix never distinguished from the common case (no
`baseline_headers`, baseline reuses the candidate's headers). No
old-side build evidence exists to derive a matching fold for that case
(there is no `--build-info-old`/`--sources-old` flag), so the correct
fallback is the caller's plain, unfolded `compile_context` — not a
second, unfounded fold attempt. Fixed by conditioning the forwarded
value on whether `baseline_headers` was given:
`eff_compile_context if not baseline_headers else compile_context`.
Regression test:
`tests/test_cli_scan.py::test_baseline_compare_with_side_aware_headers_keeps_unfolded_context`
(confirmed to fail against the pre-fix code — the folded sentinel
reached `_run_baseline_compare` even with `-H old=...` given).

**A thirteenth finding, from a further Codex review round (P1), on the
twelfth finding's own fix — a real regression in the fix itself, caught
before merge.** `not baseline_headers` was the wrong signal: `cli_scan.py`
builds `baseline_header = header_both + header_old`, so a bare, *shared*
`-H api.h` (no `old=` scoping at all — the ordinary, most common
`scan --against` usage) already makes `baseline_headers` truthy and
identical in *content* to `headers`, since both draw from the same
`header_both` list. The twelfth finding's own fix therefore treated
every `scan --against` invocation with any headers at all as
"old-side-scoped" and fell back to the unfolded `compile_context` —
silently reintroducing this whole PR's own `NOT_COMPARABLE`/false-ABI-
diff bug for the common case, one commit after fixing the narrower
`-H old=PATH` case. Fixed by checking content equality instead of mere
truthiness: `not baseline_headers or list(baseline_headers) ==
list(headers)` — the fold is used whenever the old side's resolved
headers are the same as the candidate's (shared, or genuinely absent),
and only the caller's plain, unfolded context is used when they
actually diverge (a real `old=` override). Regression test:
`tests/test_cli_scan.py::test_baseline_compare_with_shared_bare_header_still_gets_folded_context`
(confirmed to fail against the pre-fix — twelfth-finding-only — code,
which forwarded `None` rather than the folded sentinel for a bare
shared `-H` with no `old=` at all).

**A fourteenth finding, from a further Codex review round (P1), on
`ABI_RELEVANT_FLAG_PREFIXES` itself — real, pre-existing (confirmed
present at this PR's own base commit, before any of its changes), and
documented as a known gap rather than fixed here.** GNU/clang
`-include`/`-imacros` and MSVC `/FI`/`/FU` (forced pre-include) are
absent from this list, so `extract_abi_relevant_flags()` never captures
them into `CompileUnit.abi_relevant_flags` — `buildsource.adapters.
base.SOURCE_OPERAND_FLAGS` already recognizes these as value-taking, but
for an unrelated purpose (not mistaking the operand for the TU source
file), and that recognition never feeds this list. A matched compile
unit's own forced-include header is therefore silently absent from
`header_compile_context`'s derived L2 `CompileContext`, so the P0.3
fold (reached from `dump`/`scan`/`compare`'s implicit-dump path alike,
not just the three call sites this PR added) can report a real match
and stamp `parsed_with_build_context` while still parsing without a
macro-controlling forced-include header the real build always applies.
Confirmed genuinely pre-existing, not introduced by this PR:
`ABI_RELEVANT_FLAG_PREFIXES` and its consumers (`extract_
abi_relevant_flags`, `header_compile_context.py`) already existed at
commit `674a506` (this PR's own base, before any of its changes) with
the identical omission — this PR only added two more call sites
(`dump` ELF/PE-Mach-O, `scan`) to the same, already-existing
`resolve_header_compile_context` machinery `compare`'s implicit-dump
path already used. Not fixed here because a correct fix is a genuine,
non-trivial extraction-logic change: unlike every other entry in
`ABI_RELEVANT_FLAG_PREFIXES` (a bare prefix match that appends the
single matched token), `-include`/`-imacros`/`/FI` always carry a
required following value that must travel with the flag — appending
just the bare prefix (the pattern this whole list otherwise uses)
would silently drop the header filename that is the entire point. A
correct fix needs a new spaced-value-flag branch in
`extract_abi_relevant_flags` (mirroring, but distinct from, its
existing `-D`/`/D` split-form handling), verified end-to-end through
`header_compile_context._context_flags()`'s reconstruction and the
real header-parse invocation's own handling of `-include`/`/FI` —
a cross-cutting change to a shared, well-tested primitive several
other pre-existing paths already depend on, not a scoped fix reactive
to one review comment on this PR. Documented in `ABI_RELEVANT_FLAG_
PREFIXES`'s own docstring alongside this entry.

**Closed by PR D (plan "PR 3B"), but *not* by the fix this entry
proposed — that fix was investigated and found to be actively wrong,
which is the part worth not rediscovering.** The gap is real and the
consequence stated above is accurate: the derived L2 `CompileContext`
never carried a matched compile unit's own macro-controlling
forced-include header, so the header parse saw a materially different
translation unit while still reporting a real match and stamping
`parsed_with_build_context`. But routing the fix through
`ABI_RELEVANT_FLAG_PREFIXES`/`extract_abi_relevant_flags`, as this entry
proposed, would have **broken L4 replay**, which already handles forced
includes correctly and by a different route:
`source_extractors._argv.replay_extra_flags` carries
`abi_relevant_flags` through (`_carry_abi_relevant_flags`) *and*,
separately and unconditionally, re-scans the unit's raw `argv` for
forced-include/include-search tokens (`_scan_argv_for_extra_flags`,
which is deliberately **not** passed the `seen` set the first pass
builds — see that function's own docstring for why deduping there was
itself a reverted bug). Capturing a forced include into
`abi_relevant_flags` would therefore have made every L4 replay command
carry `-include config.h` twice: a silent double inclusion that a header
without include guards turns into a hard redefinition error. The general
shape is worth naming, since this list has now attracted two fixes aimed
at it: `ABI_RELEVANT_FLAG_PREFIXES` is not the only channel a compile
unit's flags reach a consumer through, so "the flag is missing from the
list" is not by itself evidence that adding it to the list is the fix —
check which consumers already reach the same fact by another route first.
Closed at the layer that actually had the gap instead:
`header_utils.forced_include_operands` is now the one shared recognizer
(the same option vocabulary and the same separate/joined spellings
`_argv`'s replay matchers use, since `match_gnu_forced_include`/
`match_msvc_forced_include` moved into that leaf — the one that already
owns this codebase's include-flag vocabulary and that both consumers
already sit above, so sharing costs no new import edge — and `_argv`
imports them), and `header_compile_context._forced_include_flags` renders its
result into the L2 command straight from `cu.argv`, never through
`abi_relevant_flags` — so L4 replay is bit-for-bit untouched. Three
rendering decisions carry their own reasoning in the code: a relative
operand is pinned to the compile unit's own `directory` **only when that
resolves to a real file** (GCC documents a two-stage lookup, and pinning
a generated header the build finds through its `-I` chain to a
non-existent path would turn a header that would have been found into a
hard "file not found"); MSVC `/FI` renders as GNU `-include`, matching
what this module already does for `-D`/`-I`/`--sysroot=`, since the
consumer is always a GNU-driver castxml/clang invocation; and
`-include-pch` (version-locked to the compiler build that produced it,
which L2's castxml-bundled/host clang is not) plus `/FU` (managed C++/CLI
`#using`, naming no C/C++ header at all) are deliberately dropped rather
than rendered. Two consequences beyond the rendering itself: a forced
include now participates in `_EffectiveContextSignature`, so two units
forcing *different* macro-controlling headers fail closed instead of
silently applying whichever grouped first (equivalent spellings of the
*same* header still agree, since the signature compares the rendered
tokens); and `header_utils.cache_relevant_operand_dirs` — the union of
`include_operand_dirs` with the new `forced_include_operand_dirs`, now
used by all three header-parse cache keys (`service._dump_elf`,
`service._attach_header_graph`, the L2 seed's own `derived_include_dirs`
return) — covers the forced header's directory, closing for this input
the same staleness class the tenth and seventeenth findings above each
had to close individually. **Eight follow-on findings from review of this
same change, every one real and every one fixed.** (1) The cache-key channel
takes directories and walks them with `iter_cache_header_files`, which is
suffix-filtered by `CACHE_HEADER_SUFFIXES` — correct for "catch transitive
includes under a search root", wrong for a file named explicitly because it
is *itself* part of the parse. A forced include routinely carries a suffix
that list does not name (`-imacros settings.def`) or none at all
(`-include generated/config`), so hashing only its parent left an edit to it
invisible while the unchanged option token kept the key identical. Fixed by
having `dumper_ast_config._cache_key` hash a non-directory entry's own mtime
directly (a strict widening — such an entry previously contributed only its
path string) and having `forced_include_operand_paths` return the file
alongside its parent; `cache_relevant_operand_dirs` was renamed
`cache_relevant_operand_paths` to stay honest about carrying both.
(2) The PE/Mach-O path never received the widened set at all:
`cli_dump_helpers.handle_non_elf_dump` binds the derived-dirs return as
`_l3_include_dirs` and discards it, since `dump_native_binary`/
`service.run_dump` exposes no `extra_hash_dirs` hook. Rather than thread one
through `run_dump`/`resolve_input` — the change this same entry's earlier
text assumed was required, carried by every caller of those — the fix is
local: `service_header_scoped._try_header_scoped_dump` folds
`cache_relevant_operand_paths(cc.gcc_option_tokens)` into its own
`deferred_dirs`, deriving the identical set from the very tokens those dirs
came from, since `handle_non_elf_dump` already passes the merged L3 context
as `compile=`. That is the same "close it where the tokens land, rather than
threading a new channel" move the tenth and seventeenth findings made, now
applied to the fourth and last header-parse cache key.
(3) A third round found the *dialect* vocabulary had drifted between the two
layers this work now shares a recognizer across:
`adapters.base._is_msvc_command` listed only `cl`/`clang-cl` while
`_argv.is_msvc_mode` (L4) also knew `dpcpp-cl` and version-suffixed drivers
(`clang-cl-20`). A CL-mode command spelled with GNU `-c` rather than `/c`
therefore read as GNU dialect on the build-evidence side, silently dropping
its `/FI` from the derived L2 context while L4 replayed it correctly. Fixed
at the cause rather than at the new call site: `header_utils.
is_msvc_driver_stem` is now the one vocabulary both use, so the fix also
reaches `_is_msvc_command`'s pre-existing consumers (source detection,
forced-language detection, structured-field masking). Only the *name* test
is shared — each caller keeps its own basename derivation, since the
adapter's is backslash-aware and `_argv`'s is not, and changing L4's would
have been a behavior change outside this PR's scope.
(4) A fourth round found the file-hashing fix in (1) still missed the case
where a forced include is resolved only through the `-I` chain
(`-I /build/gen -include config`): the bare operand stats against
abicheck's own working directory rather than the build's, and the search
directory is walked only through the suffix-filtered
`iter_cache_header_files`, which skips an extensionless or `.def` name — so
nothing hashed the real file. `forced_include_operand_paths` now also emits
one candidate per include-search directory in the same token list, whether
or not it exists. Deliberate: `_cache_key` contributes a non-existent path's
string and moves on, a candidate that *starts* existing is a real change to
what the compiler resolves, and since the search is first-match-wins a
candidate under a directory the compiler would never reach can only
over-invalidate — a spurious miss, never a stale hit, which is the correct
direction for a cache key to err in. Worth noting how this one was found:
the previous round's own new test *pinned* the unresolved bare operand as
expected output, which made a real gap read as a settled decision.
(5) A fifth round found the rendered forced include could point at nothing:
`_context_flags` emits only the structured `include_paths`/
`system_include_paths`, and the compile-DB adapter parses only
`-I`/`-isystem` into those — so a unit resolving its forced include through
an argv-only `-iquote gen` or MSVC `/Igen` had that directory in neither
field, and a bare `-include config` was emitted into a command that could
not find it. That is *worse* than the pre-existing behaviour of not
forwarding the forced include at all: a hard "file not found", or silently
the wrong same-named file. Fixed by resolving the operand against the
unit's own full search chain (`_forced_include_search_dirs`: `directory`
first, then quote/normal/system/after-system buckets, each combining the
structured field with the argv-only spellings) and emitting the absolute
path of the first match — removing the dependency on the rendered search
order rather than betting on it. **Deliberately *not* done: rendering
argv-only search dirs into the derived context.** That is the wider,
pre-existing fidelity gap the same review names — a *transitively* included
header the build reaches through `-iquote` is still unreachable to the L2
parse — but closing it changes include search order for every matched unit,
a materially broader behaviour change than this function's own correctness
requires, and it interacts with the bucket-ordering gap `_split_include_
tokens` already documents (the sixth finding above). Recorded in
`_forced_include_flags`'s own docstring as a residual.
(6) A sixth round found the same double-inclusion hazard that rules out
routing through `abi_relevant_flags`, reached from the other side:
`_merge_l3_compile_context` concatenates derived and explicit tokens
without deduplication, so a caller passing `--compiler-option -include
config.h` for a build whose compile database records the same forced header
got `-include config.h` **twice** — and an unguarded header is then
processed twice and fails to compile. Reproduced literally. This one is
worse than its arithmetic suggests, and the reason is worth keeping: the
caller passing the option by hand is precisely the one who was *working
around* the absence of this feature, so the duplicate would have broken
exactly the users the change exists to help. Fixed by
`explicit_forced_include_keys` (both caller spellings —
`gcc_option_tokens` and the free-form `gcc_options` string — each
contributing the operand as written and its resolved absolute path) with
the *derived* copy dropped on a match, keeping the established "explicit
wins" precedence and losing nothing, since both name the same file. A
*different* explicit forced header does not suppress the derived one.
(7) A seventh round found the search chain (5) added resolved through the
*wrong order within a bucket*: it sorted the argv-derived dirs, because
`_build_context_include_dirs` returns a set and determinism was the stated
goal. But a compiler takes the **first** match in a bucket in argv order, so
`-iquote z -iquote a -include config.h` resolves `z/config.h` while the
sorted chain pinned `a/config.h` — a different file, potentially different
macros, i.e. deterministic and wrong. The lesson is the trade that was made
without noticing it was a trade: determinism was available *by preserving
argv order*, so sorting bought nothing and cost correctness. Fixed by
factoring `header_utils.build_context_include_dirs_ordered` out as the real
implementation (argv order, first-occurrence-wins dedup) with the
set-returning `_build_context_include_dirs` now a thin collapse of it — every
pre-existing caller only asks "is this directory covered", so none change.
One residual, recorded at the call site: within a bucket, structured entries
are emitted before argv-derived ones rather than interleaved by true argv
position, which the structured fields do not record. It can only matter for a
command mixing spellings in one bucket (a structurally-captured GNU `-I`
alongside an MSVC `/I`), which no single real driver accepts.
(8) An eighth round (CodeRabbit) found the dedup in (6) flattened the
caller's two option spellings through its own second copy of that
flattening, unguarded — so an unbalanced quote in the free-form
`gcc_options` string made `shlex` raise `ValueError: No closing quotation`
straight out of `resolve_header_compile_context`, aborting an L2
compile-context resolution that every caller treats as best-effort, over a
malformed *caller* string. `_explicit_pin_tokens` — the same module's own
flattening for the `_ExplicitPin` scan, ten lines away — already had the
guard and already documented why ("degrades to 'no tokens from it' rather
than raising, since this is only used to *widen* what's accepted"). The
duplicate is now routed through it, so there is one flattening definition
and one degrade rule rather than two that could disagree again. Worth
naming the shape, since it is the same one as (3): a second copy of an
existing primitive drifts from it silently, and the drift shows up as the
copy lacking a property the original was deliberately given.
**Residual, deliberately unclosed:** because a
forced include still never enters `abi_relevant_flags`, it is still not
projected into a `BuildOption` by `derive_build_options`, so swapping one
build's forced-include header for another does not raise
`ABI_RELEVANT_BUILD_FLAG_CHANGED` (ADR-029 D9's build-evidence drift
signal). Closing *that* half needs a structured `CompileUnit` field every
adapter populates, a `BUILD_EVIDENCE_VERSION` bump and `build_diff`
wiring — a schema slice of its own, not a follow-on to the L2 rendering
fix, and specifically not another attempt to route it through this list.
Regression coverage: `tests/test_build_context_completeness.py` (the
recognizer, the rendered context, the ambiguity signature, the cache-key
union, and — as the executable record of the wrong fix —
`TestReplayStillEmitsForcedIncludesExactlyOnce`).

**A fifteenth finding, from a further Codex review round (P1), on the
thirteenth finding's own header-equality fix — a real gap in the fix
itself, caught before merge (fresh evidence).** Comparing only the
resolved *header* lists was not sufficient: `cli_scan.py` builds
`baseline_include = include_both + include_old` completely
independently of `baseline_header = header_both + header_old`, so a
shared, bare `-H api.h` (no `old=` scoping — passing the thirteenth
finding's own equality check) combined with side-specific `-I
old=old-build -I new=new-build` shares one header list across both
sides while still routing each through a genuinely different include
tree. The header-only check let the new side's folded `-D`/`-std`/
sysroot/include context reach a baseline parsed through a different
include scope — parsing the old binary under the new build's own
configuration risks a bad parse or a false ABI diff on the old side,
the exact failure mode this whole fix exists to prevent. Fixed by also
requiring the old side's resolved include scope to match the
candidate's own effective includes (`not baseline_includes or
list(baseline_includes) == list(eff_includes)`, ANDed with the
existing header-equality check) before reusing the fold — a genuine
`-I old=PATH`/`-I new=PATH` override on either side now falls back to
the caller's plain, unfolded context, same as a genuine `-H old=PATH`
override already did. Regression test:
`tests/test_cli_scan.py::test_baseline_compare_with_side_aware_includes_keeps_unfolded_context`
(confirmed to fail against the pre-fix — header-equality-only — code,
which forwarded the folded sentinel even though the old side's
resolved include scope diverged from the candidate's).

**A sixteenth finding, from a CodeRabbit review round (Major), on
`header_utils._INCLUDE_FLAG_PREFIXES`'s own matching itself — real,
and confirmed pre-existing for most of its consumers (present at this
PR's own base commit `dc09aec`, before any of its changes), documented
as a known gap rather than fixed here.** Every match against this
tuple — in `_has_include_build_context`, `_build_context_include_dirs`,
`_flag_tokens`, `_msvc_deferred_flag`, `include_operand_dirs`, and
`buildsource.l2_seed._split_include_tokens` alike — is exactly
case-sensitive `str.startswith`. That is correct for the GNU/clang
spellings (a real compiler only ever accepts the documented lowercase
forms), but wrong for the two clang-cl-only entries: verified against
real documentation for both drivers — native `cl.exe`'s own options are
case-sensitive (and it doesn't recognize `/imsvc` at all, a clang-cl-only
spelling), while clang-cl's own option parsing is documented
case-insensitive, so `/IMsvc`/`/EXTERNAL:I` are legal, real spellings
this tuple's exact-case matching silently fails to recognize as
include-search flags at all. Concretely: a real clang-cl build record
using `/IMsvc` would have that directory not suppress the L2 include-dir
seed, not resolve through the existing-dir dedup, and — this PR's own
two new consumers specifically — not be hashed into the AST cache key's
`extra_hash_dirs` (`include_operand_dirs`) and not be carved out of the
leading last-flag-wins token group by `_split_include_tokens`, so an
explicit `/IMsvc` override could still lose to a derived `-I` for a
colliding header. Confirmed pre-existing for `_has_include_build_context`/
`_build_context_include_dirs`/`_flag_tokens`/`_msvc_deferred_flag` — all
four already existed at commit `dc09aec` (this PR's own base) with the
identical case-sensitive matching; only `include_operand_dirs` (moved
from `l2_seed._include_operand_dirs`) is new to this PR. Not fixed here:
a correct fix needs case-insensitive matching applied *consistently* to
every one of those six consumers, not just the two this PR added — fixing
only the new ones while leaving the pre-existing four case-sensitive
would be strictly worse than today's uniform gap, making the
seed-suppression logic and the cache-hashing/ordering logic silently
*disagree* about whether a given `/IMsvc` token is an include-search
flag at all. It also needs a genuine new tie-break case-folding
introduces that a case-sensitive scan never had: `/imsvc` case-
insensitively also matches the shorter `/I` prefix, and picking the
wrong one changes which include bucket the directory is treated as (see
`_msvc_deferred_flag`'s own bucket-priority docstring) — a real, if
narrow, cross-cutting change to a shared, well-tested primitive several
pre-existing callers depend on, not a scoped fix reactive to one review
comment on this PR. Documented in `_INCLUDE_FLAG_PREFIXES`'s own
docstring alongside this entry.

**A seventeenth finding, from a further Codex review round (P1), on the
counterpart cache key the tenth finding's `_attach_header_graph` fix was
supposed to stay aligned with — real, and fixed.** `service._dump_elf`'s
own `deferred_dirs` computation (its PRIMARY header parse's cache key,
not the header-graph attach) hashed only `resolve_inferred_header_roots`'s
deferred roots, never any include-search directory riding in the compile
context's own `gcc_option_tokens` — exactly the gap the tenth finding
closed on `_attach_header_graph`'s side, left open on the primary parse
whose cache key that fix was meant to match. Editing a header under such
a directory could let the primary parse reuse a stale cached AST while
`_attach_header_graph`'s own independent second parse correctly
reparsed, producing a snapshot whose declarations and embedded graph
describe different source states — the exact divergence the tenth
finding's fix was supposed to prevent, just from the other side. Fixed
by folding `include_operand_dirs(cc.gcc_option_tokens)` into `_dump_elf`'s
`deferred_dirs` too, the identical fold `_attach_header_graph` already
applies. Regression test:
`tests/test_service_unit.py::TestDumpElf::test_gcc_option_tokens_include_dir_is_hashed`
(confirmed to fail against the pre-fix code, which never hashed the
directory into `extra_hash_dirs`).

**An eighteenth finding, from a further Codex review round (P2), on a
real ordering bug in `perform_elf_dump`'s own composition — distinct
from `_merge_l3_compile_context`'s already-fixed explicit-vs-derived
split — and fixed.** `resolve_inferred_header_roots`'s own `deferred`
tokens (the inferred `-H` header root, emitted in a search bucket that
defers below any *existing* build context) were folded into the
"explicit" side of the L3 fold purely to suppress the fold's own
internal broad include-dir seed — but `_merge_l3_compile_context` has no
way to tell a synthetic deferred marker apart from a genuine user token,
so it ranked `deferred` ahead of the L3-derived include tokens too. A
colliding generated header under the L3-derived directory could then be
shadowed by the inferred root — the exact inversion "deferred" exists to
prevent. Fixed by excluding `deferred` from the tokens passed into the
fold and appending it back at its correct lowest-priority position
(after the L3-derived includes) once the fold result is known. Verified
this doesn't break the suppression it was providing: `deferred` is
non-empty only when the *original* tokens (unmodified, still passed to
the fold) already showed build-context evidence, which the fold's own
suppression check reads directly — so nothing is lost by leaving
`deferred` itself out. Regression test:
`tests/test_non_elf_dump_l2_seed.py::test_perform_elf_dump_keeps_deferred_inferred_root_below_l3_derived_includes`
(confirmed to fail against the pre-fix code, both via an internal
assertion catching `deferred` leaking into the fold's own explicit
tokens and via the final ordering check).

**A nineteenth finding, from real Bazel/castxml CI evidence (not a
hand-built fixture) on `napetrov/abicheck-bazel-lab`'s diagnostic PR #14,
after repinning it to `abicheck/abicheck@84cf3d4` (PR #788) — a narrower
residual of this same topic survives the eighth/`_build_new_snapshot`
fold fix above, investigated but not fixed.** The lab's
`validate-two-fresh-mains.yml` workflow builds BASE and HEAD with real
Bazel, captures target-scoped cquery/aquery evidence for both, `dump`s
each fresh (`fresh-base.abi.json`/`fresh-head.abi.json`, same code,
identical `--sources`/`--build-info`), then separately `scan`s HEAD
`--against` the fresh BASE dump. `compare fresh-base fresh-head` (pure
`dump` vs `dump`) reads `COMPATIBLE`/`NO_CHANGE` with a complete
`analysis_assurance` — confirming `language_standard` parity holds, i.e.
the eighth-finding fix above genuinely works. But `scan HEAD --against
fresh-base.abi.json` (the same HEAD code, same `-H`/`--sources`/
`--build-info` inputs, `dump`'s baseline) still reads `NOT_COMPARABLE`:
`diff.reason` names exactly one differing field, `include_sequence` (not
`language_standard`, which no longer reproduces) — a narrower,
previously-undetected sibling of the field this whole topic exists to
close, confirmed via the run's own uploaded `two-fresh-mains-validation`
artifact (run
https://github.com/napetrov/abicheck-bazel-lab/actions/runs/31950549361,
job 95173233150, commit `a7f5a7f`).

**A disconfirmed hypothesis, corrected by Codex review — recorded so a
future pass doesn't re-propose it.** This entry's first draft claimed the
cause was `scan_engine._build_new_snapshot` passing a raw, unexpanded
`headers` list (still containing the directory entry) into
`seed_includes_and_fold_compile_context`/`service.resolve_input`, while
`perform_elf_dump` pre-expands via `expand_header_inputs()` first. False,
verified by reading both paths fully rather than trusting the first,
partial read: (1) `header_compile_context.resolve_header_compile_context`
— reached from *inside* `seed_includes_and_fold_compile_context`'s own
compile-context fold, not just its truthiness-only `seed_l2_includes`
half this entry's first draft checked — calls its own
`_expand_header_directories()` on the raw `headers` list, whose docstring
states it deliberately reuses `header_utils.iter_directory_headers` (the
same walk `expand_header_inputs` itself delegates to, same suffix/pruned-
segment filters) specifically so the expanded set matches what L2 actually
parses. (2) `service.resolve_input`'s own ELF dispatch, `_dump_elf`, calls
`expand_header_inputs(headers)` internally (`service.py:1281`) before
ever reaching `dumper.dump()` — the identical function `perform_elf_dump`
calls explicitly upfront, just one call-stack frame deeper. Both `scan`'s
and `dump`'s header lists therefore converge on the identical expanded,
deduped, deterministically-ordered file set before any header-AST parse
runs; a raw-vs-expanded asymmetry cannot be the cause of the `include_
sequence` mismatch.

**Two further Codex review rounds, each finding the previous round's
proposed single "the mechanism is X" narrative was itself incomplete —
pattern worth naming before the specifics: this area (`perform_elf_dump`
vs. `scan_engine._build_new_snapshot`'s relative ordering of the L3→L2
fold and `header_utils.resolve_inferred_header_roots`) has enough real
asymmetry that every single-paragraph explanation attempted so far turned
out to be a true but partial slice of it, not the whole story — so this
entry stops trying to assert one and instead lists the verified-by-
reading candidate mechanisms found, unranked, without claiming which one
(if any single one) explains the specific `include_sequence` mismatch in
the CI evidence above.** `perform_elf_dump` calls
`resolve_inferred_header_roots(headers, includes, gcc_options=
effective_gcc_options, gcc_option_tokens=tuple(gcc_option_tokens))` using
its own *pre-fold* local variables, before its later
`seed_includes_and_fold_compile_context` call folds real L3 evidence into
them; `scan_engine._build_new_snapshot` folds *first*, then passes the
already-folded `compile_context` into `resolve_input(..., compile=
compile_context)`, whose ELF dispatch `_dump_elf` makes its own internal
`resolve_inferred_header_roots(...)` call reading that already-folded
context. Two concrete, verified effects of this ordering difference, not
one:
(a) `resolve_inferred_header_roots`'s own `skip` set is built from
`user_includes` (the caller's `includes` parameter) *and*
`_build_context_include_dirs(ctx)` (dirs implied by the passed-in
`gcc_options`/`gcc_option_tokens`) — for scan's post-fold call, both of
these already carry L3-derived content, so an inferred `-H` root whose
directory the L3 evidence already covers is **skipped entirely**
(`inferred` ends up empty, the function returns `([], [])` for that
root); dump's pre-fold call has a much smaller `skip` set (no L3 content
yet), so the same root is far more likely to survive as a plain `-I`
extra-include instead. This omission is real only for the two branches of
`skip` that do **not** independently reach `extra_includes`: a
`_build_context_include_dirs(ctx)` match (a dir implied only by
`gcc_options`/`gcc_option_tokens`, never itself added to `includes`), and
the sibling deferred-token branch below. It is *not* an omission when
`skip` matches purely because the root is already literally present in
`user_includes` — `_dump_elf`'s own `eff_includes = list(includes)`
starts from that same list before `resolve_inferred_header_roots` ever
runs and is only ever added to, never filtered, so that root's slot
survives via the pre-existing `includes` entry regardless of whether the
inferred-root call re-adds it (Codex review: an earlier draft of this
entry collapsed all three skip/defer shapes into one "no slot" outcome,
which is only true for two of them).
(b) Separately, `perform_elf_dump` computes `inc_extra` from this
*pre-fold* call and only later builds `extra_includes=eff_includes +
inc_extra` for the actual `dump(...)` call, where `eff_includes` is the
(by then real) L2-seeded include list — if `inc_extra`'s root and
`eff_includes` overlap (plausible, since both can independently resolve
to the same L3-derived directory), the *same* directory can appear twice
in dump's own `extra_includes`, which is what `comparability_fields.
_include_slot_tokens` actually tokenizes into `include_sequence` — one
token per `declared_includes` slot, and `dumper_contract.
_attach_extraction_contract` builds `declared_includes` **exclusively**
from `extra_includes` (`IncludeDir(path=p, ...) for p in extra_includes`,
absent a `dump_manifest`); `gcc_option_tokens` (where a *deferred* root
rides, as `-isystem`/etc.) never contributes a slot at all (Codex review
— corrected from this entry's own prior, wrong claim that both jointly
derive slots). This sharpens rather than weakens candidates (a)/(b)
below: a root that lands in `extra_includes` always produces a slot: once
for dump's `inc_extra`, and *again* if `eff_includes` also independently
picked up the same directory (candidate (b), a real duplicate slot); a
root that `resolve_inferred_header_roots` either skips outright or
reclassifies to a deferred `gcc_option_tokens` flag produces **no** slot
at all either way, since neither reaches `extra_includes` (candidate (a),
collapsing what looked like two distinct scan-side outcomes — "skipped"
vs. "deferred" — into the same net effect on `include_sequence`). scan's
candidate side has no equivalent double-add, since its single fold call
already produced the final `includes`/`compile_context` `resolve_input`
uses directly. Either effect alone — a missing slot on scan's side, or a
duplicated slot on dump's — changes the resulting slot *count*, which is
sufficient to make `include_sequence` differ regardless of which specific
slot moved. **Not fixed here, and deliberately not narrowed to one of (a)/
(b) without more evidence**: neither this pass nor either Codex round
inspected the actual differing `include_sequence` token values from the
CI artifact (only `diff.reason`'s field name was read, not its content),
so which effect (or another one still unfound) actually fired in this
specific repro is genuinely unknown; a real fix needs that inspection (or
a live `bazel`/`castxml` repro, absent from this pass's environment)
before it can even be scoped, let alone attempted — this file's own
"known gaps over risky reactive patches" convention applies doubly here,
given how many single-paragraph "found it" claims this same footnote has
already had to walk back. Consequence for
`napetrov/abicheck-bazel-lab`: PR #14's `fresh-to-fresh` job (its real
workflow-job name) genuinely fails overall — its own
`compare fresh-base fresh-head` step passes, but its separate
`scan HEAD --against fresh-base.abi.json` step is the one that returns
`NOT_COMPARABLE` and fails the job — so it should be treated as still red
on this one residual field, and the lab's own checked-in
`abi/math.abicheck.json` should **not** be regenerated
against the new core pin yet — doing so now would just encode a baseline
that a fresh `scan --against` still can't cleanly compare to, the same
problem this diagnostic exists to catch, not fix.

**Two real mechanisms found and fixed by actually inspecting the
differing `include_sequence` token values, using a minimal, castxml-free
local repro (a `g++`-compiled one-header library + a hand-written
`compile_commands.json`, `--ast-frontend clang`) rather than a live
Bazel/castxml run — the repro this whole footnote's own "not fixed here"
note said was the missing prerequisite.** Both are real instances of
candidate (b) above (a duplicated `-I`/`-isystem` entry), reached from
two different composition points, neither previously identified:
(1) `perform_elf_dump`'s own `extra_includes=eff_includes + inc_extra`
(and `service_header_scoped._try_header_scoped_dump`'s identical
`eff_includes += inc_extra`) concatenate two independently-derived
include-dir lists with no dedup — fixed with a new
`header_utils.dedup_paths_preserve_order()`, applied at both composition
sites. (2) The load-bearing one for this specific repro:
`l2_seed._merge_l3_compile_context`'s `derived_includes` (the P0.3 fold's
own derived compile-unit include dirs) and the caller's own *explicit*
`gcc_options`/`gcc_option_tokens` can independently carry an `-I` for the
identical directory — concretely, a `--build-info` compile database is
matched *both* by this fold's own resolver *and* by the pre-existing
legacy `-p`/`--compile-db` auto-match mechanism that populates
`perform_elf_dump`'s `effective_gcc_options` string before the fold ever
runs, so the same directory reaches the merged `gcc_option_tokens` twice:
once via `explicit_tail` (the split `gcc_options` string) and once via
`derived_includes`. Confirmed via direct inspection of both
`CompileContext` objects the merge receives (`explicit.gcc_options`
literally contained `"-I <dir>"`, `derived.gcc_option_tokens` literally
contained `("-I", "<dir>", ...)`  for the same `<dir>`) — not
reconstructed from `diff.reason` alone, closing exactly the evidentiary
gap the "Two further Codex review rounds" paragraph above says was still
missing. Fixed with a new `header_utils.drop_include_tokens_duplicating_
paths()`, which drops a `derived_includes` pair whose directory already
appears among `explicit_tail + explicit.gcc_option_tokens`'s own
include-search operands — "explicit wins, searches first" is unaffected,
since only the later, redundant `derived` copy is ever dropped. Verified
end to end: the real compiler argv `dump` sends to clang no longer
repeats `-I <dir>`, and a fresh `dump` baseline's `ast_compile_args` now
matches a `scan --against` candidate's own (both carry exactly one `-I
<dir>` for the matched project directory) — see
`tests/test_build_context_completeness.py`'s
`TestDedupIncludeDirsAcrossCompositionSites`/
`TestMergeL3CompileContextDropsDuplicateExplicitInclude`.

**A Codex review round on the same PR found the first version of this
fix class-blind, and it was fixed before merge.** `drop_include_tokens_
duplicating_paths()`'s first cut matched purely on resolved directory,
ignoring which include-search *class* (`-I`/`-isystem`/`-iquote`/
`-idirafter`) each entry belonged to — but a real compiler consults
those as distinct search buckets in a fixed order regardless of argv
position (the identical class-blindness `_split_include_tokens`'s own
docstring already documents as a known gap one function over), so
dropping an `-isystem <dir>` merely because an unrelated `-I <dir>`
existed elsewhere could change which bucket that directory is searched
from for a colliding header basename — concretely, `-I A -I B -isystem
A` resolves a colliding name from `B`, while the class-blind rewrite
`-I A -I B` resolves it from `A` instead. Fixed by making the dedup key
`(flag-class, resolved directory)` rather than the directory alone, via
a new `_include_class_path_pairs()` shared by both the "already
covered" side and the token walk being filtered — see
`TestDedupIncludeDirsAcrossCompositionSites::
test_drop_include_tokens_duplicating_paths_is_class_sensitive`. Both
call sites' "already covered" argument changed shape accordingly (raw
tokens, not bare `Path`s) so the class of an *already-emitted* entry is
never lost either.

**Not fully closed — a third, deeper mechanism survives and still
reproduces `NOT_COMPARABLE` on `include_sequence` for the identical
repro, confirmed by direct inspection after the two fixes above.** Even
with both duplicate-`-I` bugs fixed, `dump`'s baseline and `scan`'s
candidate still disagree, because the matched directory reaches
`declared_includes` (the sole source `dumper_contract.
_attach_extraction_contract` builds `include_sequence`'s slots from) via
**structurally different channels on the two paths** rather than merely
differing in count: on `dump`, the legacy `-p`/`--compile-db` match
supplies the directory as part of `effective_gcc_options` *before* the
L2 seed ever runs, and `seed_l2_includes`'s own suppression rule
(correctly) declines to seed a directory when explicit context already
supplies one — so the directory reaches the parse only through
`gcc_option_tokens`, and `eff_includes`/`declared_includes` stays empty
for it. `scan_engine._build_new_snapshot` has no equivalent legacy `-p`
step at all — nothing pre-populates `effective_gcc_options` before its
own fold runs, so the identical directory reaches the *same* fold's L2
seed unsuppressed, landing in `eff_includes`/`declared_includes` instead.
Two structurally different `declared_includes` — empty vs. one entry —
for the same real include root is exactly what `include_sequence` is
built to detect, so it correctly (if unhelpfully) still fires. **Not
fixed here**: closing it needs a design decision this pass did not have
standing to make on its own — either `dump`'s CLI stops running the
legacy `-p`/`--compile-db` auto-match whenever `--build-info` already
feeds the new P0.3 fold (the two have overlapped, silently, since the
fold was introduced — this pass is the first evidence either mechanism's
*placement choice* for a resolved directory is externally observable,
not just its content), or `scan`'s candidate resolution gains an
equivalent legacy-match step so both paths agree on *which* channel
supplies a matched directory, not merely that they supply the same one.
Either is a real, cross-cutting change to which of two established
mechanisms wins for a `dump`-only surface `scan` has no counterpart to
— not a mechanical follow-up to either dedup fix above. Confirmed via
the same local repro: after both fixes, `dump`'s `ast_compile_args`
still carries `-I <dir>` (via `gcc_option_tokens`) while `declared_
includes` is empty; `scan`'s candidate carries the identical `-I <dir>`
via `declared_includes` instead — same effective compiler argv, still a
real `profile_fingerprint`/`include_sequence` disagreement.

**Re-verified end to end (2026-08-20) against the literal bug report this
whole entry was originally opened from** (a `dump --sources --build-info
--depth source` baseline compared against an unchanged codebase via
`scan --against`, reported carrying empty `ast_resolved_standard`/
`ast_compile_args` and failing as `NOT_COMPARABLE`): the reported
symptom does **not** reproduce on current `main` for a plain,
single-compilation-unit `compile_commands.json` (real `g++ -std=c++17`
build, real `clang` L2 frontend, no `-p`/`--compile-db` involved) — the
`dump` baseline's `ast_resolved_standard`/`ast_compile_args` are
correctly populated and the follow-up `scan --against` resolves
`NO_CHANGE`, not `NOT_COMPARABLE`. This confirms the accumulated fixes
above (the L3->L2 fold itself, the include-dir dedup fixes, the
class-sensitive dedup fix) already close the reported case for real —
the pinned commit the report was filed against
(`abicheck/abicheck@891bd9d7`) predates essentially all of them. Added
as a real, non-mocked end-to-end regression:
`tests/test_dump_scan_l3_comparability.py` (a real `g++`-compiled
library + `compile_commands.json`, driven through the actual `dump`/
`scan` CLI commands via `CliRunner`, not a stubbed `dump()` call the way
the existing unit coverage in `tests/test_cli_dump_helpers_coverage.py`
is) — closing the gap this entry's own earlier text noted between "the
mechanism is unit-tested" and "the reported end-to-end symptom is
verified gone." **The residual, narrower gap immediately above this
note — the legacy `-p`/`--compile-db` auto-match (`dump`-only) landing a
matched directory through a structurally different channel than the
P0.3 fold's own seeded `declared_includes`, reproduced against real
Bazel `aquery`/`cquery` evidence in `napetrov/abicheck-bazel-lab`'s own
PR #14 — is unaffected by this re-verification and remains genuinely
open** for a `--build-info` combined with an explicit `-p`/
`--compile-db`, or for real multi-target Bazel `aquery` graphs this
environment cannot reproduce (no live Bazel/castxml toolchain
available). A user hitting `NOT_COMPARABLE` after this date should
check first whether their invocation combines `-p`/`--compile-db` with
`--build-info`, or is a genuine multi-TU Bazel build — the plain,
single-compile-unit case this note re-verifies is not the culprit.

**Narrower still: the residual gap also reproduces without any Bazel
toolchain or `-p`/`--compile-db` at all — the minimal trigger is just
"the matched compile unit's own flags include an `-I<dir>` that is not
already in the caller's explicit `includes`" (2026-08-20, found while
adding generalized parity testing for this bug class, prompted by a
direct question — "how did we end up with two different behaviours,
and can this be caught generically?" — rather than by a new field
report).** A real `g++ -std=c++17 -I<dep-dir>` build, `--build-info` a
matching `compile_commands.json`, no `-p`/`--compile-db` anywhere:
`dump`'s own baseline JSON correctly carries `-I <dep-dir>` in
`ast_compile_args`, but its `contract.profile_fields.include_sequence`
reads `"[]"` — confirmed directly by inspecting the emitted JSON, not
inferred. Root cause, read from the code rather than guessed:
`dumper_contract._attach_extraction_contract` builds `declared_includes`
(the source `include_sequence` tokenizes) **exclusively** from
`extra_includes`, and for this shape the `-I<dep-dir>` reaches the
parse only through `gcc_option_tokens` (the L3->L2 fold's own derived
compile-unit include dirs), never through `extra_includes` — while
`scan_engine._build_new_snapshot`'s own candidate resolution (which
calls `service.resolve_input` directly, per this same entry's "PR C"
paragraphs below, rather than through the shared `resolve_side_snapshot`
primitive `compare`'s implicit-dump path and the typed `DumpRequest`
API both already use) seeds `eff_includes`/`declared_includes` for the
identical directory instead — two structurally different channels for
the same fact, exactly the shape this entry's own "third, deeper
mechanism" paragraph already names for the Bazel case, just reproduced
here without Bazel. Confirms `compare`'s implicit-dump path is
genuinely unaffected: comparing the same `dump` baseline against the
same live binary via `compare` (not `scan --against`) resolves cleanly
for this exact shape — the divergence is specifically between `dump`'s
CLI path and `scan`'s own candidate-resolution path, narrower than
"any comparison against a `dump` baseline." **Not fixed here** — same
reasoning as the Bazel-specific case above (a real design decision on
which of two established include-seeding channels should win, not a
drive-by patch) — but now has permanent, generalized regression
coverage rather than depending on a Bazel CI lab to notice it again:
`tests/test_dump_cli_typed_api_parity.py`'s
`test_scan_against_real_dump_baseline_is_comparable` parametrizes over
several real build-evidence shapes (plain, an added macro, this
extra-include-dir shape). Rather than a bare `xfail(strict=True)` (three
Codex review rounds on this test found real gaps in cruder versions of
this idea — see the test module's own comments for the full history),
the known-divergent shape is checked against the *exact* diagnosed
failure signature (`NOT_COMPARABLE` naming `include_sequence`) before
being treated as expected via a conditional `pytest.xfail()`; anything
else for that shape — including the gap closing entirely — fails the
test outright, forcing this note and `_SCAN_KNOWN_DIVERGENT_SHAPES` to
be updated deliberately rather than the test quietly going green. A
future regression that widens the gap to a *previously-passing* shape
fails immediately too, since only the shape explicitly listed gets any
tolerance at all. The sibling
`test_dump_cli_and_typed_api_agree_on_resolved_compile_context` in the
same module separately pins the narrower invariant that *does* already
hold across all three shapes today (`dump`'s CLI path and the typed
`DumpRequest` API path agree on `ast_resolved_standard`/
`ast_compile_args`) — so a regression in *that* invariant, which is
what #810's original literal symptom was about, is caught independently
of the `include_sequence` gap this note documents.

**The nineteenth finding's own missing prerequisite — real evidence from
the exact Bazel `include_sequence` mismatch — has now been supplied, and
the mismatch does not reproduce on current `main` (2026-08-22).**
`napetrov/abicheck-bazel-lab`'s `UPSTREAM_TO_ABICHECK.md` (2026-08-21
entry, "`abicheck scan --against` reports NOT_COMPARABLE against every
`abicheck dump` baseline") recorded exactly this: `mode: dump` and
`mode: scan` for real Bazel `//:math` evidence (`cc_library(includes =
["include"])`, verified byte-identical evidence packs across runs)
disagreeing on `contract.profile_fields.include_sequence`, deterministic,
on every CI run pinned to `abicheck/abicheck@6fb8536` (#812). Reproduced
directly, without Bazel or castxml: a `g++`-compiled library with the
identical real shape a `cc_library(includes = ["include"])` action
produces — two simultaneous `-I` search directories (the package's own
`include` dir and Bazel's always-present package/workspace-root search
path) that are both real, legitimate ancestors of the *same* physical
public header, tokenizing as two `hdrs:` slots in `include_sequence`
(`abicheck_lab/math.h` under the `include` dir, `include/abicheck_lab/
math.h` under the root — matching the committed `abi/math.abicheck.json`
baseline's own recorded fields exactly). Checked out at the reported pin
(`6fb85361c`, #812) in a separate worktree: the mismatch reproduces
there verbatim (`dump`'s baseline records `include_sequence: []` — the
P0.3 L3→L2 fold never reached the ELF `dump` CLI's header parse at that
commit — while `scan`'s candidate resolves two `hdrs:` slots for the
identical evidence, so `scan --against` correctly, if unhelpfully,
refuses the pair as `NOT_COMPARABLE`). The identical repro against
current `main` (well past #812 — the "PR C" `dump`/`scan` convergence
work chronicled throughout this same entry, `#814`/`#815`/`#817`/`#823`,
landed after the lab's pin) produces `NO_CHANGE`/exit 0 on `scan
--against`, `compare`'s implicit-dump operand, and both CLI-vs-typed-API
parity lenses alike — the `dump`/`scan` convergence work already closed
this specific shape as a side effect, not as a targeted fix for it.
Given `main` was already correct, no `abicheck` source change was made
for this finding; what was missing was permanent regression coverage
pinning this exact real-world shape (a Bazel `includes`-attribute-style
*duplicate owned include directory*, distinct from `extra-include-dir`'s
unrelated-second-header shape), so a future regression in the shared
`seed_includes_and_fold_compile_context`/`_slot_token_for_ancestor`
machinery fails a fast, deterministic test here instead of needing a
fresh Bazel CI report to notice again. Added as a fourth parametrized
shape, `"duplicate-owned-include-dirs"`, in
`tests/test_dump_cli_typed_api_parity.py`'s `_BUILD_SHAPES` — it runs
through all four existing parity/comparability tests in that module
(both CLI-vs-typed-API lenses, `scan --against`, `compare`'s
implicit-dump operand) with no `xfail`, matching every other closed
shape there. The pinned Action commit `abicheck-bazel-lab` reported
against (`6fb8536`) predates the fix entirely; upstream consumers hitting
this exact symptom need to move their pin forward past `#814`, not wait
on a new `abicheck` change.

### Depth contract, CLI vs. API/MCP — closed for real by G33 Phase 5, having first been closed as stale

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry says it is closed. service_dump_pipeline.py:62 uses enforce_requested_depth, and the scan/mcp_server modules it discusses no longer exist (git ls-files lists no mcp_server.py and no scan_engine).

Finding 1 below is now out of date in a way
worth keeping visible rather than deleting: it closed this gap on the
grounds that no service/MCP surface *promised* a depth-qualified snapshot,
so there was nothing to extend the gate to. That was true when written and
is no longer: `DumpRequest.depth` and the MCP `abi_dump`'s `depth` argument
are exactly such a promise, and `service_dump_pipeline.run_dump_request`
enforces it — the same floor, raising the Tier-2 `ValidationError` where the
CLI path raises `DumpDepthNotSatisfiedError`. Note which direction the fix
went: the gap was closed by giving the typed surface the *capability* and
the gate together, not by extending a gate to a surface that had no
capability to gate. Finding 2 is unchanged and still correct. The original
text follows.

This entry previously said PR
#601 (which adds a hard-fail `DumpDepthNotSatisfiedError` when an explicit
`dump --depth` isn't actually reached, in `cli.py`/`cli_dump_helpers.py`)
was still open, and that `abicheck/service.py`'s `ScanRequest`/
`run_scan_subprocess` and `abicheck/mcp_server.py`'s MCP tools needed the
same check extended to them once it merged. PR #601 merged 2026-07-19.
Re-checking what "extend the same check" would actually mean turned up two
separate findings, both closing this gap rather than giving it new code:
1. `check_requested_depth_satisfied` (the strict gate PR #601 added) is
   called from exactly one place, `cli._write_snapshot_output` — reached
   only by the `dump` command and one `cli_buildsource.py` snapshot-writing
   helper. Neither `service.py`'s `run_dump`/`resolve_input` nor the
   `abi_dump` MCP tool accept a `depth`/`sources`/`build-info` parameter at
   all (confirmed by reading both) — there is no service.py/MCP surface
   that promises a depth-qualified persisted snapshot for this gate to
   extend to.
2. The only place a caller *can* pass an explicit `depth=` through
   `service.py`/MCP is `ScanRequest`/`abi_scan`/`abi_estimate` — and
   `service_scan.run_scan`, the CLI `scan` command
   (`cli_scan.py`), and the MCP `abi_scan` tool all call the exact same
   `scan_engine.run_scan_core`, so they already share one evidence-contract
   implementation (`_check_scan_evidence_contract`'s pinned-depth
   `_EvidenceContractError`, ADR-037 D5) — there was never a CLI-vs-API/MCP
   disparity on the `scan` side to close, before or after PR #601.
`_validate_public_depth`'s docstring in `mcp_server.py` carried the same
stale "PR #601 open, tracked as remaining work" wording and was corrected
alongside this entry.

### `ruff format` has never gated anything, and the tree has never been formatted — CLOSED: the tree is formatted and `fmt-check` now runs in CI

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The heading says CLOSED, and that is true: .github/workflows/ci.yml:215 runs `verify.py --profile pr --only lint,fmt-check,typecheck,...`.


Two separate problems were tangled here. (1) `ruff` was pinned in two places
that disagreed: `.pre-commit-config.yaml` at `rev: v0.9.0`, and
`pyproject.toml`'s `[dev]` at `ruff>=0.3` — a floor, so CI and
`pip install -e ".[dev]"` resolved whatever was newest on the day they ran.
That is not cosmetic: 0.9.0 reports an `F811` in
`abicheck/cli_buildsource_helpers.py` that current ruff does not, so a
contributor's `pre-commit` run and CI reached different *lint* verdicts on
unmodified code. Fixed by pinning both to the same exact version, forward
rather than back (pinning to 0.9.0 would red the lint lane on existing
code), with `tests/test_toolchain_pins.py` asserting the two stay in
lockstep. (2) Separately — and *not* caused by the version skew — the tree is
not `ruff format`-clean under **any** version: 488 files under 0.9.0, 486
under 0.16.3, ~56.5k changed lines, and the diffs are near-identical across
versions (checked), i.e. the formatter has simply never been applied
repo-wide. It went unnoticed because the `fmt-check` step, though present in
`scripts/verify.py`'s `fast`/`pr`/`full` profiles, **is not run by any CI
job**: `ci.yml`'s `lint-and-types` invokes
`--profile pr --only lint,typecheck,docs-build`, no workflow runs the full
`pr` profile, and `pre-commit` is not run in CI at all. So the one consumer
that would catch it is `pixi run check` (which *does* run the whole `pr`
profile) — i.e. a pixi contributor's local gate is currently stricter than
CI. That much was **already known and deliberately tracked**, in
`tests/test_verify_profiles.py`'s `_PR_STEPS_NOT_IN_A_CI_ONLY_LIST`
(`"fmt-check": "NOT RUN IN CI — pre-existing gap, tracked here"`); what this
entry adds is the *measurement* of what enabling it would cost, and the
finding that the gap is not version skew.

**Closed.** The deferred reformat was done in its own PR, as that plan
specified. Two things had changed since the measurement above and both
argued for doing it then rather than later: the drift was *growing* (486
files on 2026-09-11, **625 files / 58,564 diff lines** on 2026-09-13 —
+139 files in two days, because nothing enforced it on new code), and the
open-PR count was down to two, so the "conflicts with every in-flight
branch" cost was at a local minimum. `ci.yml`'s `lint-and-types` job now
runs `--only lint,fmt-check,typecheck,docs-build`, and the two markers that
tracked the gap (`_PR_STEPS_NOT_IN_A_CI_ONLY_LIST`'s `fmt-check` entry and
this paragraph's deferral) are gone. A green CI run now does say something
about formatting.

Two things worth not rediscovering. (1) The reformat was verified
semantics-preserving by `ast.parse` + `ast.dump` comparison of every one of
the 624 changed `.py` files against its committed version, rather than by
trusting the formatter: 622 were byte-identical in AST, and the two that
differed are benign docstring-text changes (`""""What is left"` gains the
space ruff inserts to disambiguate a docstring opening on a quote). (2)
`abicheck/model/change_catalog/kinds.pyi` is excluded from formatting in
`ruff.toml`'s `[format]` section, and the exclusion is load-bearing rather
than cosmetic: it is generated by `scripts/gen_changekind_stub.py`, whose
`--check` mode (a required gate) fails on any byte it would not itself
write. Formatting it rewrites the quotes and wraps the six members whose
slug pushes the line past 88 columns, so `gen_changekind_stub.py --check`
and `fmt-check` could not both pass. Teaching the generator to emit the
wrapped form was considered and rejected — it would be a second,
hand-maintained copy of `ruff format`'s line-breaking rules, drifting on
every ruff bump. Lint still applies to the stub; only formatting is
excluded.

### `scan --config <path>` can execute an untrusted `build.query` even when `resolve_effective_allow_query` (ADR-037 D4 "level-implies-query") denies authorization — confirmed real and confirmed pre-existing (CodeRabbit review on #817, fresh evidence, traced through git history rather than assumed)

**Status (re-verified 2026-10-09 at `456989f`): MOOT.** The scan command and cli_scan_helpers.resolve_effective_allow_query were deleted (ADR-068): git ls-files has no cli_scan* or scan_engine. buildsource/embed.py:162-163 now requires build_config_explicit for query trust.

`cli_buildsource.embed_build_source()`'s own
`cfg_trusted_for_query = build_config is not None or build_query is not
None` computation treats a merely-non-`None` `build_config` path as
sufficient authorization to run that config's `build.query` key
(`buildsource/inline.py`'s `collect_inline_pack`, gated on
`build_config_trusted_for_query`) — it has no way to distinguish "a config
path was supplied" from "querying was actually authorized." That trust
model is intentional for `dump`/`compare` (an explicit, operator-supplied
`--config` is deliberately trusted to execute its own `build.query` —
see `embed_build_source`'s own inline comment), but `scan` layers a
stricter, independently-designed rule on top
(`cli_scan_helpers.resolve_effective_allow_query`: the config must both
declare `build.query` *and* have an explicitly-pinned deep evidence level)
that this shared function's own trust check never consults. Confirmed
pre-existing, not introduced by PR #817 or by #814's "PR 3A convergence"
it merged: at commit `551725e` (immediately before #814),
`scan_engine._build_new_snapshot` already called
`embed_build_source(build_config=build_config, allow_build_query=
allow_build_query, ...)` directly, passing the raw `--config` path
unconditionally, independent of `allow_build_query`'s value — the
identical bypass already reachable there. `service_input_resolution.
_gated_build_query_inputs`'s `build_config_locally_trusted` parameter
(added by #814) was built explicitly to preserve that pre-migration
behavior for `scan` (see its own docstring), not to introduce it. **Not
fixed here**: a correct fix needs `embed_build_source`/
`collect_inline_pack` to accept an authorization signal genuinely
distinct from "was a config path given" — threaded through two
independent call sites (the L2 seed's own pack-arg resolution in
`l2_seed._resolve_l2_seed_pack_args`, and the L3-L5 embed step in
`embed_build_source` itself) — verified against real `scan --config`
scenarios where the config sets `build.query` but no deep evidence level
is pinned. That is a real, cross-cutting change to a shared trust
primitive `dump`/`compare` also depend on, not a one-line change to
`_gated_build_query_inputs`'s gate. Filed here per this file's own
"known gaps over risky reactive patches" convention rather than attempted
under review pressure on an unrelated PR.

### PR C (typed `dump`/`scan` convergence, CLI cleanup phase two's PR 3A) — investigated in depth; one real, scoped, verified slice landed; the full convergence the plan describes is NOT attempted, and here is exactly why

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The convergence this entry says was blocked has now landed. `render_dump_dry_run` takes a `ResolvedDumpRequest` (abicheck/cli_dump_helpers.py:656-657), and `service_dump_pipeline.execute_dump_request` exists (abicheck/service_dump_pipeline.py:399). `perform_elf_dump` is gone; header_conditionals.py:1303 records 'Track 1 deleted the dead perform_elf_dump'. The scan side (scan_engine.py, item 2) was removed under ADR-068, and the entry's own updates mark the remaining residuals (blockers 4/5/6 and explicit-`--config` parity) closed.


The plan (`docs/contribute/plans/cli-cleanup-phase-two.md`, "PR 3A") asks
for one canonical path -- `Click parsing → DumpRequest → ResolvedDumpRequest
→ dry-run-or-execute → DumpResult` -- with **both** `dump_cmd`/
`perform_elf_dump` and `scan_engine._build_new_snapshot` routing through
`service_dump_pipeline.run_dump_request` (or the per-input primitives it
shares via `service_input_resolution.resolve_side_snapshot`), the way
`compare`'s implicit-dump operand already does, with `dump --dry-run`
rendering a real `ResolvedDumpRequest` object -- the resolve-only step
execution builds on, not the executed `DumpResult` itself (a `--dry-run`
that renders `DumpResult` would have to have already executed, which
contradicts its own never-executes contract; see the plan's PR 3A section,
"ResolvedDumpRequest and DumpResult are two distinct objects") -- rather
than a separately-computed preview. Read `run_dump_request`, `resolve_side_snapshot`
and siblings, `perform_elf_dump` (1999 lines), `handle_non_elf_dump`, and
`scan_engine._build_new_snapshot` in full before concluding this.

**Three independent, concrete reasons the full migration cannot be done
soundly in one pass, each confirmed by reading the actual code rather than
assumed:**

1. **`dump --dry-run` is not a projection of a resolved object today -- it
   is a hand-written second implementation.** `cli_dump_helpers.
   render_dump_dry_run()` re-derives the resolved depth/collect-mode/
   compile-DB-match/backend from the *same raw inputs* `perform_elf_dump`
   receives, independently, in its own function -- there is no shared
   `ResolvedDumpRequest` either one builds from. Its own docstring is
   explicit about the scope this implies: "Cheap, read-only resolution
   only ... Never runs castxml/clang, a build query, or any I/O beyond
   stat()/PATH lookups" -- i.e. it is *deliberately* a cheaper, narrower
   re-implementation, not a dry pass of the same resolver the real run
   uses. **Half-closed by the slice landed below**: `resolve_dump_request`
   now IS a real "resolve without executing/writing" mode --
   `service_dump_pipeline.resolve_dump_request`/`ResolvedDumpRequest`,
   stopping before any castxml/clang invocation or write, exactly what
   this blocker originally said didn't exist. What remains open is purely
   the wiring: `render_dump_dry_run()` has not been migrated to build from
   a real `resolve_dump_request()` call in place of its own independent
   re-derivation -- the capability exists, nothing consumes it yet.
2. **`perform_elf_dump` has real post-processing hooks with no equivalent
   in `run_dump_request` at all.** After the primary header-AST/DWARF
   snapshot, it runs, in a carefully established order: the ADR-039
   build-context collector (`_attach_build_context`), the G31
   `service._attach_header_graph` second pass (its own independent clang
   re-invocation and AST cache key), and the optional
   `ABICHECK_CLANG_LAYOUT_TOOL` clang-layout-tool attach -- each reusing
   the L3→L2-folded `effective_compile_context` (PR #782) and each with
   its own dedicated, hard-won correctness fixes recorded above in this
   same "Known gaps" section (findings 9, 10, 17, 18 on the L3→L2-fold
   entry alone). `run_dump_request` has no post-processing stage at all
   -- it returns whatever `resolve_side_snapshot` produced. Routing
   `perform_elf_dump` through it would mean either dropping these passes
   (a real snapshot-completeness regression) or adding an equivalent
   hook to `run_dump_request` and re-verifying all four passes'
   already-fixed ordering/cache-key/flag-isolation bugs against the new
   call shape -- itself a project the size of this whole PR, not a
   rename.
3. **`scan_engine._build_new_snapshot` has scan-specific behavior
   `DumpRequest` cannot express, and this file already documents two
   multi-round bug hunts in exactly this area that a rushed reroute
   would risk reopening.** Its own `-H old=PATH`/`-I old=PATH` side-aware
   baseline handling (the twelfth/thirteenth/fifteenth findings on the
   L3→L2-fold entry above) decides, per comparison, whether the
   candidate's own L3-folded `compile_context` may be reused for the
   baseline parse or must fall back to the caller's unfolded one -- a
   decision inherently about *two* snapshots' relationship, which
   `DumpRequest` (built for *one* input) has no field for. Forcing this
   through a `DumpRequest`-shaped call would need a new pair-aware
   concept alongside it, which is exactly the kind of design `service_
   compare_pipeline.py`'s own module docstring already explains was
   deliberately kept *out* of `service_input_resolution.py`'s per-input
   primitives ("The pair-shaped decisions deliberately stayed behind...
   neither means anything for a lone dump").

**What landed instead, safely and independently of all three blockers
above: `service_input_resolution._seeded_includes`/
`_seeded_compile_context` -- the shared per-input primitive `compare`'s
implicit-dump operand and `dump`'s typed API (`run_dump_request`) both
already use -- ran the L2 include-dir seed and the P0.3 L3→L2
compile-context fold as two independent calls, each capable of running
`buildsource.inline.collect_inline_pack()`.** That is the exact
self-deadlock shape already found and fixed, by name, for `perform_elf_
dump`/`handle_non_elf_dump`/`scan_engine._build_new_snapshot` in this same
section's L3→L2-fold entry (its "fifth finding"): a caller whose
`sources`/`build_info` genuinely needs the zero-config *inferred*
build-system query would have the include-dir seed's own inferred query
hold the deterministic build-dir lock until its cleanup runs --
deliberately deferred until after the L2 parse consumes the seeded dirs --
so the compile-context fold's own, separate inferred-query attempt would
contend on the identical lock. That fifth finding's fix,
`buildsource.l2_seed.seed_includes_and_fold_compile_context()`, was wired
into all three CLI-side resolvers at the time -- but never into
`resolve_side_snapshot`, the fourth, typed-API call site, which kept the
older two-call shape. `resolve_side_snapshot` never actually hit the
600s timeout (`collect_mode` is forced `"off"`/`allow_inferred_build_
query=False` here, matching every Tier-2 API caller's "never execute a
build system as a side effect" rule -- see `_seeded_includes`'s own
docstring), so this was real, avoidable duplicated work and a real
divergence from the one shared primitive the other three call sites had
already converged on, not a live self-deadlock. Fixed by replacing the
two separate helpers with one, `_seeded_includes_and_compile_context()`,
which calls the identical `seed_includes_and_fold_compile_context()` the
other three sites already use. This is a genuine, if narrow, piece of PR
3A's actual convergence goal -- one fewer place a change to how an input
resolves can drift -- landed without touching any of the three blockers
above. Verified via the existing `resolve_side_snapshot`/
`_seeded_compile_context` test coverage in `tests/test_header_compile_
context.py` and `tests/test_bazel_root_targets_l2_seed.py` (both updated
for the renamed/merged function, not weakened), plus the full fast unit
suite and `mypy`/`ruff` clean on both touched modules.

**What remained genuinely open at the time this paragraph was written,
and why forcing it further would have been reactive rather than sound**:
all three blockers above, in full -- a "resolve without executing" mode
for `run_dump_request`, a post-processing hook `perform_elf_dump`'s
second-pass attaches can plug into, and a pair-aware primitive `scan`'s
baseline-reuse decision can express -- are each their own real,
multi-file design, not a follow-up edit to this same PR. Given the
density of prior review rounds already recorded against this exact code
(the L3→L2-fold entry above alone lists eighteen numbered findings,
several reverted-and-refixed), attempting any of the three under
continued session pressure risked reopening one of them, which is
precisely what this file's own "known gaps over risky reactive patches"
convention exists to avoid. **Superseded for blocker 1 by the slice
below**, landed the same day: `resolve_dump_request` now provides that
"resolve without executing" mode, so only the wiring (migrating
`render_dump_dry_run()` to build from it) remains open for blocker 1;
blockers 2 and 3 are still fully open, unchanged. See the plan doc's own
PR C section for a status note recording the same scope.

**A second, narrow slice landed (2026-08-18): the first blocker's missing
primitive now exists, though nothing consumes it yet.** `service_dump_
pipeline.py` gained `ResolvedDumpRequest`/`DumpResult` (additive
dataclasses) and split `run_dump_request` into `resolve_dump_request()`
(validation + evidence resolution, no castxml/clang, no write) and
`execute_dump_request()` (the actual `resolve_side_snapshot` call, the
dependency walk, the depth floor). `run_dump_request` itself is now a
literal composition of the two (`execute_dump_request(resolve_dump_
request(request)).snapshot`) and keeps its existing signature and return
type unchanged — no breaking-API decision needed, confirmed by two Codex
review rounds on the design before it was coded (see the plan doc's PR C
section for what those rounds caught: `ResolvedDumpRequest` and
`DumpResult` must stay genuinely distinct objects — a `DumpResult`
carrying a real storage result has, by construction, already executed, so
it cannot also be what a read-only `--dry-run` renders; and the achieved
effective depth belongs on `DumpResult`, not `ResolvedDumpRequest`, since
`fold_dump_provenance_into_dict` derives it from the completed snapshot
and a resolve-only object has none to derive it from). **This closes only
the first blocker's missing capability, not the blocker itself**:
`cli_dump_helpers.render_dump_dry_run()` is still the independent,
hand-written second implementation it always was — migrating it to build
from a real `resolve_dump_request()` call is unattempted, and blockers 2
and 3 (the post-processing hooks, the pair-aware scan baseline decision)
are both still fully open, for the identical reasons already given above.
Verified via new direct tests on the split itself
(`tests/test_typed_dump_request.py::TestResolveExecuteDumpRequestSplit` —
the resolve step never reaches `resolve_input`, the two-step path
produces the identical snapshot `run_dump_request` does, the depth floor
raises only at execute time, and `DumpResult.effective_depth` matches
`_gated_source_label` computed the same way `fold_dump_provenance_into_dict`
already does), the full existing `test_typed_dump_request.py`/
`test_header_compile_context.py`/`test_clang_header_backend_integration.py`
suites (unchanged, still green), the full fast unit suite, and
`mypy`/`ruff` clean on both touched files.

**Re-investigated (2026-08-19): the dry-run migration (blocker 1) is
larger than "wire the renderer to `resolve_dump_request()`" —
`dump_cmd` has no `DumpRequest` to resolve in the first place, on
either branch.** Read `cli.py`'s `dump_cmd` in full, not assumed: it
never constructs a `DumpRequest` object anywhere (confirmed by grep —
no `DumpRequest(` call site exists in `cli.py` or `cli_dump_helpers.py`
today). Its real resolution path is two CLI-only helpers,
`resolve_dump_collect_context`/`resolve_dump_compile_context`
(`cli_dump_helpers.py`), computing `collect_mode`/`header_backend`/
`includes`/`gcc_option_tokens` directly off raw Click parameters,
entirely independent of `service_input_resolution`/
`service_dump_pipeline.resolve_dump_request` — and this is not a
dry-run-only path: those same locals feed the real `dump()` call a few
hundred lines later in the same function, on the non-dry-run branch.
So migrating `render_dump_dry_run` to build from a real
`ResolvedDumpRequest` is not an isolated renderer change: it requires
first constructing a `DumpRequest` from `dump_cmd`'s ~30 CLI
parameters (matching Click's own parsing precisely — including the
`_resolved_compile_context`/`_resolved_collect_mode`/`_resolved_
include_labels`/`_resolved_lang_explicit` private hooks `compare`'s own
`ctx.invoke` already threads through this same command for its
implicit-dump operand), and doing so for *both* branches at once, so
the preview and the real run cannot silently diverge the moment only
one of them migrates. That is blocker 2 restated from the other
direction: the real run cannot move to `execute_dump_request()`
without the post-processing hooks blocker 2 already names, and the
dry-run preview cannot honestly move to `resolve_dump_request()` alone
while the real run it previews keeps using a wholly different resolver
— a preview built from one resolver describing an execution built from
another would be strictly worse than today's "two independent
implementations, kept in sync by hand," since it would *look*
authoritative without being connected to what actually runs. Not
attempted here, for the same reason blockers 2/3 were not: a real,
cross-cutting redesign of `dump_cmd`'s ~250-line resolution section
(`cli_dump_helpers.py` is already at its 2000-line AI-readiness hard
cap, so any new shared surface needs a sibling module, not an inline
addition), not a follow-up to the already-landed `resolve_dump_request`/
`execute_dump_request` split — that split remains real, additive
progress in its own right, just not yet consumed by `dump_cmd`.

**Slice landed (2026-08-19, same session): `service_input_resolution.
SideResolution`/`_resolve_side_snapshot_impl`, plus two newly-found,
narrower blockers on the next step.** `_resolve_side_snapshot_impl`
(the real implementation behind `resolve_side_snapshot`, now a one-line
wrapper) returns the P0.3 fold's own effective `includes`/
`CompileContext` — previously computed inside `resolve_side_snapshot`
and discarded after use — as a new `SideResolution` object;
`service_dump_pipeline.DumpResult` surfaces the same two fields,
populated by `execute_dump_request`. Purely additive, zero behavior
change for every existing caller, fully tested
(`tests/test_typed_dump_request.py`). This is real progress toward "one
shared primitive," but attempting the next step — routing
`perform_elf_dump`'s primary parse through it — found the two are not
simply duplicate callers of one function: `perform_elf_dump` receives an
*already-resolved* `debug_info_path` from its caller (`dump_cmd`, via
`_resolve_debug_artifact`), while `service._dump_elf` (which
`_resolve_side_snapshot_impl` reaches through `service.resolve_input`)
has no such parameter at all and independently *re-derives* the
identical fact from raw `debug_roots`/`enable_debuginfod`/
`debuginfod_url`. Merging the two needs the two debug-artifact
resolutions confirmed equivalent first — a real, separate investigation,
not a follow-up edit. A parallel check of `scan_engine._build_new_
snapshot` (which already calls `service.resolve_input` directly, so it
doesn't share this particular ELF-pipeline divergence) found two
different, narrower blockers instead: it passes `symbols_only`/
`debug_presence_only` to `resolve_input`, which `_resolve_side_snapshot_
impl` never threads through yet (a straightforward additive gap); and
its `embed_build_source` call constructs `public_headers` differently
from `embed_side_build_source`'s own construction (`_expand_public_
headers` over the combined headers+dirs list, vs. the shared wrapper's
separate, unexpanded treatment) — a genuine behavioral difference
needing reconciliation, not just a naming one. Neither was attempted
this session, per this file's own "known gaps over risky reactive
patches" convention, given this exact code area's extensive prior
history of exactly this shape of subtle divergence (18+ numbered
findings on the L3→L2-fold entry above). **PR 3A's full convergence
remains open; PR 3C (the "PR F" removal of `dump --build-query`/
`dump --build-compile-db`) stays blocked on it**, per the plan doc's own
explicit ordering requirement — see `docs/contribute/plans/cli-cleanup-
phase-two.md`'s PR 3A section for the equivalent, fuller account.

**Slice landed (2026-08-20): both "narrower blockers" above closed, plus
the debug-artifact-resolution question this entry raised confirmed
equivalent — no code change needed for that half.** `perform_elf_dump`
has exactly one caller (`dump_cmd`, confirmed by grep), and `dump_cmd`
never sets `symbols_only`/`debug_presence_only` (`dump` has no such
flags at all — only `scan`/`compare` do), so `_dump_elf`'s extra `not
symbols_only and not debug_presence_only` gate around its debug-artifact
resolution is vacuously true for every input `perform_elf_dump` can
actually pass it; the remaining textual differences (`debug_roots` vs.
`list(debug_roots) or None`, `click.echo` vs. `notify`, `if artifact:`
vs. `if artifact is not None`) are all behaviorally inert (the resolver
backends already do `list(debug_roots or [])` internally, and a
`DebugArtifact` instance is always truthy). `symbols_only`/
`debug_presence_only` now thread through `resolve_side_snapshot`/
`_resolve_side_snapshot_impl` into `service.resolve_input`, both
defaulting `False` so every pre-existing caller is unaffected — the same
additive shape as the existing `changed_paths`/`allow_build_query`
pass-throughs (`tests/test_header_compile_context.py::
test_resolve_side_snapshot_forwards_symbols_only_and_debug_presence_only`,
confirmed to fail pre-fix with `TypeError: unexpected keyword argument
'symbols_only'`). The `public_headers` construction divergence:
**a fix was attempted and merged, then reverted the same day after
review caught a real regression it missed — kept here in full because
the first pass's reasoning was genuinely incomplete, not just
under-tested.** The first pass read one consumer of `embed_build_
source`'s `public_header_roots` (`source_extractors._argv.
split_public_roots`/`_ClassifyContext.classify()`) and confirmed a
directory root already classifies every file under it via segment/
prefix matching, so the `_expand_public_headers`-based expansion looked
purely redundant *against that consumer* — and switched
`_build_new_snapshot`'s call to the simpler, unexpanded raw pass-through
`embed_side_build_source` already uses. That missed a **second,
differently-shaped consumer of the same list**:
`clang_public_roots._equivalent_public_roots_for_unit`, the
install-tree-vs-build-tree "mirror detection" heuristic L4 replay uses
when a public root names a physically different tree from the build's
own include dir. Its promotion rule is asymmetric by root shape: a
*file* root promotes on a single sampled match; a *directory* root
needs `>= _PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES` (2) matches before
promoting the whole directory — so a build include dir mirroring only
ONE header out of a larger public root loses that promotion entirely
once the directory stops being pre-expanded, confirmed by direct
reproduction against the function itself (three installed headers, one
mirrored in the build tree: expanded file roots promote it, a single
directory root promotes nothing). `embed_side_build_source`'s own raw
pass-through (already shipped, used by `compare`/`dump`) carries the
identical weakness — not fixed here, since unifying either direction
changes real classification behavior for a real consumer, and deciding
which needs its own scoped design, not a same-PR revert-and-redo.
Reverted `_build_new_snapshot`'s call back to the expanded shape and
pinned two regression tests: the call's own shape (`tests/
test_scan_l2_cleanup_ordering.py::
test_scan_candidate_expands_public_header_dirs_before_embed`) and the
underlying asymmetry directly against `_equivalent_public_roots_for_unit`
itself (`tests/test_clang_public_roots_coverage.py::
test_equivalent_public_roots_promotes_on_single_match_only_for_file_roots`),
so a future "simplify this like the other primitive" pass doesn't
silently reintroduce the same regression. **Neither fix routes
`_build_new_snapshot` through `_resolve_side_snapshot_impl` itself** —
they make that future migration safe, they don't perform it.
Investigating the migration surfaced one more wrinkle this entry hadn't
named: `_build_new_snapshot`'s own `allow_build_query` gates only its
`embed_build_source` call, never its `seed_includes_and_fold_compile_
context` call (which always passes `build_query=None, build_compile_
db=None` — `scan` has no such CLI flags to begin with), whereas
`_resolve_side_snapshot_impl`'s `_gated_build_query_inputs` gates both
from one shared decision; reconciling that needs confirming what
`_build_new_snapshot`'s `allow_build_query` is actually meant to
authorize today, before the two functions' gating can be safely
unified. Blockers 4 (post-processing hooks) and 5/6 (`dump_cmd` building
a real `DumpRequest`; a pair-aware scan-baseline primitive) remain fully
open, unchanged from the notes above — see the plan doc's own PR 3A
section for the identical, fuller account.

**Slice landed (2026-08-21): the ADR-039 collector gate is now one shared
function all three resolvers call, which closed a real dump-vs-scan
asymmetry nobody had named — and attempting blocker 5 next turned up three
concrete obstacles the notes above do not mention.** Re-reading the three
resolvers side by side found that `scan_engine._build_new_snapshot` never
ran the ADR-039 build-context collector **at all** (the ELF `dump` CLI
always had; the typed pipeline gained it in PR #809), so `scan --against`
a `dump`-produced baseline compared a candidate with no
`build_context_defines`/`conditional_fields` against a baseline carrying
both — and the reconciler could clear a context-free header-parse false
positive (a `#ifdef`-guarded record field the context-free parse pruned)
on the baseline side but not on the candidate's. Fixed at the gate rather
than by writing a fourth copy of it:
`header_conditionals.attach_build_context_for_parsed_headers` now owns the
compile-DB resolution (from an already-resolved path *or* from
`build_info`), the best-effort header expansion a directory `-H` entry
needs, the `snap.from_headers` check, and the caller-supplied
`live_elf_parse` answer; `perform_elf_dump`,
`_resolve_side_snapshot_impl`, and `_build_new_snapshot` all call it.
`perform_elf_dump` gains `from_headers` in the process — the same gate its
own sibling `parsed_with_build_context` stamp ten lines above already
applies, for the recorded reason that a `--dwarf-only` run explicitly
ignores `-H`. A latent ordering bug was fixed alongside:
`_resolve_side_snapshot_impl` drained the L2 seed's cleanups only *after*
`embed_side_build_source`, so an inferred build query's lock was still
held when the embed ran its own — the self-contention this entry's own
fifth finding records for the CLI resolvers. Unreachable today (the seed's
`collect_mode` is pinned `"off"`), fixed now because it springs on
whichever PR relaxes that pin, which is what migrating the CLI resolvers
means. Tests: `tests/test_scan_adr039_build_context.py` (7 cases; the
three positive ones confirmed to fail against the pre-fix
`scan_engine.py`) and `tests/test_typed_dump_request.py::
TestSeedCleanupsDrainBeforeTheEmbedStep` (confirmed to fail pre-fix).

**Blocker 5's three obstacles, each verified rather than assumed** (full
account in the plan doc's PR 3A section): (a) `InputSpec.path` is a
*required* field and `dump`'s source-only branch (`dump --sources ./tree`
with no SO_PATH) has no path, while `--dry-run` runs before that dispatch
— so "both branches build from one `DumpRequest`" is unreachable for that
shape until `InputSpec` can express "no binary", a public typed-API model
change reaching every consumer. (b) The two collect-mode resolvers
genuinely disagree, measured directly: they agree for *every* explicit
`--depth`, and the no-inputs case is unobservable, but **`--build-info`
with no `--depth` is `source-target` on the CLI
(`resolve_dump_collect_context`) and `build` through the typed path
(`collect_mode_for`)** — so taking the collect mode from
`resolve_dump_request` would silently stop a `dump --build-info <pack>`
at L3 that attempts L4 today. Which default is right is a product
decision, not a mechanical reconciliation. (c) The ELF `dump` CLI embeds
L3–L5 at *write* time (`cli_buildsource._write_snapshot_output`, together
with the G21.7 warning, the Flow-2 `--inputs` fold, the depth gate and
the provenance fold) while `execute_dump_request` embeds at *resolve*
time inside `_resolve_side_snapshot_impl` — routing the real run through
the typed executor **embeds twice**, re-running L4 replay, unless the
write path is restructured in the same change. Note what (b) implies for
a "just migrate the dry-run first" shortcut: it would report a collect
mode the real run does not use, which is worse than today's two
hand-synced implementations.

**One more verified divergence, not fixed:** `scan`'s
`embed_build_source` call passes no `extractor`, taking that function's
`"auto"` default, and `buildsource.inline._make_source_extractor` treats
anything but a literal `"castxml"` as clang — while every other resolver
passes `service_compare_evidence.effective_frontend(...)`, which resolves
`"auto"` to **castxml** (`dumper._resolve_header_backend`, no availability
fallback). So `dump --depth source` and `scan --depth source` over one
project at their defaults replay L4 through *different extractors*, and a
`scan --against` a `dump` baseline compares source-ABI facts from two
different tools — precisely what `effective_frontend`'s own docstring says
it exists to prevent. Making `scan` match would newly require castxml for
a scan that works with clang today: a real behavior change for real users,
unverifiable without a castxml-capable environment, so it needs its own
slice rather than a same-pass patch. **Re-checked 2026-08-21: castxml is
still absent from this environment, so this stays a documented gap rather
than a guessed fix.**

**Blockers 5 and 6 closed (2026-08-21, later the same day) — `dump_cmd`
now builds one real `DumpRequest`, and the pair-aware baseline rule lives
in one primitive. The real runs are deliberately still not migrated.**

*Blocker 5* was three sub-issues, each closed at the layer that had the
gap rather than at the call site that noticed it. (a) `InputSpec.path` is
now `Path | None` — a pure widening, so no existing caller changes — with
"which requests may leave it `None`" enforced once, per request type, in
`validation_errors()` (never for `CompareRequest`; for `DumpRequest` only
alongside real `sources`/`build_info`/`dump_manifest`), and
`api_types.required_path` as the single place the narrowing is spelled
rather than seven defensive call sites. The `dump_manifest` clause is
worth recording because it was found the right way round: a first revision
named only `sources`/`build_info` and broke `dump --dump-manifest
m.yaml --dry-run` (no SO_PATH), caught by the *existing*
`tests/test_cli_dump_manifest.py` — which is precisely the "the model
cannot express what the CLI accepts" gap the widening exists to close.
(b) The CLI-vs-typed collect-mode disagreement (`--build-info` with no
`--depth`: `source-target` on the CLI, `build` through the typed path)
is resolved in favour of the CLI's older, documented default, via a new
`service_compare_evidence.dump_collect_mode_for`. `collect_mode_for` is
**unchanged** — `compare`'s own front end genuinely infers omitted depth
from its inputs, which is a different question, and changing it would have
been the easy wrong fix. Pinned by
`tests/test_dump_collect_mode_parity.py` against the *real* CLI resolver
over the whole `(depth, sources, build_info)` grid. (c) The write-time
embed is now idempotent: `cli_buildsource.build_source_already_satisfies`,
stated through the same `_missing_requested_evidence_layers` the
neighbouring G21.7 warning already trusts, so the guard and the warning
cannot disagree about what "satisfied" means; its `pack is None -> []`
case is deliberately *not* satisfaction, which is what keeps it a no-op
for today's CLI (`tests/test_dump_embed_idempotence.py`, including an
`integration` end-to-end count proving one real `dump --depth source`
embeds exactly once).

With those closed, `abicheck/cli_dump_request.py` builds one `DumpRequest`
from `dump_cmd`'s parameters and `--dry-run` renders from a real
`ResolvedDumpRequest`. **The half-migration hazard the plan names — "a
preview built from one resolver describing an execution built from another
is worse than two hand-synced implementations, since it looks
authoritative without being connected to what actually runs" — is answered
structurally, not asserted.** The request is fed the CLI's
*already-resolved* values (compile context, frontend, explicit-language
decision) rather than re-deriving them, so it records the run; and the
fields the pipeline *does* derive independently are pinned equal to the
CLI's own by `tests/test_dump_request_from_cli.py::
TestResolvedRequestAgreesWithTheCliLocals`. Sub-issue (b) was a
prerequisite for exactly that: without it the preview would have reported
a collect mode the real run does not use. One user-visible consequence,
stated rather than left to be discovered: `DumpRequest.validate()`
front-runs `dumper.dump()`'s own runtime rejection of `--dump-manifest`
combined with `-I`, so that combination is now a usage error in the dry
run too — inside the dry-run contract, which permits usage errors.

*Blocker 6* is `service_input_resolution.BaselineReuseContext` /
`resolve_baseline_compile_context`: the "may the candidate's folded
context also parse the baseline" rule, extracted from the four-clause
boolean inline in `scan_engine.run_scan_core` that the twelfth, thirteenth
and fifteenth findings above each had to correct in turn. `run_scan_core`
calls it today; `_resolve_side_snapshot_impl` accepts the same object as
an **optional** `baseline_reuse_hint` and reports the identical answer on
`SideResolution.baseline_compile_context`, so the migration that finally
routes `_build_new_snapshot` through the shared resolver inherits the rule
instead of reimplementing it a fourth time. Deliberately an opt-in hint,
not a widening of `resolve_side_snapshot`'s single-input contract — a
caller that passes none is bit-for-bit unaffected. Given that correction
history, it is tested as a primitive rather than only through `scan`
(`tests/test_baseline_reuse_context.py`), per this file's own
"Primitive-level property tests" guidance: the contract as invariants,
the resolver-agrees-with-its-own-predicate property, and a pin that
include *order* matters (search order is first-match-wins, so a "compare
as sets" simplification has to argue with a test rather than pass
silently).

**Still open, unchanged:** neither real run routes through the shared
pipeline. `dump` executes through `perform_elf_dump`/
`handle_non_elf_dump` and `scan`'s candidate through `service.
resolve_input`/`embed_build_source` directly. What blocks each is now
concrete rather than open-ended — the ADR-039 collector's CLI-only inputs
(`--compile-db-filter`, the raw `effective_compile_db`) need typed-API
representation, and `_write_snapshot_output`'s provenance/`--inputs`/
depth-gate sequence needs reordering around a resolve-time embed — but
each is its own slice. PR 3C (removing `dump --build-query`/
`--build-compile-db`) therefore stays blocked, per the plan's own ordering
rule: moving those inputs into config while two resolvers still interpret
that config independently is the exact failure the three-way split exists
to prevent.

**Both real-run migrations attempted and stopped (2026-08-21, later
session) — and the reason is now measured rather than reasoned about,
which changes what "still open" means here.** The paragraph above says the
two resolvers are structurally separate. Comparing the written `dump` CLI
snapshot against `execute_dump_request`'s, field by field, over a real
`g++` build and a real clang L2 parse, shows they *already produce
non-comparable snapshots* for the same library from the same evidence:
everything agrees except the extraction contract, where the CLI records
`macro_ops` as `[["D","FOO=1"],["D","FOO=1"]]` against the typed path's
one entry, and `include_sequence` as `[]` against the typed path's one
slot. `scope_fingerprint` agrees; `profile_fingerprint` therefore differs
in exactly those two shapes. Both trace to one mechanism — the
`dump` CLI runs the legacy `-p`/`--compile-db` auto-match
(`cli_helpers_compare._resolve_build_context_flags`, merged into
`effective_gcc_options`) *in addition to* the P0.3 L3→L2 fold whenever
both are fed by the same `--build-info` compile database. The duplicate
`-D` is that overlap recorded twice; the empty `include_sequence` is the
legacy match supplying `-I<dep>` as explicit context *before* the L2 seed
runs, so `seed_l2_includes` correctly declines to seed it and the
directory reaches the parse through `gcc_option_tokens`, which contributes
no `declared_includes` slot — the sole source `include_sequence`
tokenizes. This is the `dump`-vs-typed-API half of the same "third,
deeper mechanism" the L3→L2-fold entry above already records for
`dump`-vs-`scan`.

**Why that stops the migration rather than motivating it.** Routing the
real run through `execute_dump_request` drops the legacy match, so the
migration does not merely need the two prerequisites named above — it
*forces* the design decision the L3→L2-fold entry says is open. Dropping
the legacy match is arguably right (the fold is strictly richer:
per-header matching, ambiguity checking, include paths, forced includes),
but it makes `dump --compile-db-filter` inert, and `InputSpec`
deliberately carries no `compile_db_filter` field — one was added and
removed in the same review round for having no successful execution path
(see that field's replacement comment in `api_types.py`). Making a
documented flag silently inert is worse than the gap. The ordering is
therefore three slices, not one: thread `--compile-db-filter` into the
shared fold (`buildsource/l2_seed.py`/`header_compile_context.py`), decide
and ship the legacy-match removal, then migrate the real run. **The first
of those three landed in the same session** — and it turned out to be a
user-facing bug in its own right, not merely migration plumbing:
`resolve_header_compile_context`'s fail-closed ambiguity message names
`--compile-db-filter` as a way to narrow the input, but the filter reached
only the legacy match, so a user who followed that advice got the identical
error back. Reproduced end to end (`dump --depth headers -H api.h
--build-info db.json --compile-db-filter a.cpp` over two TUs disagreeing on
an ABI-relevant `-D`) and fixed by threading `source_filter` through
`resolve_header_compile_context`/`l2_seed`/`perform_elf_dump`, with the
matching rules consolidated into one shared
`build_context.source_matches_filter` so the fold, the legacy match and the
ADR-039 collector cannot select different translation units for the same
filter. A filter matching nothing keeps every unit — the conservative
fallback the other two layers already applied. Tests:
`tests/test_compile_db_filter_scope.py` (the primitive's contract as
invariants, the three layers agreeing, the resolver, and a real
`g++`+clang `dump` proving the guarded field is parsed in or out according
to which TU the filter names; the end-to-end cases confirmed to fail
pre-fix). Still open in that first slice: the *typed* half —
`InputSpec.compile_db_filter` plus the CLI's own
L2-filtered/L3-unfiltered refusal mirrored into
`resolve_dump_request`, which is where the resolved collect mode is known
(see that field's replacement comment in `api_types.py`). One
environmental fact independently rules out attempting the *migration
itself* in that session, whatever order the three slices land in: the
*default* header backend is castxml, and no working castxml was
obtainable — a hand-assembled conda-forge 0.7.0 build segfaults inside
`clang::ParseAST` on any input — so every measurement above is
clang-backend only, and migrating the real `dump` run while able to
exercise only the non-default backend is not a verified change.

**The `scan` side is four items, not the two named above, and three of
them are behaviour changes rather than missing plumbing** (read against
`_resolve_side_snapshot_impl` line by line): (1) the L4 extractor default,
unchanged and still castxml-blocked; (2) the `public_headers` expansion
shape, where the shared wrapper's raw pass-through is the one already
reverted for regressing `clang_public_roots._equivalent_public_roots_for_
unit`; (3) the seed's collect mode — `_seeded_includes_and_compile_
context` pins `collect_mode="off"` so a Tier-2 primitive never executes a
build system as a side effect, while `_build_new_snapshot` passes scan's
real one, so routing through the shared primitive silently removes scan's
ability to run the zero-config inferred build query in its seed; and (4)
`defer_cleanup`, which `embed_side_build_source` has no parameter for —
the only purely additive item of the four. Each of (1)–(3) could become an
opt-in parameter the way `symbols_only`/`allow_build_query`/
`changed_paths` already are, which is what a future slice should do;
reproducing a dozen parameter behaviours exactly on the hot path of every
`scan`, with the integration lane only partly executable, is the rewrite
shape this area's review history keeps punishing.

**What landed instead:** `tests/test_dump_cli_typed_api_parity.py::
test_dump_cli_and_typed_api_agree_on_extraction_contract`. Its sibling
compares `ast_compile_args` through `split_compile_args`'
semantics-preserving normalization, which is the right lens for "did both
paths reach the same compile" and structurally blind to both divergences
above — `profile_fingerprint` hashes the recorded fields *as recorded*, so
a difference normalization hides is still a comparability failure. The two
known-divergent (shape, field) pairs are encoded the same conditional-xfail
way `_SCAN_KNOWN_DIVERGENT_SHAPES` already is: the exact diagnosed
signature reproduces, or the test fails outright, so "the gap closed"
fails as loudly as "a new field diverged" and the mapping cannot go stale
silently. Verified in both directions.

**The two divergences that test recorded are closed, `scan`'s candidate
resolver is migrated, and the measurement itself turned up a second, larger
bug (2026-08-21, later session). The `dump` real run is still not migrated —
the reason is now two items, not open-ended.**

*The legacy-match overlap.* The design decision the entry above left open is
made: **when the P0.3 fold resolves a compile context for the headers being
parsed, it is the sole source of compile-database-derived context**, and the
legacy `-p`/`--compile-db` auto-match's own derived flags are unfolded rather
than stacked on top of it. When the fold does not apply (no `--build-info`,
or a header no compile unit matches) the legacy match still runs and still
applies — only the overlap is dropped. The worry that ranked this second —
that dropping the legacy match makes `--compile-db-filter` inert — no longer
holds: the filter reaches the shared fold too, since the preceding slice
threaded `source_filter` through `seed_includes_and_fold_compile_context`/
`resolve_header_compile_context`. Where the conditional goes is the load-
bearing part: `dump_cmd` merges the legacy flags into `effective_gcc_options`
*before* calling `perform_elf_dump`, so that function now takes them
separately (`legacy_build_context_flags`) and hands the fold the caller's
*own* `--gcc-options` string as its explicit context. Presenting the legacy
result to the fold as though it were an explicit user choice is precisely
what recorded the same `-D` twice and routed a derived `-I` through
`gcc_option_tokens` instead of `declared_includes`. User-visible result, not
only a fingerprint tidy-up: `scan --against` a real `dump` baseline for the
extra-`-I` shape goes from **exit 6, `NOT_COMPARABLE ... differing fields:
include_sequence`** to exit 0 for an unchanged library.

*`scan`'s candidate resolver.* `scan_engine._build_new_snapshot` now builds
an `InputSpec`/`SideEvidence` and calls `_resolve_side_snapshot_impl`,
returning its `SideResolution`; `run_scan_core` hands the
`BaselineReuseContext` in at resolve time and reads
`SideResolution.baseline_compile_context` rather than recomputing it. The L2
seed, the `parsed_with_build_context` stamp, the ADR-039 collector gate, the
drain-before-embed ordering and the pair-aware baseline rule are inherited
from one implementation instead of written twice — each of which had already
needed its own separate correction on this path (findings 8/12/13/15 above,
and the round where `scan` turned out never to run the ADR-039 collector at
all). The four documented divergences are preserved as **opt-in parameters**
on the shared primitive, which is what the plan said a future slice should
do: `seed_collect_mode`, `seed_lang_explicit`, `defer_cleanup`,
`source_extractor`, `expand_public_header_roots`, `source_frontend_compile`.
The L4 extractor default therefore stays a documented gap — matching the
other resolvers would newly require castxml for a `scan --depth source` that
works with clang today, and castxml is still absent here. Equivalence was
measured, not argued: candidate snapshot, effective includes, effective
compile context and deferred-cleanup count, over three real build shapes ×
three collect modes, identical before and after apart from wall-clock
timestamps and the build-source pack's own content hash.
`test_scan_engine_calls_the_shared_resolver` was a source-text match on
`run_scan_core`; it is replaced by two behavioural pins through a real
`scan --against`.

*The second bug, found by the verification bar rather than by a report.*
Extending the parity measurement from the extraction contract to the *whole*
snapshot showed the two paths disagreeing on the L3–L5 payload, and not
cosmetically: the `dump` CLI recorded `0/2 symbols matched`,
`reachable_declarations=0`, `fact_family_states: empty-confirmed` where the
typed path recorded `1/2` matched and a real `source_decl_to_binary_symbol`
mapping. `cli_buildsource._write_snapshot_output`'s own `embed_build_source`
call passed **no** `public_headers`/`public_header_dirs`, so L4 replay ran
with an empty `public_header_roots` set — every declaration classifies
private and nothing links. Nothing fails: the layer is present and the
coverage row honestly says "partial", so every L4-derived source-ABI finding
was simply inert for a `dump`-produced baseline. Fixed on both the ELF and
PE/Mach-O paths. With it, the `dump` CLI's written snapshot and
`execute_dump_request`'s agree on every field except wall-clock timings and
the CLI's own provenance layer (`git_commit`, `version`).

**What still blocks the `dump` real-run migration — two items, both real.**
Blocker 4 (post-processing hooks) is closed on measurement, not just on
reading: `service.run_dump`'s ELF branch already runs every pass
`perform_elf_dump` does (SYCL, `python_ext`, `python_api`, `numpy_capi`, the
G31 header graph, the G28 clang-layout attach), the ADR-039 collector runs
inside `_resolve_side_snapshot_impl`, and the whole-snapshot comparison shows
no difference in any field those produce. What remains: (1)
**`--compile-db-filter` would go inert** — `InputSpec` deliberately carries
no `compile_db_filter`, so the shared path cannot narrow the fold or the
ADR-039 collector the way the native CLI does, and making a documented flag
silently do nothing is worse than the gap; the step is specified (add the
field, thread it into `_seeded_includes_and_compile_context` and
`attach_build_context_for_parsed_headers`, mirror the CLI's
L2-filtered/L3-unfiltered refusal into `resolve_dump_request`) but is its own
slice. (2) **The default backend is still unexercisable here** —
`--ast-frontend` defaults to castxml, none is available (re-checked), so
every measurement above is clang-only, and migrating the real `dump` run
while able to exercise only the non-default backend is not a verified change.
PR 3C stays blocked, per the plan's own ordering rule.

**Item (1) closed (2026-08-21, later session): `InputSpec.compile_db_filter`
now exists, exactly as specified above — nothing more, nothing less.**
`service_input_resolution._seeded_includes_and_compile_context` forwards it
as `source_filter` to `seed_includes_and_fold_compile_context`; the
`attach_build_context_for_parsed_headers` call two paragraphs down does the
same, so the fold and the ADR-039 collector agree on which translation
units the filter selects (the identical invariant
`build_context.source_matches_filter` already established for the three
CLI-side layers — see the root AGENTS.md's forced-include entry's
MSVC-driver-vocabulary lesson on why a second copy of a shared matching
rule is the wrong move). `resolve_dump_request` mirrors the CLI's own
`compile_db_filter_scope_error` refusal, computed from `evidence.
collect_mode`/`evidence.headers` — the same resolved values the CLI reads
`compile_db_from_build_info` back against — so a typed caller cannot reach
the L2-filtered/L3-unfiltered snapshot shape the CLI refuses outright; the
refusal raises `ValidationError` (translated to `click.UsageError` at the
CLI boundary by `resolve_dump_request_for_cli`, unchanged). `dump_cmd`
forwards its own `--compile-db-filter` local into `build_dump_request`, so
`--dry-run`'s resolved object now records the same filter the real run
applies, closing the last gap in that request's own honesty contract for
this one field. Verified against the identical real `g++`+clang project
`TestDumpCliHonorsTheFilterInTheFold` already uses (two TUs disagreeing on
an ABI-relevant `-D` behind one `#ifdef`-guarded field), driven through the
typed `DumpRequest`/`resolve_dump_request`/`execute_dump_request` path
directly rather than the CLI: the scope-error refusal fires under the same
condition the CLI refuses under, a request with no filter is unaffected,
and the filter selects the same translation unit's context for the header
parse the CLI test already pins (`tests/test_compile_db_filter_scope.py`'s
`TestTypedApiHonorsTheFilterInTheFold`). Confirmed the CLI's own behavior
is unchanged by re-running `TestDumpCliHonorsTheFilterInTheFold` directly.
**Item (2) is unchanged and remains the sole blocker**: castxml is still
unavailable in every environment this work has been done in, so the real
`dump` CLI execution path (`perform_elf_dump`/`handle_non_elf_dump`) still
does not route through `execute_dump_request`, and PR 3C stays blocked.
This slice narrows what item (2) alone is blocking, nothing more — it does
not migrate the real run, and does not claim to.

**Two Codex review findings on the same slice, both real, both fixed
before merge.** (P2) `InputSpec.of()` — the documented loose-value
convenience factory every front end other than a direct dataclass
construction uses — never gained a `compile_db_filter` parameter, so
`InputSpec.of(..., compile_db_filter=...)` raised `TypeError` for an
unrecognized keyword: the field was reachable only by constructing
`InputSpec` directly, despite being advertised as public typed-API
surface. Fixed by adding the parameter and forwarding it through
unchanged. (P1) The scope-error guard above was wired into
`resolve_dump_request` only — but `InputSpec.compile_db_filter` is shared
by `CompareRequest.old`/`.new` too, and `resolve_compare_request` reaches
the identical `resolve_side_snapshot` primitive (the P0.3 fold narrows,
`embed_side_build_source` still collects L3 unfiltered), so a typed
`CompareRequest` side could reach the exact L2-filtered/L3-unfiltered
snapshot shape the guard exists to reject, with no check catching it. Fixed
by extracting the guard into a shared function,
`service_compare_evidence.reject_compile_db_filter_scope_mismatch` (mirrors
`reject_debug_format_for_binaries`'s existing `(label, ...)` per-side
shape), called from both `resolve_dump_request` (`input`) and
`resolve_compare_request` (`old`/`new`) — one guard, not two independently
drifting copies. Regression coverage: `tests/test_compile_db_filter_scope.py`'s
`test_input_spec_of_forwards_compile_db_filter` and
`TestCompareRequestAppliesTheSameScopeGuard` (the latter, like its
`DumpRequest` sibling, verified against the identical real `g++`+clang
project), plus a re-run of every pre-existing test in this area to confirm
the extraction changed no behavior.

**Three further Codex review findings on the same slice, each real, each
fixed, and each a pre-existing gap in the native CLI's own scope check
too (not introduced by this typed-API slice).** (P1) The guard's compile-
database resolution considered only an explicit `--build-info`/
`build_info` — a `--sources`/`sources` tree with no `build_info` at all
can still have its `compile_commands.json` auto-discovered
(`buildsource.inline._autodiscover_compile_db`, the identical P4 strategy
the fold and the L3 embed both already use to find one from `sources`
alone), so that combination reached the same filtered-L2/unfiltered-L3
mismatch uncaught. Reproduced directly: a real two-TU project resolved
with `sources` only, filtered to one TU at L2, still embedded both TUs'
compile units as L3 evidence (`BuildEvidence.compile_units` length 2).
(P1) Even with an explicit `--build-info <dir>` given, the resolution
checked only `<dir>/compile_commands.json` directly — not a conventional
out-of-tree build subdirectory (`<dir>/build/compile_commands.json`) the
real fold's own `--build-info` resolution
(`buildsource.inline._compile_db_at`, delegating to
`_find_compile_db_in_dir` for a directory) already searches, explicitly
documented as matching `--sources` auto-discovery's own contract.
Reproduced directly: the identical project with its database moved into
a `build/` subdirectory, `--build-info` pointed at the project root — the
fold correctly resolved and filtered by the nested database while the
guard never fired. Both fixed in one place,
`header_conditionals.compile_db_for_filter_scope_check` (deliberately
**not** folded into `compile_db_from_build_info` itself, which also
drives the CLI's unrelated legacy `-p` auto-match and must stay
`--build-info`-direct-child-only — see that function's own docstring),
consumed by both `cli.py`'s `dump_cmd` and the shared typed guard.
Regression coverage: `TestScopeGuardCoversSourcesOnlyAutoDiscovery` and
`TestScopeGuardCoversNestedBuildInfoDatabases` in
`tests/test_compile_db_filter_scope.py` (three entry points each — CLI,
`DumpRequest`, `CompareRequest` — plus a positive control for the nested
case confirming the database is genuinely what gets filtered).

**A fifth finding, investigated and deliberately left as a documented gap
rather than fixed reactively — the point at which these findings stopped
converging on real, reachable bugs.** `execute_dump_request()`/
`_resolve_side_snapshot_impl()` also accept a keyword-only
`build_compile_db` (a glob, mirroring `--build-compile-db`), forwarded
unfiltered to both the L2 fold and the L3 embed the same way
`build_info`/`sources` are — in principle the identical mismatch class.
But `build_compile_db` is not a field of `DumpRequest`/`InputSpec` at
all: it exists purely as scaffolding for the not-yet-landed PR 3A
real-run migration (this module's own docstring: "the real ELF/PE/
Mach-O run still executes through `perform_elf_dump`/
`handle_non_elf_dump`, not through `execute_dump_request`"), and
`execute_dump_request` has exactly one caller in the whole codebase —
`run_dump_request`, which never passes it. No CLI, no typed-API path, and
no test can reach this combination without bypassing the entire
`DumpRequest`-shaped public surface and hand-calling the semi-internal
`execute_dump_request` with a kwarg nothing in that surface can set —
a different reachability class from the four findings above, each
reproduced end-to-end through real, ordinary usage before being fixed.
Left for whichever change gives `build_compile_db` its first real caller
(i.e. the PR 3A real-run migration itself) to close alongside that
migration, rather than shipping validation code with no real path to
verify it against.

**A sixth finding (Codex review, fresh evidence) reopened convergence:
the guard's original `compile_db_from_build_info`-only check covered a
literal compile database and nothing else, but the fold it guards
(`resolve_header_compile_context`/`filter_units_by_source`) narrows
*whatever* `BuildEvidence.compile_units` a `--build-info` resolves to,
regardless of shape.** A `--build-info` naming a pre-captured `collect`
pack directory (`is_pack_dir`) or a Bazel `aquery`/`cquery` jsonproto
resolves compile units the identical way a literal compile database does
— both are routed through their own adapters
(`buildsource.inline._maybe_collect_bazel_build_info`/pack loading), not
`load_compile_db()` — and the L3 embed collects that same `BuildEvidence`
unfiltered either way, so the mismatch reproduced for both shapes with no
error, purely because neither is a `compile_commands.json` file
`compile_db_from_build_info` recognizes. Fixed: `compile_db_for_filter_
scope_check` now also recognizes a `--build-info` that `sniff_build_info_
format` (the same cheap, execution-free classifier `compile_db_from_
build_info` already uses, so the two cannot disagree) reports as `"pack"`
or `"bazel_aquery"`/`"bazel_cquery"`, returning the `--build-info` path
itself as the guard's non-`None` signal (the guard only ever checks
`is None`, so this needs no literal compile-database content).
`compile_db_filter_scope_error`'s docstring, which had explicitly claimed
the opposite ("a pack or Bazel jsonproto routes through a different
adapter" → `None`, i.e. by design out of scope), was corrected alongside
the fix — that claim was the bug's own design rationale, not a separate
error. **Still not covered, and not attempted here**: a `--sources` tree
with no discoverable `compile_commands.json` at all, resolved instead
through the zero-config *inferred* build-system query (cmake/make/bazel).
Unlike the pack/Bazel-jsonproto case, telling whether that combination
would actually resolve multiple compile units means running the build
system's own query — the exact side effect this cheap, read-only scope
check exists to avoid paying twice per invocation (once to check, once for
real) — so this residual is documented rather than guessed at, per this
file's own "known gaps over risky reactive patches" convention. Regression
coverage: `TestScopeGuardCoversPackAndBazelBuildInfo` in
`tests/test_compile_db_filter_scope.py` (the predicate directly — pack
directory, both Bazel jsonproto shapes, both positive and negative
controls, plus a plain non-pack directory and a non-Bazel JSON-object file
confirmed to still resolve `None`; five of nine cases confirmed to fail
against the pre-fix guard).

**A seventh finding (Codex review, fresh evidence) on the same guard:
the sixth finding's fix only recognized a pack named by `--build-info`,
but `buildsource.l2_seed._l2_seed_pack_inputs` folds a `--sources` pack
(a classic `BuildSourcePack` or a Flow-2 `abicheck_inputs/` directory)
into L2 seeding the identical way — carrying its own normalized
`BuildEvidence` in — whenever no `--build-info` was given at all (an
explicit `--build-info` always wins L3, matching that function's own
`if build_info is None:` gate on the assignment).** A `--sources` naming
such a pack, with no `--build-info`, reproduced the identical mismatch:
the guard's fallback resolution (`compile_db_from_build_info` then
`_autodiscover_compile_db`) only ever looks for a literal
`compile_commands.json` inside *sources*, which a pack directory does not
carry at its root — so it silently resolved `None` and let the mismatch
through. Fixed by recognizing a `sources` pack the identical way
`_l2_seed_pack_inputs` does (`is_pack_dir` / `inputs_pack.is_inputs_pack`),
gated on `build_info is None` to match that function's own precedence
exactly — an explicit `--build-info` (even one that itself resolves to
nothing recognizable) still means the sources pack's evidence is never
folded into `base_build`, so the guard must not treat it as filterable
evidence in that combination either (pinned by its own regression test).
Regression coverage: `TestScopeGuardCoversSourcesPacks` in
`tests/test_compile_db_filter_scope.py` (a classic pack and a Flow-2
inputs pack named by `sources`, the scope error firing, the
`build_info`-takes-precedence control, a no-filter control, and a plain
non-pack `sources` directory still falling through to ordinary
auto-discovery; three of six cases confirmed to fail against the pre-fix
guard).

**An eighth finding (Codex review, fresh evidence) — not a missing case
this time, but a genuine false positive the seventh finding's fix
introduced.** That fix restructured the function so every branch fell
through unconditionally to the `sources`-based checks once none of the
`build_info` branches matched — including the case where `build_info`
was genuinely given but resolved to nothing recognizable. That is wrong:
`buildsource.inline._resolve_compile_db` — the real function every one
of these seeded resolvers (`collect_inline_pack`, in turn called by
`seed_includes_and_fold_compile_context`/`embed_build_source` alike)
ultimately calls — tracks `explicit_input_missed` and returns `None` as
soon as a *given* `--build-info` misses, deliberately, per its own
comment: "surface that miss rather than masking it with a stale
auto-discovered DB ... checked BEFORE auto-discovery." So an explicit
`--build-info` that doesn't resolve means neither the real L2 fold nor
the L3 embed ever falls back to a `sources`-discovered database — falling
back in the guard (the post-seventh-finding behavior) produced a false
positive: rejecting a `--compile-db-filter` combination the real
resolvers wouldn't actually apply to either side of, a usage error for a
perfectly safe invocation. Fixed by returning `None` immediately once the
`build_info is not None` branch exhausts its own checks, before ever
reaching the `sources`-based fallbacks — matching `_resolve_compile_db`'s
own precedence exactly. Regression
coverage: `TestScopeGuardDoesNotFallBackToSourcesWhenBuildInfoMisses` in
`tests/test_compile_db_filter_scope.py` — a pure-predicate case (an
unresolvable `build_info` alongside a `sources` tree carrying a real,
auto-discoverable `compile_commands.json`, confirmed to fail against the
post-seventh-finding code) plus a positive control against the real
g++/clang project fixture, confirming the guard doesn't reject a genuinely
safe combination.

**A ninth finding (Codex review, fresh evidence): the third
under-coverage's own pack recognition for `--build-info` (the sixth
finding above) only ever checked `is_pack_dir` — a classic
`BuildSourcePack` — never `inputs_pack.is_inputs_pack`, the Flow-2
`abicheck_inputs/` shape.** `_l2_seed_pack_inputs` recognizes both shapes
for `build_info` identically (`is_pack_dir(build_info) or
_is_inputs_pack_dir(build_info)`), and `embed_build_source`'s own
`bi_is_inputs` check embeds a Flow-2 `build_info` pack the same way — so a
`--build-info` naming a Flow-2 pack reproduced the identical mismatch,
missed only because the sixth finding's fix carried over `is_pack_dir`
without its Flow-2 sibling, even though the seventh finding's fix
(`--sources` packs) already checks both. Fixed by adding `or
is_inputs_pack(build_info)` to the same branch. Regression coverage:
`TestScopeGuardCoversPackAndBazelBuildInfo::
test_flow2_inputs_pack_named_by_build_info_is_recognized` in
`tests/test_compile_db_filter_scope.py`, confirmed to fail against the
pre-fix guard.

**A real regression the scan-migration paragraph above introduced, found
by Codex review and fixed the same session (2026-08-21): `scan --config
<path>` silently lost the config's own *passive* settings whenever the
config declared no `build.query` — the common case, not an edge one.**
`_resolve_side_snapshot_impl`'s shared `build_config`/`build_query` gate
(`_gated_build_query_inputs`) blanket-nulls `build_config` unless
`allow_build_query` is exactly `True`, a default sized for `dump`/
`compare`'s typed API (no CLI-side consent step of its own, so mere
presence cannot be trusted). Migrating `scan`'s candidate resolution onto
this same primitive routed it through that gate too — but `scan`'s own
consent gate, `cli_scan_helpers.resolve_effective_allow_query` (ADR-037
D4 "level-implies-query"), only ever answers `True` when the config
*itself* declares an executable `build.query` key AND an explicitly-pinned
deep evidence level; it was never meant to answer whether the config may
be *read* at all. `build_config`'s own query field is already, correctly,
gated downstream regardless of this local gate — `collect_inline_pack`'s
presence-based `build_config_trusted_for_query`, computed independently by
both of this gate's callers (`l2_seed._resolve_l2_seed_pack_args`,
`cli_buildsource.embed_build_source`) since before this migration existed.
Confirmed against scan's own pre-migration source (commit `c3f6add`):
`build_config` was always forwarded ungated to both
`seed_includes_and_fold_compile_context` and `embed_build_source`,
trusting exactly that downstream gate; `allow_build_query` was a separate,
already-documented-dead-in-the-`True`-direction parameter that never
gated `build_config`'s presence at all. Fixed with a new opt-in parameter,
`build_config_locally_trusted` (threaded through `_gated_build_query_
inputs`, `_seeded_includes_and_compile_context`, and
`_resolve_side_snapshot_impl`), defaulting `False` so `dump`/`compare`'s
typed-API contract is completely unchanged; `scan_engine.
_build_new_snapshot` passes `True`, restoring its exact pre-migration
behavior. `build_query` — the bare, always-executable command string, with
no downstream gate of its own — stays fully gated by `allow_build_query`
regardless of this flag either way. Regression coverage:
`tests/test_gated_build_query_inputs.py` (primitive-level tests on the
gate itself, plus one end-to-end test on `scan_engine._build_new_snapshot`
proving `build_config` survives even when `allow_build_query` is falsy;
5 of 8 cases confirmed to fail against the pre-fix gate).

**A build-source pack's replay *scope* (`"changed"` vs `"target"`) is not
recorded anywhere, so the write-time idempotence guard cannot distinguish
them — investigated (CodeRabbit review), not fixed; currently unreachable
by any real caller, which is why this stayed a documented gap rather than
a same-session patch.** `_missing_requested_evidence_layers()` maps an
ADR-033 collect mode to its expected *layer set* (`CI_MODE_TO_LAYERS`) and
checks only whether each layer's embedded payload is non-empty — it never
reads `collection_for_ci_mode()`'s other return value, the replay scope.
`source-changed` (only affected TUs replayed) and `source-target` (the
full target) map to the *identical* layer set `("L3", "L4", "L5")`, so in
principle a pack built under `source-changed` — non-empty because *some*
TUs were affected — could read as satisfying a later `source-target`
request through `build_source_already_satisfies()`, the write-time
check-before-embed guard PR 3A blocker 5 sub-issue 3 added (see above).
Traced why this is not reachable today: that function has exactly one
caller (`_write_snapshot_output`'s guard), which runs on `snap.build_
source` *before* any embedding has happened for the current `dump`
invocation — the only other `snap.build_source = ...` assignment anywhere
in the codebase is `embed_build_source()`'s own, which this guard exists
to gate — so `snap.build_source` is always `None` entering the guard for
the one real caller, and the function is unconditionally a no-op today
exactly as its own docstring already states. It exists for the *future*
migration that routes `dump`'s real execution through
`execute_dump_request` (still blocked, see above); in that migration both
the resolve-time and write-time embeds would receive the *same* resolved
`collect_mode` for one invocation, not two different ones, so this
specific scope mismatch doesn't arise from that path either. The deeper
gap the finding surfaces is real independent of this predicate, though:
`BuildSourceManifest` has no field recording replay scope at all —
`pack.manifest.inputs` (`buildsource/inline.py`) only ever records
`{"sources", "build_info", "collected"}`, never `"changed"` vs `"target"`.
A correct fix needs a new manifest field threaded through every
pack-producing call site (`collect_inline_pack` and its Bazel/compile-DB
siblings) plus a scope-aware read in `_missing_requested_evidence_
layers`, with its own regression coverage for a genuine scope-narrowing
scenario — a real, if currently latent, data-model gap, not a one-line
fix to this one predicate.

**A real regression caught post-merge by CI on this same branch
(2026-08-21), traced to the `fb688cb` dump-side fix above interacting
with a *pre-existing*, differently-scoped `scan` default — a live
`tests/test_dump_scan_l3_comparability.py` end-to-end test (added on
`main` by an unrelated, earlier PR) went from passing to failing the
moment this branch merged the base back in, `git bisect`-isolated to
exactly the dump-side write-time-embed fix.** That fix made `dump`'s
written baseline correctly link its L4 declarations to the binary's
exported symbols for a project whose only `-H` input is a lone header
*file* (no directory, no `--public-header-dir`) — but `scan`'s own
candidate resolution, unchanged by that fix, still derives its L4
`public_header_roots` from `cli_scan_baseline._public_provenance_set`,
which *deliberately* returns an empty root set for exactly that shape (a
lone file cannot establish a public directory boundary — a real,
separately pinned contract, `test_lone_file_does_not_activate`, unrelated
to and predating this PR). Before the dump-side fix, both sides degraded
to zero L4 matches symmetrically, so nothing was ever reported; after it,
only the dump baseline matched, and the asymmetry itself read as a real
`source_decl_binary_symbol_mismatch`/`source_to_binary_mapping_changed`
RISK finding on an *unchanged* library. Confirmed base-red-negative (the
identical test passes on plain `main`) and confirmed *not* a
merge-interaction artifact (it already reproduces on this branch's own
tip before merging `main` back in) before attempting a fix. Considered
and rejected: widening `_public_provenance_set` itself (would also
silently flip the L2/crosscheck-origin classification — and its skip/
present status for `exported_not_public`/`private_header_leak`/etc. —
every other `scan` invocation of this shape already relies on, a far
broader behavior change than this fix needs, and it would break that
helper's own pinned unit test). Fixed narrowly instead:
`service_input_resolution.embed_side_build_source` gained
`l4_public_headers`/`l4_public_header_dirs`, an override pair for *that
one call's* root set, defaulted to the existing `public_headers`/
`public_header_dirs` for every pre-existing caller (so `compare`/`dump`'s
typed pipeline are bit-for-bit unaffected). `scan_engine.
_build_new_snapshot` now computes a second, wider root set via the same
`split_public_header_inputs` `dump`'s own fix already uses (unioned with
the narrower, provenance-derived set, not replacing it — an explicit
`--public-header-dir` must still reach L4 even when it isn't itself
derivable from the raw `-H` list) and passes it through this new
parameter — L2/crosscheck-origin classification is completely untouched.
Regression coverage: a new direct unit test on `_build_new_snapshot`
itself, `tests/test_scan_l2_cleanup_ordering.py::
test_scan_candidate_widens_l4_roots_with_a_lone_header_file` (confirmed
to fail against the pre-fix code), alongside restoring the pre-existing
end-to-end integration test to green. One sibling, pre-existing test
(`test_scan_candidate_expands_public_header_dirs_before_embed`) used a
nonexistent placeholder `-H` path purely as an unrelated fixture detail;
once `headers` started contributing to the same L4 set, that placeholder
made `expand_public_header_inputs`'s best-effort expansion degrade to a
raw pass-through for *everything* (a real, if narrow, generalization of
that same best-effort-degrades-on-any-missing-path behavior) — fixed by
emptying that test's own `headers` list, since its actual subject is
`public_headers`/`public_header_dirs`'s own expansion, not `headers`'s.

**ADR-063 Phase 1 (`docs/contribute/plans/one-semantic-pipeline.md`,
"finish the `dump`/`scan` typed-API convergence") re-investigated this
entry's still-open blocker 2 with castxml genuinely available in the
investigating environment (a solver-resolved conda-forge install, not the
hand-assembled 0.7.0 build the plan's Design section found segfaulting) —
so the environmental precondition for full option (a) convergence no
longer blocks. **One real, safely-landable slice of blocker 1 closed for
real** (`cli_dump_helpers.render_dump_dry_run` now takes the real
`ResolvedDumpRequest` `resolve_dump_request_for_cli` already produces —
`so_path`/`headers`/`sources`/`build_info`/`depth`/`collect_mode`/
`header_backend`/`dump_manifest` are all read off it, not re-passed as
fifteen independently-threaded primitives — verified against
`test_dump_cli_typed_api_parity.py -m integration`, 16/16 green. **That
acceptance-gate file itself is clang-only, not evidence of castxml
coverage**: every one of its subprocess invocations hard-codes
`--ast-frontend clang`, not parametrized by backend at all (confirmed —
`pytest tests/test_dump_cli_typed_api_parity.py -m integration -k
castxml` selects zero tests). What castxml's newfound availability
separately confirmed is broader but different: the wider integration
suite's own castxml-specific cases (`pytest tests/ -m "integration and
not slow" -k castxml`) are 38/38 green with only the two pre-existing,
unrelated `xfail`s — real evidence `abicheck dump --ast-frontend castxml`
itself works end to end in this environment, not evidence this
particular parity file exercises it. Field-level parity between the two
paths was already closed
before this phase started -- `_CONTRACT_KNOWN_DIVERGENT_FIELDS` and
`_SCAN_KNOWN_DIVERGENT_SHAPES` in that test module were both already
empty -- so there was no xfail-gated shape left for this phase to flip;
confirmed empty both before and after this phase's change.

**Blocker 2 (the post-processing hooks) does NOT close, and the reason is
independent of which AST backend is available, so obtaining castxml did
not remove it.** Re-read `perform_elf_dump` end to end (not skimmed)
looking specifically for whether its first try block (the primary
`seed_includes_and_fold_compile_context` + `dump()` call) could be
replaced by a call to `execute_dump_request()`, keeping the second try
block's post-processing hooks (the ADR-039 collector's own explicit
second call, the header-graph attach, the clang-layout-tool attach)
unchanged as hooks applied to the returned snapshot. Two sub-findings,
each confirmed against the real code, not assumed:

1. *(Not actually a blocker — investigated and ruled out.)* The ADR-039
   collector (`attach_build_context_for_parsed_headers`) already runs a
   second time, unconditionally, inside `_resolve_side_snapshot_impl`
   itself (PR C's own shared-gate work wired it in there too). Calling it
   a *third* time from `perform_elf_dump`'s own existing second block —
   which is what "keep the hook, route the primary parse" would produce
   — is safe: `attach_build_context` *assigns*
   `snap.build_context_defines`/`conditional_fields`, it never
   accumulates, so a second identically-scoped call is idempotent, and
   `parsed_with_build_context` is only ever set `True`, never reset to
   `False`, so a redundant second stamp cannot regress it. Similarly,
   `scope_header_dirs` (a parameter `perform_elf_dump`'s own `dump()`
   call passes that `_dump_elf`, reached via `execute_dump_request`,
   does not) turns out to be provably redundant with `resolve_dump_
   request`'s own `public_header_dirs` (both are derived from the
   identical `split_public_header_inputs(headers)` call, and `dump()`
   unions them for the extraction contract) — so this is not a real
   divergence either, just a vestigial second computation of the same
   set of directories.
2. *(A real, structural blocker, confirmed by reading the code, distinct
   from anything the Design section named.)* `dump_cmd`'s legacy
   `-p`/`--compile-db` auto-match (`cli_helpers_compare.
   _resolve_build_context_flags`, using `build_context_for_header`/
   `build_context_union_fallback` — a completely different code path from
   the P0.3 L3->L2 fold's `seed_includes_and_fold_compile_context`) is
   computed in `dump_cmd` *after* `resolve_dump_request_for_cli` already
   built the `ResolvedDumpRequest` (`_resolved`), on the real-execution
   branch only, never on the typed-request-building path at all. Its
   result (`effective_gcc_options`/`effective_compile_db`/
   `compile_db_context_matched`) is what `perform_elf_dump`'s own
   `effective_gcc_options` parameter already carries into its primary
   `dump()` call -- and per this same entry's earlier "legacy-match
   overlap" fix, that legacy match's derived flags are the *sole* source
   of compile-database-derived context whenever the P0.3 fold does *not*
   independently match the same header (the fold's result wins and
   supersedes it whenever the fold *does* match — already the case
   `effective_gcc_options`/`l3_context_applied`'s reassignment in
   `perform_elf_dump` handles). `resolve_dump_request`/
   `_resolve_side_snapshot_impl` has no equivalent call to
   `_resolve_build_context_flags` anywhere -- `DumpRequest.input.compile`
   only ever carries the CLI's own explicit `--gcc-options`, never the
   legacy match's derived flags. So routing `perform_elf_dump`'s primary
   parse through `execute_dump_request()` as-is would silently drop real,
   still-live, still-documented (`dump --build-query`/
   `--build-compile-db`/`-p`/`--compile-db` are explicitly not yet
   removed — PR 3C is gated on this same convergence closing first)
   compile-database-derived flags for exactly the headers the P0.3 fold
   itself does not match — a real regression, not a refactor, for any
   project relying on that fallback. Closing this for real needs the
   legacy match's computation moved earlier (before `resolve_dump_
   request_for_cli` runs) and threaded into the `DumpRequest`/
   `CompileContext` the resolved object carries, so the typed pipeline
   sees it too — a genuine, separate design change to the request-
   building sequence (which field absorbs the legacy match's *derived*,
   not user-typed, flags, and whether that blurs `DumpRequest`'s
   documented "records the run, not a second opinion about it"
   contract), not a same-session drive-by fix. **Not attempted here** —
   recorded so a future attempt starts from this precise mechanism
   instead of re-deriving it, per this file's own "known gaps over risky
   reactive patches" convention.

**Net effect on this phase's own scoping**: full "route `perform_elf_dump`
through `execute_dump_request`" (Design section item, this entry's
original blocker 2) remains unattempted for the reason above -- this was
never actually gated on castxml availability the way the Design section's
own item 2 implied; that item's "(b) scope to clang, castxml tracked as
residual" framing turned out to describe the wrong axis. What castxml's
availability *did* let this phase newly verify -- run against the wider
integration suite, not the clang-only acceptance corpus itself (see
above) -- is that `abicheck dump --ast-frontend castxml` genuinely works
end to end in this environment today, closing the environmental
uncertainty the Design section's segfault finding had left open. The
acceptance corpus's own field-level parity (`_CONTRACT_KNOWN_DIVERGENT_
FIELDS`/`_SCAN_KNOWN_DIVERGENT_SHAPES` empty) remains verified for clang
only, exactly as it was before this phase -- extending that specific
corpus to also parametrize over castxml is real, still-open follow-on
work this phase did not attempt.

**Update (2026-09-30): closed.** The legacy match now has one owner in
the typed pipeline, `workflows/artifact/compile_db_match.py`, and
`execute_dump_request` runs it itself from `DumpExecutionOptions.
compile_db`/`compile_db_filter`. The CLI no longer computes tokens and
threads them through: `_resolve_build_context_flags`,
`dry_run_compile_db_matched` and `dry_run_build_context_preview` are
deleted, and the pass-throughs described below are renamed
(`legacy_compile_db_tokens` -> `compile_db_tokens`,
`_fold_legacy_compile_db_tokens` -> `_fold_compile_db_tokens`). The union
fallback remains the legacy match's own semantics and was deliberately not
moved into the P0.3 fold (that would change every typed-API caller). The
history below is kept as written.

**Update (2026-08-29): the legacy-match threading half of blocker 2 is now
closed; routing `perform_elf_dump` itself through `execute_dump_request`
is still open.** This session re-read the exact mechanism the entry above
names (`cli_helpers_compare._resolve_build_context_flags`'s legacy
``-p``/``--compile-db`` auto-match having no equivalent inside
`resolve_dump_request`/`execute_dump_request`) and closed the piece that
was safely landable without also restructuring `perform_elf_dump`'s own
try/except/cleanup structure in the same change:

1. `execute_dump_request` gained a new, purely additive
   `legacy_compile_db_tokens: tuple[str, ...] = ()` parameter, threaded
   down through `workflows.artifact.execute._resolve_side_snapshot_impl`
   into `workflows.artifact.resolve._seeded_includes_and_compile_context`
   — the exact same "optional pass-through, defaulted to a no-op, that
   exists only for `dump`'s still-live CLI legacy flags" pattern
   `build_config`/`build_query`/`build_compile_db` already established on
   these same three functions (PR 3A). A caller that already computed the
   legacy match's own derived flags (exactly what `dump_cmd` already does
   via `_resolve_build_context_flags`, unchanged) can now thread them
   through the typed pipeline and have them actually reach the real L2
   header-AST parse (`service.resolve_input`'s `compile=` argument).
2. **Precedence preserved exactly**, mirroring `perform_elf_dump`'s own
   "legacy-match overlap" fix this entry already documents: the tokens are
   merged into the resolved `CompileContext.gcc_options` only when the
   P0.3 fold's own `applied` came back `False` for a given header — when
   the fold *does* apply, its own result is used verbatim and the legacy
   tokens are discarded rather than stacked on top, verified by a
   dedicated precedence test (see below) that pins the merged
   `gcc_options`/`gcc_option_tokens` string as byte-identical between "no
   legacy tokens" and "legacy tokens given" when the fold applies.
3. The merge helper (`_fold_legacy_compile_db_tokens`) is a small,
   independent 3-line reimplementation of
   `cli_helpers_compare._merge_gcc_options`'s ordering (legacy flags
   prepended ahead of any existing `gcc_options`), not an import of that
   function — `workflows/artifact/resolve.py` is an engine-layer module
   under `scripts/check_ai_readiness.py`'s `engine-cli-boundary` check,
   which forbids importing a `cli_*` sibling (that module itself imports
   `click`). Confirmed via `check_ai_readiness.py`: zero new
   `engine-cli-boundary` findings.
4. Verified end to end against a real `g++` build + real `compile_commands.json`
   + real clang L2 parse, not only the merge helper in isolation
   (`tests/test_legacy_compile_db_typed_threading.py`, 4/4 green): a
   compile unit whose source text does not `#include` the public header
   at all is exactly the shape where the two mechanisms provably disagree
   — `header_compile_context.resolve_header_compile_context` (the P0.3
   fold) returns `context=None` with **no union fallback** (confirmed by
   reading its own docstring: "no header the given `CompileUnit`s
   reference" degrades to nothing, full stop), while `build_context.
   build_context_for_header` (the legacy match) falls back to
   `build_context_union_fallback`, which still merges the compile
   database's `-D`s and still sets `compile_db_path` (so
   `_resolve_build_context_flags`'s own `matched` comes back `True`). One
   test proves the real CLI already sees the union-fallback define (the
   fixed point to reproduce); one proves the typed path does **not** see
   it with the new parameter absent (proving the gap this closes was
   real, and that the new parameter is genuinely opt-in rather than
   silently changing existing callers' behavior); one proves the typed
   path **does** see it, byte-identically to the CLI, once the CLI's own
   already-computed `_resolve_build_context_flags` output is threaded
   through `legacy_compile_db_tokens`; one proves the fold-wins precedence
   holds when the fold does apply.

**What is explicitly still open, and why this session did not attempt
it**: `perform_elf_dump`'s primary parse (the first try block --
`seed_includes_and_fold_compile_context` + `dump()`) does **not** yet call
`execute_dump_request()` — the real `dump` CLI's ELF/PE/Mach-O run still
executes through `cli_dump_helpers.perform_elf_dump`/`handle_non_elf_dump`
exactly as before, so `dump_cmd` does not pass `legacy_compile_db_tokens`
anywhere yet (there is nowhere in it that calls `execute_dump_request` to
pass it to). Closing that remaining piece needs restructuring
`perform_elf_dump`'s own try/except/`ResolvedArtifactPlan` cleanup
handling to delegate to `execute_dump_request()` while preserving its
second try block's post-processing hooks (the ADR-039 collector's own
second call, the header-graph attach, the clang-layout-tool attach) as
hooks applied to the returned `DumpResult`'s snapshot — this entry's own
earlier sub-finding 1 already confirmed that keeping those hooks as a
second pass is safe/idempotent, so the remaining work is purely the
control-flow restructuring itself, not a new correctness question. Given
this exact code area's own history in this entry (18+ numbered findings
on the adjacent L3->L2-fold alone, several reverted-and-refixed), that
restructuring was deliberately left as its own, separately-reviewable
slice rather than folded into this one — consistent with how every prior
slice in this entry was landed one at a time. `cli_dump_request.py`'s own
module docstring and `service_dump_pipeline.execute_dump_request`'s
docstring both point back here for exactly what remains.

**Correction (2026-08-29, same day, Codex review on PR #935): the
threading above landed with a real bookkeeping gap of its own, now
fixed.** Folding the legacy tokens into the resolved `CompileContext`
(sub-finding 2 above) updated `gcc_options` but left the function's
returned `applied` boolean — the exact signal `_resolve_side_snapshot_
impl` gates `AbiSnapshot.parsed_with_build_context` on — untouched at
`False` whenever the P0.3 fold itself did not match. Confirmed by reading
the actual gate (`workflows/artifact/execute.py`'s `if context_applied
and snap.from_headers: snap.parsed_with_build_context = True`): a typed
dump relying purely on the legacy-match fallback would have parsed real
compile-database context and then still reported it as absent — wrongly
triggering the `header_parse_context_drift`/`header_build_context_
mismatch` advisory findings and wrongly failing a `--depth build` gate
that the real CLI's own `perform_elf_dump` path (whose `compile_db_
context_matched` OR `l3_context_applied` condition already handles this
correctly) would have satisfied for the identical evidence. A second,
distinct problem in the same spot: an empty `legacy_compile_db_tokens`
tuple is indistinguishable from "the legacy match never ran" — so a
compile unit the legacy match genuinely matched, but which legitimately
derives zero castxml flags, had no way to signal that it *was* matched.

Fixed by adding a second, independent parameter, `legacy_compile_db_
matched: bool = False` — mirroring `perform_elf_dump`'s own `compile_db_
context_matched` parameter exactly, the second element of
`_resolve_build_context_flags`'s own return — threaded through the
identical three-function chain (`execute_dump_request` →
`_resolve_side_snapshot_impl` → `_seeded_includes_and_compile_context`).
`_seeded_includes_and_compile_context` now returns `applied=legacy_
compile_db_matched` (not the fold's own, already-`False` `applied`) in
the branch where the P0.3 fold did not match, in both its early-return
path (no `sources`/`build_info`, or no headers) and its main path — so
a real match sets `parsed_with_build_context` regardless of whether any
tokens were actually derived, while an unmatched call (the default, and
every pre-existing caller) stays exactly as it was. Both new parameters
default falsy, so this remains purely additive.

Verified with four fast, monkeypatch-based unit tests (no compiler
needed — `tests/test_legacy_compile_db_matched_signal.py`): matched with
zero tokens still sets `applied=True`; unmatched with zero tokens stays
`applied=False` (the pre-existing default behavior, pinned unchanged);
matched with real tokens sets both the folded `gcc_options` and
`applied=True`; the fold-applies-wins precedence (sub-finding 2 above)
holds regardless of what the legacy-match parameters claim. Confirmed
each of the four fails with `TypeError: unexpected keyword argument
'legacy_compile_db_matched'` against the pre-fix code (the parameter
did not exist), not merely that they pass now.

**Second correction (2026-08-29, same day, second Codex review round on
PR #935): the fix above still under-counted a real call shape.** A caller
may thread non-empty `legacy_compile_db_tokens` while leaving the new
`legacy_compile_db_matched` parameter at its default `False` — exactly the
shape `tests/test_legacy_compile_db_typed_threading.py`'s own end-to-end
caller uses. `_seeded_includes_and_compile_context` still returned
`applied=legacy_compile_db_matched` alone in that case (both the
early-return and main-path branches), so `applied` stayed `False` even
though non-empty tokens are themselves proof a legacy match derived real
flags — reproducing the identical `parsed_with_build_context` under-report
the first correction above closed, just reachable from a different call
shape.

Fixed via a shared `_legacy_compile_db_achieved(matched, tokens) -> bool`
helper: `matched or bool(tokens)`. Both branches now call it instead of
reading `legacy_compile_db_matched` directly. `legacy_compile_db_matched`
remains necessary on its own for a genuinely matched compile unit that
legitimately derives zero flags (an empty token tuple can't represent that
case); non-empty tokens are sufficient evidence on their own, independent
of whether `matched` was also passed.

Verified with two new fast unit tests in the same file
(`test_tokens_alone_without_explicit_matched_flag_still_marks_applied`,
covering the main path; `test_early_return_path_also_honors_tokens_alone`,
covering the early-return branch) — both confirmed to fail against the
pre-fix code (`assert False is True`) via `git stash`, and to pass after.
Full fast unit suite re-run clean (33836 passed, 129 skipped, 4 xfailed,
0 failed) after this second correction.

**Third correction (2026-08-29, same day, third Codex review round on PR
#935): a distinct, real correctness bug in the same function, found by
reading the actual token shapes involved rather than assumed.**
`_fold_legacy_compile_db_tokens` used to merge *tokens* into
`CompileContext.gcc_options` via `" ".join(tokens)` — but *tokens* are
already-split argv entries (`build_context.to_castxml_flags()`'s own
return, e.g. `("-I", "/opt/SDK Files/include")`, one element per argv
position, never pre-joined), and every consumer of `gcc_options`
re-splits it via `_compiler_options.split_gcc_options` before handing it
to the real castxml/clang subprocess. A token containing embedded
whitespace — a Windows SDK include path with a space, or a compile-db
`-DNAME=a b` define — silently split back into the wrong number of
tokens on that second pass, corrupting the derived include path or macro
value the moment a typed dump relying on the legacy match actually
reached the real parse. Confirmed real, not theoretical: `to_castxml_
flags()` genuinely emits `-I`/`<path>` as two separate list elements
(`flags.extend(["-I", str(inc)])`), so any compile-database include path
with a space reaches this function in exactly the corrupting shape.

**Also confirmed to be pre-existing, shared debt, not novel to this
PR**: `cli_helpers_compare._merge_gcc_options` — the real CLI's own
legacy-match merge path, which `_fold_legacy_compile_db_tokens`'s own
docstring already documented as byte-for-byte mirroring — has the
identical `" ".join(build_context_flags)` pattern feeding the identical
`CompileContext(gcc_options=...)` field, so the real, unconditional
`dump -p compile_commands.json` CLI path carries this same corruption
today for a compile-database entry whose derived flags include
whitespace. **Not fixed here** — this correction's scope is the typed
pipeline this session's own work introduced; `_merge_gcc_options` is
pre-existing, live, widely-exercised code with its own blast radius, and
changing it needs its own dedicated review pass rather than riding along
inside an unrelated correction. Recorded here as a known, real,
reproducible gap: an include path or define value containing a space in
a compile database used with `dump -p`/`--compile-db` (no `--dry-run`
involved — this is the real-execution path) can silently corrupt the
derived castxml flags.

Fixed in the typed pipeline by routing *tokens* through
`CompileContext.gcc_option_tokens` (verbatim argv entries, a field that
is never re-parsed by `split_gcc_options`) instead of the `gcc_options`
string. Precedence preserved exactly: since the combined-token order
always places `gcc_options` ahead of `gcc_option_tokens` (later wins),
and the legacy match must still lose to an explicit, caller-supplied
value, *ctx*'s own `gcc_options` string is split once here — with the
identical `split_gcc_options` splitter every consumer already applies to
it downstream, so this changes no token list, only where the split
happens — and the combined tuple built as `(*tokens, *split(ctx.
gcc_options), *ctx.gcc_option_tokens)`: legacy first (lowest
precedence), then whatever *ctx* already carried, in its original
relative order.

Verified with five new fast unit tests
(`TestWhitespaceBearingTokensSurviveTheFold` in
`tests/test_legacy_compile_db_matched_signal.py`): a whitespace-bearing
include path and a whitespace-bearing define value both survive intact;
an explicit `ctx.gcc_options` still outranks a conflicting legacy token;
an explicit `ctx.gcc_option_tokens` still outranks a conflicting legacy
token; an empty token tuple remains a true no-op (`ctx` returned
unchanged by identity, not merely by value). Three of the five confirmed
to fail against the pre-fix code via `git stash` (the two whitespace
tests, and the `gcc_option_tokens`-precedence test — the `gcc_options`-
precedence test and the no-op test already held under both versions).
The three pre-existing tests this correction's field change touched
(`test_matched_with_tokens_folds_flags_and_marks_applied`,
`test_tokens_alone_without_explicit_matched_flag_still_marks_applied`,
`test_early_return_path_also_honors_tokens_alone`) were updated to
assert `gcc_option_tokens` instead of the now-unused `gcc_options`
string; `tests/test_legacy_compile_db_typed_threading.py`'s own
precedence test (`test_fold_wins_over_legacy_tokens_when_it_applies`)
needed no change, since it already read the *combined* effective token
sequence across both fields rather than pinning `gcc_options` alone.

**Fourth correction (2026-08-29, same day): this entry's own claim that
"the remaining work is purely the control-flow restructuring itself, not a
new correctness question" is WRONG, and is retracted here.** A dedicated
session set out to do exactly the routing that claim scoped —
`perform_elf_dump`'s primary parse calling `execute_dump_request()` instead
of `seed_includes_and_fold_compile_context()` + `dumper.dump()` as two
independent steps — read every function on both sides end to end
(`perform_elf_dump`, `handle_non_elf_dump`, `service_dump_pipeline`'s
`resolve_dump_request`/`execute_dump_request`/`ResolvedDumpRequest`/
`DumpResult`, `workflows/artifact/execute.py`'s
`_resolve_side_snapshot_impl`/`enforce_requested_depth`,
`workflows/artifact/resolve.py`'s `_seeded_includes_and_compile_context`,
`service.resolve_input`, `service_dump_native._dump_elf`,
`cli_buildsource._write_snapshot_output`, `cli_dump_request.
build_dump_request`, and `frontends/cli/commands/dump.py`'s real call
sites) and built the parameter-by-parameter parity map the routing needs.
**Neither function was converted.** Two *structural* blockers were found
that the claim above did not anticipate, both distinct from the legacy
`-p`/`--compile-db` mechanism the earlier sub-finding 2 named and closed.
Several previously-suspected blockers were, by contrast, ruled out for
real; both lists are below so a future attempt starts from measured facts.

**Blocker A (ELF only, and it is exactly `DumpResult`'s own documented
"Lifetime caveat" made live rather than latent).** `perform_elf_dump`
passes the CLI's *real* `collect_mode` to
`seed_includes_and_fold_compile_context`, which sets
`allow_inferred_build_query=collect_mode != "off"` (`buildsource/l2_seed.py`)
and therefore genuinely returns non-empty `pending_cleanups` — the
temporary build directory a zero-config *inferred* build-system query
seeded, whose generated headers the seeded include dirs point at.
`perform_elf_dump` drains that plan in a `finally` placed deliberately
**after** its two post-processing second passes (`service._attach_header_
graph` and `workflows.extraction.attach_clang_layout`), and its own inline
comment states why in as many words: "the header-graph pass above (when
requested) reuses the same seeded include dirs the main `dump()` parse
used, so cleanup must wait until it ... has run". `_resolve_side_snapshot_
impl` drains in a *nested* `finally` immediately after
`service.resolve_input`, and its own comment states, equally explicitly,
why it must: `embed_side_build_source` runs its own inferred query inside
the same call, and an undrained seed still holds the deterministic
per-source-tree build dir under an exclusive `flock`, so a later drain
makes the second query self-contend for up to `INFERRED_QUERY_TIMEOUT_S`
(600s) — the identical self-contention shape recorded as the fifth finding
on the L3→L2-fold entry. The two requirements are in **direct conflict**
the moment `perform_elf_dump`'s parse routes through that primitive: today
they don't conflict only because `perform_elf_dump` runs no embed inside
its own plan. Deferring the seed cleanups back out to the CLI caller (an
additive `defer_seed_cleanup` pass-through, the obvious-looking fix) is
precisely what re-creates the 600s contention; draining them where the
shared primitive does is precisely what deletes the directories the two
second passes still need to re-parse headers under. `DumpResult`'s own
docstring already names this ("safe for *identity or comparison* ... a
caller intending to re-read a file under one of these paths ... cannot yet
do so safely"), and already scopes the fix as PR 3A's pair-aware/lifetime
redesign — a separate piece of work, not a control-flow rewrite. Weakening
or disabling either second pass to dodge it was considered and rejected:
each exists because of its own recorded Codex-review regression (a second
clang pass silently degrading to a declaration-only graph, and a
`dump --ast-frontend clang` baseline silently carrying no layout-tool
facts).

**Blocker B (both ELF and PE/Mach-O).** `execute_dump_request` is a
resolve **+ embed + enforce** pipeline: `_resolve_side_snapshot_impl` runs
`embed_side_build_source` (L3-L5) inline, `service.resolve_input` →
`run_dump` applies `dumper_scoping.resolve_dependency_scope` from
`InputSpec.include_dependencies`, and `execute_dump_request` then calls
`enforce_requested_depth`. The `dump` CLI does all three of those things
**after** the parse and after provenance stamping, in
`cli_buildsource._write_snapshot_output`: `embed_build_source` (guarded by
`build_source_already_satisfies`), then `check_requested_depth_satisfied`,
then `resolve_dependency_scope(snap, include_dependencies, header_roots)`.
Routing the primary parse through `execute_dump_request` therefore reorders
all three relative to the CLI's own post-parse pipeline, with three
concrete consequences, none of them cosmetic: (1) the ADR-039 build-context
reconciliation, the header-graph attach and the clang-layout attach would
run over an *already dependency-scoped* snapshot (`--include-system-
declarations` defaults off, so `InputSpec.include_dependencies=False` is
the common case, and the inner scope call has no access to the write path's
`header_roots` set at all); (2) the depth floor would be enforced against
only the *inner* embed's result, before `_write_snapshot_output`'s own
embed — the one that actually fills L3-L5 for a `dump` today — has run; and
(3) that floor raises `ValidationError` where the CLI's own
`check_requested_depth_satisfied` raises a Click error, a different
user-facing message and exit code for the identical input. Making this
safe means either suppressing three behaviors inside a shared Tier-2
primitive for one caller (inventing a code path, which this entry's own
convention forbids) or moving the CLI's write-time embed/enforce/scope
stanza to resolution time — a real, separately-reviewable redesign of
`_write_snapshot_output`'s contract, not part of the routing.

**Ruled out, with evidence, so they are not re-litigated.** (i) The
P0.3 fold's fourth return value (`l3_include_dirs`, which
`perform_elf_dump` folds into `extra_hash_dirs`) is *not* lost: the folded
context's own tokens carry those dirs, and `service_dump_native._dump_elf`
independently recomputes the same set via
`cache_relevant_operand_paths(cc.gcc_option_tokens)`. (ii) The P3
inferred-header-root derivation (`resolve_inferred_header_roots` →
`inc_extra`/`deferred`/`deferred_dirs`) is *not* lost either — `_dump_elf`
performs the identical derivation itself. (iii) `debug_info_path` is not
lost: `_dump_elf` resolves it from `debug_roots`/`enable_debuginfod`, both
of which `build_dump_request` already puts on the `InputSpec` (it would be
resolved *twice*, once by `dump_cmd` for its echo and once here, which is
wasteful and double-logs but is not a correctness gap). (iv) The
whole-snapshot cache (`resolve_input` → `cached_run_dump`, which
`perform_elf_dump`'s bare `dump()` bypasses) does **not** newly activate:
`build_dump_request` always sets `InputSpec.compile` to the CLI's resolved
`CompileContext`, and `service_dump_cache._dump_is_cacheable` refuses to
cache any call with a non-`None` `compile`. (v) `follow_deps` on PE/Mach-O
is not a divergence: `populate_side_dependency_info` is documented and
implemented as an ELF-only no-op. (vi) `ast_memoize_scope()`/
`suppress_streaming_prune()` are trivially preservable — the caller can
wrap the `execute_dump_request` call itself. (vii) `handle_non_elf_dump`
has **no** Blocker A: it runs no post-processing second pass and already
drains its plan in a `finally` immediately after the parse, exactly where
the shared primitive does. It is blocked by B alone, which is why
converting "the small one first as a warm-up" does not in fact isolate a
safely-landable slice.

**Net**: the remaining piece of this entry is *not* control-flow-only.
Closing it needs (a) PR 3A's already-scoped pair-aware/lifetime redesign of
the L2 seed's cleanup ownership, so a caller with post-parse hooks can keep
the seeded dirs alive without the embed step self-contending on their lock,
and (b) a decision about where `dump`'s L3-L5 embed, depth enforcement and
dependency scoping belong — resolution time (matching the typed pipeline)
or write time (matching today's CLI) — since the two cannot both be true of
one code path. Recorded at this precision, per this file's own convention,
so the next attempt starts from the mechanism rather than re-deriving it.

**The real ELF `dump` run is migrated (CLI cleanup phase two, PR C).**
Decision (b) above is resolved as **split, not uniform**: only the L3-L5
embed moves to resolution time (`execute_dump_request`); depth enforcement
and dependency scoping stay at write time, unchanged, in
`_write_snapshot_output`. This became possible only because two
prerequisites this entry's own "Blocker A"/"Blocker B" analysis called
for were separately closed first, in the plan doc's own PR 3A subsection
(2026-08-27/28, "Investigated further"/"Update"): a real, end-to-end test
(`tests/test_dump_write_after_resolve_time_embed.py`) confirmed the
depth-gate/provenance/dependency-scope half of `_write_snapshot_output`'s
sequence already handles a resolve-time-embedded snapshot correctly with
*no* code change (the two depth checks share the identical
`evidence_depth.gated_source_label`/`depth_rank` primitives, so calling
both is redundant, not wrong), and the Flow-2 `--inputs` pack fold was
separately verified safe layered on top of a resolve-time embed too. That
closes Blocker B's "resolve + embed + enforce" concern for the
*embed* alone — depth enforcement is not moved, so its own reordering
hazard never applies; **Blocker A (the seed-cleanup self-contention) is
independently a non-issue for `dump`'s real invocation shape**, because a
`dump` CLI request's own `header_backend`/`allow_build_query` inputs never
produce a resolve-time seed with non-empty `pending_cleanups` for a
request that also carries a compile database or a pack `--sources`/
`--build-info` value under the shapes this migration's own tests exercise
(`build_source_already_satisfies` already accounts for the common,
compile-DB-backed case) — the two ELF-specific post-processing second
passes (`_attach_header_graph`, `attach_clang_layout`) that Blocker A
worried about run *inside* `service.resolve_input`'s own ELF dispatch
(`workflows/dump/native.py`), not as a separate stage `execute_dump_request`
adds on top, so they already see the seeded dirs before that dispatch's
own cleanup drains them — the same ordering `perform_elf_dump` itself
used to hand-maintain, now owned by one implementation instead of two.
One structural nuance from Blocker B's own concern (1) is real and
*not* separately reasoned away here, only measured: `service.run_dump`'s
own choke point (`dumper_scoping.apply_dependency_scope_to_run_dump_
result`) already dependency-scopes the snapshot *before*
`_resolve_side_snapshot_impl`'s own ADR-039 collector/header-graph/
clang-layout attaches run on it (this is the shared pipeline's own
existing, pre-migration behavior for `compare`/`scan`, not something this
migration introduces) — so `_write_snapshot_output`'s own, unchanged
`resolve_dependency_scope` call at the end is a second, write-time pass
over an already-once-scoped snapshot rather than the sole pass it used to
be. Confirmed idempotent for every shape this migration's own parity
suite exercises (both calls scope against the same effective header-root
set), not proven idempotent in general.
`frontends/cli/commands/dump.py`'s real (non-`--dry-run`) ELF branch calls
a sibling module, `frontends/cli/dump_execute.py` (split out purely to
keep `dump.py` under the architecture gate's 800-line production-file cap
— ADR-061 freezes the flat `cli_*.py` root family's member list, so a
genuinely new module goes into its responsibility-package tree, alongside
`runtime.py`/`artifact_set_dry_run.py`, instead), which builds a second,
execution-scoped `ResolvedDumpRequest` from the same
`DumpRequest` `--dry-run` already resolves — re-pointed at the
post-linker-script-following `so_path` (`resolve_dump_request`'s own
`detect_binary_format` call runs before any such following, so feeding it
the pre-follow path risked a wrong `fmt` for a symlink-to-linker-script
input) and with `requested_depth` nulled out (so `execute_dump_request`'s
own `enforce_requested_depth` — a *different*, more generically-worded
`ValidationError` than `check_requested_depth_satisfied`'s
`DumpDepthNotSatisfiedError` — never fires; `_write_snapshot_output`'s own
call stays the sole, unchanged enforcement point for this case, preserving
`tests/test_depth_vocabulary.py`'s pinned message) — and calls
`execute_dump_request(exec_resolved, legacy_compile_db_tokens=...,
legacy_compile_db_matched=...)`, threading the legacy `-p`/`--compile-db`
auto-match through as the explicit pass-through ADR-063 Phase 1 already
built for exactly this purpose (`execute_dump_request`'s own docstring)
rather than porting it into `InputSpec` as a first-class field — the
pass-through already implements the P0.3-fold-wins precedence rule
`perform_elf_dump`'s own `_fold_explicit_gcc_options` hand-rolled, so
adding a second, dataclass-shaped representation of the identical fact
would be a second place for it to drift, not a cleaner one.
`perform_elf_dump` itself is retired from this call site (still defined,
in case any other caller depends on it, but `dump_cmd` no longer imports
it); `handle_non_elf_dump` (PE/Mach-O) is untouched — no PE/Mach-O
toolchain was available in this environment to verify a migration
against, so it stays exactly where this entry's "Blocker B (both ELF and
PE/Mach-O)" heading already scoped it: open.

**Update (2026-09-01, PR #980): PE/Mach-O is now migrated too, closing
this entry's remaining half.** The design this entry already worked out
for ELF (null out `requested_depth` before calling
`execute_dump_request`, keep `_write_snapshot_output`'s embed/enforce/
scope stanza as the sole enforcement point, thread the legacy `-p`/
`--compile-db` auto-match through as an explicit pass-through) carried
over to PE/Mach-O mechanically, with no second structural investigation
needed — `execute_dump_request`/`_resolve_side_snapshot_impl` were
already format-generic (`is_elf=True if fmt == "elf" else None`,
`attach_build_context_for_parsed_headers`/`embed_side_build_source`
called unconditionally regardless of format), the same pipeline
`compare`'s implicit-dump operand and `scan`'s candidate resolution
already used for PE/Mach-O input. `handle_non_elf_dump` is retired from
the CLI's real dispatch the same way `perform_elf_dump` was (still
defined, for its own direct unit tests). **Verified only via mock-based
CLI/unit tests, not a real end-to-end parity run** — no PE/Mach-O
toolchain was available in this environment either, so unlike ELF's own
`test_dump_cli_typed_api_parity.py` corpus, there is no byte-for-bit
confirmation against a real compiled DLL/dylib. `AGENTS.md`'s own
`service_dump_pipeline.py` entry is updated to match.

One real, user-visible behavior change falls out of the migration rather
than being a side effect nobody decided: `dump`'s L4 source-extractor
default flips from an accidental **clang** (`perform_elf_dump` forwarded
the bare, unresolved `header_backend` straight to the write-time embed,
which treats anything but the literal string `"castxml"` as clang) to
**castxml** (the shared pipeline's `effective_frontend` resolution,
matching `compare`'s implicit-dump operand, the typed `DumpRequest` API,
and `dump`'s own L2 header-AST default) — this is the plan's own
"item 3" castxml L4 phantom-implicit-member bug's actual payoff: that fix
(`Function.is_compiler_generated`, elsewhere in this file) is exactly what
makes this default safe to inherit now, where an earlier investigation in
this same entry explicitly deferred it for being unsafe before that fix
existed. `--ast-frontend clang` recovers the previous default for a caller
that needs it. The sibling `scan`-vs-`dump`/`compare` L4 extractor default
divergence (the plan's own "item 2") is **unchanged by this migration** —
`scan_engine._build_new_snapshot` still hardcodes `source_extractor="auto"`
(resolving clang) via its own opt-in override on the shared primitive,
deliberately preserved when `scan`'s candidate resolution was migrated
onto the same primitive; that remains its own separate, deferred decision.

> **Update (2026-09-02): item 2's *explicit-request* half is now closed;
> only its unflagged-default half remains.** The paragraph above stays
> accurate as a record of *that* migration, but the hardcoded
> `source_extractor="auto"` it describes is gone — `scan_engine`'s call
> site now passes `service_compare_evidence.explicit_source_extractor(
> compile_context) or "auto"`. Full account, including why this was safe
> where the earlier "flip it to `effective_frontend`" attempt (recorded
> above) was not, and exactly what is still open: the plan's own **item 2**
> in
> `cli-cleanup-phase-two.md`, which was the
> narrative owner for this item's status. **Update (2026-09-06): that plan
> is closed and this item dissolves rather than closing** — ADR-068 retires
> `scan`, so the unflagged-default half disappears with `scan_engine`
> itself; the full investigation narrative is in that file's git history
> (last full-length revision `a92a4a89`). The one thing worth keeping here,
> since it is what this file exists for: the reverted attempt was reverted
> for surfacing the castxml L4 phantom-implicit-member bug, and that bug
> (`Function.is_compiler_generated`, elsewhere in this file) being fixed is
> what made the request half tractable at all.

Verified: the full fast unit suite; the real-toolchain (`g++`/clang/
castxml) integration suite for this area
(`test_dump_cli_typed_api_parity.py`'s 16 cases — `_CONTRACT_KNOWN_
DIVERGENT_FIELDS` stays empty, i.e. zero remaining divergence between the
migrated CLI path and the typed pipeline — plus `test_dump_scan_l3_
comparability.py`, `test_dump_write_after_resolve_time_embed.py`,
`test_dump_embed_idempotence.py` — updated to count the resolve-time embed
call site too, not just the write-time fallback — `test_compile_db_
filter_scope.py`, `test_dry_run_contract.py`, `test_dry_run_build_query_
contract.py`, `test_l2_seed_flow2_packs.py`,
`test_scan_adr039_build_context.py`, `test_castxml_l4_phantom_members.py`,
`test_dump_depth_provenance.py`, `test_depth_vocabulary.py` — 2 xfails,
the same pre-existing, already-documented `_SCAN_KNOWN_DIVERGENT_FRONTENDS`
signature, unchanged); `mypy`/`ruff` clean on the touched modules.

> **Update (Block 7, PR C's own tail): the explicit-`--config`
> dry-run/execution parity residual named throughout this entry is now
> closed.** `api_types.InputSpec` gained a `build_config` field mirroring
> `ScanRequest.build_config` — `cli_dump_request.build_dump_request` sets
> it from `dump`'s own `--config`, and `cli_compare_helpers`/
> `frontends.cli.commands.compare`'s inline `--old/new-sources` embed path
> (its own nested `ctx.invoke(dump_cmd, ...)`) sets it from `compare`'s own
> raw `--config` value too. `workflows.plan.SidePlan.build_config` carries
> it into `_check_bazel_target_scoping`, which now calls
> `bazel_target_scoping_failure(..., build_config=side.build_config, ...)`
> instead of always passing `None` for `dump`/`compare` — so the shared
> `AnalysisPlanner.resolve` chokepoint both `--dry-run` and the real run
> go through sees the same explicit config `embed_build_source` already
> honored at real-execution time via its own `cfg_path = build_config or
> discover_build_config(raw_sources)` precedence, instead of only ever
> seeing whatever `.abicheck.yml` auto-discovery found at `sources`. Does
> not change what actually executes: `embed_build_source` still receives
> the CLI's own raw `--config` value out-of-band, exactly as before; this
> only widens what the pre-flight resolution (and therefore `--dry-run`)
> can see. The `--dry-run` `build.query` trust receipt
> (`add_build_query_dry_run_section`) was already correct before this
> change and is untouched.
>
> **A security review on the PR landing this (#1089) caught a real
> regression in the first version of the fix, worth recording precisely
> since it is exactly the class of bug ADR-032 D5 exists to prevent.** The
> first attempt forwarded `compare`'s already-*resolved* `cfg_path`
> (`config if config is not None else discover_project_config()` —
> conflating an explicit `--config` with `_resolve_compare_config`'s own
> auto-discovery fallback) into the nested `dump_cmd` invocation's
> `build_config` parameter, instead of the raw, explicit-only `config`
> value. `embed_build_source`'s `cfg_trusted_for_query = build_config is
> not None or build_query is not None` treats any non-`None` `build_config`
> as operator authorization to execute `build.query` — so that version
> would have let a `compare --sources old=<tree>` invocation with *no*
> `--config` at all execute an arbitrary `build.query` command from a
> `.abicheck.yml` merely auto-discovered from the working directory or the
> sources tree being compared, i.e. from content a reviewed PR's own
> checkout can control. Fixed by forwarding the raw `config` parameter
> (`run_compare`'s own un-resolved `--config` kwarg — `None` unless the
> operator actually typed it) instead of `cfg_path`. Verified via
> `tests/test_compare_inline_embed_config_trust.py`'s
> `test_auto_discovered_config_is_not_forwarded` (fails against the
> vulnerable version — asserts `build_config` stays `None` when only an
> auto-discovered config exists) and `test_explicit_config_is_forwarded`
> (positive control — an operator-supplied `--config` still reaches the
> nested invocation, so the fix doesn't overshoot into never forwarding
> one at all). `dump`'s own `--config` was never subject to this: its
> `build_config` local is always the literal, explicit-only Click flag
> value, with no auto-discovery fallback at that layer.
>
> Verified via `tests/test_analysis_plan.py`'s
> `test_dump_request_honors_explicit_build_config_over_auto_discovery`/
> `test_compare_request_honors_explicit_build_config_over_auto_discovery`
> (request-level, through `AnalysisPlanner.resolve` directly) and
> `tests/test_bazel_root_targets.py`'s
> `test_dump_cli_explicit_config_scoping_matches_dry_run_and_real_run`
> (end to end through the CLI, asserting `--dry-run` and the real run
> agree); the full fast unit suite; `mypy`/`ruff` clean on every touched
> module. Directory/package `compare`'s release fan-out was left
> untouched — out of this residual's stated scope (the plan names
> `dump`/`compare`, the single-pair commands).

### `scan_abi3_resolve.py` is a new flat `workflows`-legacy root module whose own docstring says its placement exists specifically so `scripts/check_architecture.py`'s `unclassified-import` check won't inspect its dependency on `serialization.py` (Codex review, PR #951, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): MOOT.** abicheck/scan_abi3_resolve.py and workflows/scan_abi3* no longer exist (deleted with scan by ADR-068). The serialization/probe_harness halves were already recorded as fixed.

The module needs both `python_ext`
(`detect_python_extension_from_binary`) and `serialization`
(`load_snapshot`, for the snapshot-input fallback) to answer `scan
--dry-run --abi3`'s candidate-recognition question the same way the
real run's `service.resolve_input` does; `serialization.py` already
imports `python_ext` (for `PythonExtMetadata`/the `detect_python_extension`
backfill), so `python_ext` importing `serialization` back would form a
real two-module cycle, and a small module living outside both,
importing each, is the correct general shape. Codex is right that
keeping it flat rather than moving it under the migrated
`abicheck/workflows/` package (alongside `scan_abi3_dry_run.py`, its
only caller) sidesteps a check rather than satisfying it. **Investigated
the real fix and it doesn't fit in this PR**: `serialization.py`
matches `storage`'s own described responsibility (AGENTS.md's module
map: "snapshot serialization ... the public compatibility surface"),
so classifying it under `architecture/modules.yaml`'s `storage` layer
looks like the natural move -- but `storage`'s `may_import` is
`["model"]` only, and that reclassification immediately surfaces two
already-latent violations a purely-unclassified `serialization.py`
currently hides from `check_architecture.py`'s `dependency-direction`
check entirely (an unclassified import target is skipped by that check,
by design): `abicheck/probe_harness.py` (classified `compare`, whose own
`may_import` is `["model"]` only) already calls
`serialization.snapshot_to_dict`/`snapshot_from_dict` at runtime, and
`serialization.py` itself imports `python_ext` (classified `extract`)
at runtime for the same reason `scan_abi3_resolve.py` does. Unlike
`check_ai_readiness.py`'s `import-cycle-growth` check,
`dependency-direction` has no allowlist mechanism to grandfather either
violation while the reclassification lands. **Both fixed.**
`serialization.py`'s own `python_ext` coupling closed first (ADR-061 gap
E closure package 5, slice 1, 2026-09-07): the `detect_python_extension()`
backfill moved out of `serialization.py` into
`abicheck/workflows/snapshot_load.py::backfill_python_ext_from_evidence()`,
called as an explicit post-load step -- `serialization.py` no longer
imports `python_ext` at all (it imports `workflows.snapshot_load`
instead, and `python_ext_from_dict` from `.model`, a plain dataclass
parser, not the `extract`-classified `python_ext.py`). The
`probe_harness.py` half closed second (slice 2, 2026-09-08): its
snapshot round-trip (`.to_dict()`/`.to_json()`/`.from_dict()`,
`write_matrix_snapshot`/`load_matrix_snapshot`) moved out of
`probe_harness.py` entirely into `abicheck/workflows/findings.py`, which
may legitimately import both `compare` (for the dataclasses) and
`serialization`. `probe_harness.py` no longer calls
`serialization.snapshot_to_dict`/`snapshot_from_dict` at all. Neither
latent violation this entry named is still open. `serialization.py`'s own
~1500-line `snapshot_to_dict`/`snapshot_from_dict` codec proper has since
been given a real owner too (ADR-061 gap E closure package 6): it moved to
`storage/snapshot_codec.py` and four siblings
(`snapshot_schema_versions.py`, `snapshot_encode.py`,
`snapshot_decode_declarations.py`, `snapshot_reliability_flags.py`), each
kept under the ADR-061 new-file production line ceiling since a brand-new
file gets no adoption-debt baseline exemption. `serialization.py` itself
is confirmed (not just documented) to stay `public_root_surfaces`-listed
permanently, for the same "no single layer" reason ADR-061 gap B's own
closure status already established for `checker_policy`/`contract_gating`/
`reclassify`: it is the one legal route through which
`workflows.snapshot_load.backfill_python_ext_from_evidence` and
`policy.analysis_assurance_degraded_facts.degraded_reliability_facts` run
between `storage.snapshot_codec.decode_snapshot` and
`storage.snapshot_codec.finalize_snapshot` -- neither call is legal from a
`storage`-classified module (`may_import: [model]` only), and
`dependency-direction` resolves a dynamic `importlib.import_module` call
against a classified target the identical way it resolves a static import
(only an *unclassified* target, like this facade, is skipped by design),
so there is no bridge-module trick available for either. Verified against
the full architecture gate (`scripts/check_architecture.py`) with zero new
findings. Left `scan_abi3_resolve.py` in its current, self-documenting flat-legacy
placement (its own docstring already states the reason and the
precedent it follows) as accepted debt -- that placement was never about
`serialization.py`'s own classification, so this closure does not change it.

### ADR-063 Phase 3 (D5)'s traversal migration went through three review rounds before landing on a design that reads `AbiSnapshot.surface_graph` never at all for the closure walk — the history is worth keeping because each round's fix created the next round's bug

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry records itself as resolved: graph read removed from the closure walk, and the perf gate on PR #979 commit 5544540 succeeded. The text is history only.

Round 1 (the original
migration): `policy/public_surface_closure.py` read a graph node's
`referenced_identifiers`/`identifiers_collision` attrs, stamped once at
graph-build time by `compare/surface_graph.py`. Round 2 (Codex, PR #979):
`snap.surface_graph` being non-`None` does not mean its nodes carry those
attrs at all — `service_header_graph_attach._attach_header_graph` installs
an L5 graph on essentially every real dump without ever populating them —
so trusting an attrs-less node as "references nothing" silently collapsed
the transitive closure on the *ordinary, default* dump path. The fix
(`resolve_surface_graph_nodes()` unconditionally calling
`build_public_surface_facts()` to enrich/backfill the graph before
reading it) introduced two further problems of its own: (a) a genuine,
measured 30-100%+ performance regression against `scripts/
benchmark_scaling.py`'s "Baseline regression (PR vs base)" CI gate,
because `checker.compare()`'s default `scope_to_public_surface=True`
calls this path twice per compare (once per side) and building real
`GraphNode`/`GraphFact` objects through the ADR-046 evidence-merge
machinery for every declaration is meaningfully more expensive per-call
than the deleted regex-based re-parse it replaced; and (b) Round 3
(Codex, second security-focused round): even *with* enrichment, a
schema-v29 (or otherwise untrusted/adversarial) snapshot could carry a
stale or crafted `referenced_identifiers` fact at a confidence this
module's own freshly-registered fact (always `CONF_UNKNOWN`, the lowest
rank in `model.graph_vocabulary._CONFIDENCE_RANK`) cannot outrank —
`model.graph_facts.merge_graph_facts`'s per-key precedence would let the
stale/poisoned value silently win over the correct, current one,
reproducing the exact same collapsed-closure failure mode as round 2,
just reachable through the round-2 fix instead of around it. An
identity-keyed cache was also tried, purely to close the perf
regression from (a), and reverted separately: it broke
`tests/test_export_surface.py::TestUnresolvedTypeEdges::
test_a_scope_lost_alias_key_is_followed_to_its_target`, which mutates a
snapshot's `typedefs`/`types` in place between two calls and correctly
expects the second to see the new content.

**Fixed, for real, by removing the graph from this computation
entirely** rather than trying to make trusting it safe. Both the
attrs-staleness hazard (round 2) and the evidence-merge-precedence
hazard (round 3) share one root cause: trusting anything cached on the
shared, evidence-mergeable graph for a value that has exactly one
legitimate source (the snapshot's own current declarations) and no
legitimate second producer to reconcile evidence with. Once that was
understood, the fix stopped being about merge precedence or caching at
all: `compare/surface_graph.py`'s own `referenced_identifiers_by_node()`
(renamed public, alongside its `ReferencedIdentifiers` return type) was
already a pure function of the snapshot's declarations, computed
*before* any `GraphNode` is even built — `policy/
public_surface_closure.py` and `export_surface.py`'s closure-walk entry
points now call it directly and thread the result through
(`_referenced_identifiers`/`_node_identifiers_or_collision`/
`_seed_public_roots`/`_walk_type_closure`/`_walk_exact_type_closure` all
take a `ReferencedIdentifiers` now, not a `dict[str, GraphNode]`), never
touching `snap.surface_graph` or `GraphNode.attrs` at all.
`resolve_surface_graph_nodes()` (the round-2 enrichment function) had no
remaining caller once both call sites switched, and was deleted rather
than kept as unused surface. This closes the security concern outright
(nothing is ever merged, so there is no precedence for an adversarial
fact to win) and, as a direct consequence, removes essentially all of
the `GraphNode`/`GraphFact`/evidence-merge construction cost from the
hot path too — an ad hoc local re-run of `scripts/benchmark_scaling.py`
after this fix showed `add_remove@2000` and `type_churn@1000` (two of
the scenarios the perf gate had flagged) back in line with or better
than the pre-migration baseline numbers quoted in the gate's own
failure output. **Confirmed by CI itself, not just the local re-run**:
the `Performance` workflow's own "Baseline regression (PR vs base)" job
(PR #979, commit `5544540`) completed with `conclusion: success` — the
gate's own noise-controlled PR-vs-base measurement, not an ad hoc local
timing, so this entry is resolved rather than an open gap. Left in this
history for the multi-round record: three review rounds on one commit
chain, each fix closing the previous round's hazard while (in round 2's
case) introducing this one.

### `action/run.sh` has no "effective output path" counterpart to `_effective_format` — investigated, deliberately not fixed (Codex review, PR #998, fresh evidence)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** action/run.sh:2532 now defines _effective_output_file, the effective -o/--output counterpart, and :2276 says _attached_output_value is used exactly as _effective_output_file does.

`extra-args` supplying its own `-o`/`--output`
(`abicheck/cli_options.py`'s `-o/--output`) is a different flag than
`--format`, and Click's last-flag-wins rule applies to it exactly the same
way: an `extra-args: -o report.json` on top of an Action run with no
`output-file:` input configured really does write the primary report to
`report.json` on disk instead of stdout — but `$OUTPUT_FILE` (this
script's own tracking variable, sourced only from `INPUT_OUTPUT_FILE`)
never learns about it, so `_json_report_src` finds nothing: not
`$OUTPUT_FILE` (empty), not `$_STDOUT_JSON_FILE` (nothing on stdout, since
`-o` redirected it), not `$PR_JSON` (only populated when this script's own
injection fires). A scan or compare that exits non-zero this way (e.g.
`EVIDENCE_CONTRACT_ERROR`) publishes the generic `ERROR` instead of the
real, more specific verdict its own report on disk could have named.
**Not fixed here**, for the same "coordinated primitive, not a narrow
patch" reason as the pathname-expansion gap above: `--write` already has
its own effective-value recovery (`_extra_args_write_json_path`), but
`-o`/`--output` has none, and building one properly means giving it the
same freshness/fingerprint discipline `_json_report_src` already applies
to `$OUTPUT_FILE` (a pre-existing file at the extra-args path must not be
trusted as this run's own output) — a new `_effective_output_file` helper
and its own test suite, not a one-line change to a single call site.

### `--depth` is a floor for live extraction, not a ceiling for a pre-built snapshot — real, cross-cutting, and previously undocumented outside one function's own docstring. Fixed (ADR-063 Phase 8 follow-up): the comparison-time projection this entry's own "real fix" paragraph called for is now `abicheck.policy.depth_projection.project_snapshot_to_depth`, applied by `classify_compare_pair` right after `enforce_requested_depth` confirms the floor

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** abicheck/policy/depth_projection.py exists (project_snapshot_to_depth), and the entry records it as fixed along with its follow-up review rounds.

`enforce_requested_depth`
(`workflows/artifact/execute.py`) already fails a run when the *resolved*
evidence falls short of an explicit `--depth`, and its own docstring has
long carried this note: "this is a floor, not a ceiling. An input that is
an already-serialized JSON snapshot with richer embedded evidence than
`depth` requested still carries all of it — `resolve_input`'s `fmt ==
"json"` branch returns `load_snapshot(path)` verbatim... which `--depth`
has never projected down for a pre-built snapshot either." A Codex review
round on PR #1016 (D1: accepting `--depth binary` for a directory/package
compare) reproduced this concretely and flagged it as if newly
introduced: `compare old_dir new_dir --depth binary` over a directory of
saved JSON snapshots (rather than live binaries) still emits real
header-derived findings and can still publish `BREAKING`, because nothing
strips a snapshot's already-embedded evidence down to what was requested.
Checked and confirmed **not** a regression from that PR — a *single-pair*
`compare old.json new.json --depth binary` over two plain snapshots
reproduces the identical behavior today, unrelated to any directory/
package handling; PR #1016 only extended `--depth binary`'s
*acceptance* to a second operand shape that inherits a limitation the
single-pair path has always had. `cli_compare_options.
_resolve_depth_for_set_inputs` (named `_reject_depth_for_set_inputs`
until its per-rung allow-list was deleted -- see "A depth shortfall is a
hard per-member `ERROR` ..." below) cross-references this
entry so the directory/package path states the same acknowledged
limitation explicitly rather than silently inheriting an undocumented
one.
**The two obvious-looking fixes below were, and remain, correctly
rejected — the eventual fix is neither of them, kept here so both stay
un-reattempted:** stripping a resolved snapshot's higher-level facts down
to the requested depth *before* comparing would work for this one call
site, but would also discard evidence a caller legitimately wants to keep
on a snapshot that gets reused for a *later* comparison at a higher depth
— `--depth` is meant to gate what a comparison *uses*, not to mutate a
snapshot's own persisted content. Rejecting `--depth binary` outright for
any operand backed by a pre-built snapshot (matching the pre-#1016
directory/package behavior) would reintroduce exactly the asymmetry D1
closed, since the single-pair path already accepted and silently
under-enforced the same combination.

**The fix actually shipped is exactly the comparison-time projection this
entry called for**: resolve the snapshot as before, then filter what
`checker.compare()` is allowed to see down to the requested rung, keeping
the resolved `AbiSnapshot` itself untouched.
`abicheck.policy.depth_projection.project_snapshot_to_depth` is that
filter — pure (returns a deep copy), mapped onto the public
`binary`/`headers`/`build`/`source` ladder via the exact same rank table
(`evidence_depth.DEPTH_RANK`) this entry's own "applied the other
direction" phrase named, and validated against the one prior
already-trusted reference implementation of this idea:
`scripts/check_tier_accuracy.py`'s `project()`, which the per-tier
accuracy gate runs against a real (if synthetic) labelled corpus.
`classify_compare_pair` (`service_compare_pipeline.py`) applies it (via
`project_pair_to_depth`) to a local `old`/`new` view, gated on
`request.depth is not None` — the same "no explicit depth, no effect"
contract `enforce_requested_depth` already has — right after that
function confirms the floor; `pair.old`/`pair.new` themselves are never
mutated, so a caller reading the unprojected snapshot elsewhere is
unaffected. `dump` deliberately does **not** apply this at write time,
for the exact reason given above (it would discard evidence a later,
deeper comparison might want) — `--depth` on `dump` stays floor-only,
unchanged. See `project_snapshot_to_depth`'s own docstring for the
precise field-by-field scope (visibility/origin scoping, macro/constexpr
constants, the Python-API stub surface, the header-AST `SemanticIR`, the
header-only `surface_graph`, `build_mode`, the `BuildSourcePack`
L3/L4/L5 split; below `headers` with no DWARF backing (`dwarf.has_dwarf`
false), `types`/`enums`/`typedefs`/signatures are fully stripped too,
not merely re-scoped — matching a real DWARF-less binary-only dump
exactly, per `extract/export_symbol_identity.py`'s own production
behavior).

**A second review round (Codex, same PR) found two more real gaps in the
first cut of this fix, both now closed:** (1) the projection was wired
only into `classify_compare_pair` — the typed-API/release-fan-out
chokepoint — while the *native* `abicheck compare` CLI
(`cli_compare_helpers.run_compare`) and `scan --against`'s baseline path
(`cli_scan_baseline._run_baseline_compare`) each call
`compare_snapshots()` directly and never saw it; both now apply
`project_pair_to_depth` themselves, right after resolving their own
pair, mirroring `classify_compare_pair`'s placement. (2) the `binary`
rung's structural-fact handling was fixed at DWARF-informed (L1)
behavior unconditionally — a purely header-derived snapshot with no
DWARF at all still leaked full `types`/`enums`/function-signature data
through a `binary`-depth projection; `_snapshot_has_native_debug_info`
now picks L0 (fully stripped) vs. L1 (structural facts kept) per
snapshot, not fixed to one or the other.

**A third review round (Codex, same PR) found four more real gaps, all
now closed:** (1) an explicit out-of-band `--old/new-sources`/
`--old/new-build-info` pack directory is resolved *separately* from the
snapshot object `project_pair_to_depth` projects — `cli_compare_helpers.
run_compare` passed the raw pack paths straight through to
`prepare_embedded_build_source`, which reloads and diffs them
unconditionally, so a pack-backed `compare --sources ... --depth binary`
still leaked full L3-L5 findings even after the first two rounds' fixes.
Closed with a new `project_build_source_pack_to_depth` (mirroring
`project_snapshot_to_depth`'s own `build_source` capping, factored into a
shared `_project_build_source_pack` helper both call) — `run_compare` now
resolves each side's pack itself, caps it, attaches the capped pack back
onto `old.build_source`/`new.build_source`, and passes `None` for the
four path arguments so `prepare_embedded_build_source`'s own
`resolve_side_pack` falls back to the now-capped embedded payload instead
of reloading the raw one; the same fix also corrected the *reporting*
side (`analysis_assurance`/`old_evidence_depth`/`new_evidence_depth`),
which previously re-resolved the uncapped pack from the raw paths a
second time for these fields even after the findings themselves were
capped. (2) `snap.contract` (an ADR-050 `ExtractionContract`) survived a
`binary`-depth projection, so `checker.compare()` could still raise a
scope/profile mismatch error from two sides' *original* header scopes
even though a binary-only comparison never looks at either — now cleared
alongside the other L2+ facts. (3) a `Visibility.HIDDEN` (non-exported)
function/variable was promoted to `ELF_ONLY` like every other function/
variable, manufacturing a false `*_removed_elf_only` finding for a
declaration no real binary-only dump would ever have seen as a symbol at
all — now dropped from the projected snapshot entirely instead. (4)
`types`/`enums` were kept or dropped by the whole-snapshot
`dwarf.has_dwarf` flag, the same coarse signal functions/variables still
use — but this codebase's model *does* carry real per-record DWARF
evidence for these two families (`DwarfMetadata.structs`/`.enums`, keyed
by name), so an uninstantiated header-only record sitting alongside
unrelated real DWARF content was incorrectly retained; a new
`_dwarf_confirmed_names` filtered `types`/`enums` per declaration name
instead of by the whole-snapshot flag. **Superseded by the fourth review
round below** — the per-record name check turned out to be one level too
narrow.

**A fourth review round (Codex, same PR) found three more real gaps, all
now closed:** (1) the third round's per-record `_dwarf_confirmed_names`
fix retained a *header-derived* `RecordType`/`EnumType` wholesale
whenever DWARF merely confirmed the same-named struct/enum *existed* —
DWARF confirming a struct's name says nothing about whether its *fields*
agree with the header's own spelling (DWARF only ever backfills numeric
*layout* onto a header-derived record — `dumper_layout_backfill.py`'s
own scope — never its field-level type text), so a header-only field-type
change could still leak through a `binary`-depth projection whenever an
unrelated real DWARF struct happened to share the changed struct's name.
Fixed by replacing the per-record name check with a whole-snapshot
`not snap.from_headers` requirement (`_structural_facts_are_dwarf_
confirmed`, replacing `_dwarf_confirmed_names`/`_snapshot_has_native_
debug_info`): `AbiSnapshot.from_headers`'s own field comment states the
real distinction precisely — "DWARF-derived declarations populate the
SAME functions/types lists [as header-derived ones] but must NOT be
mistaken for header-level evidence." Only a genuinely DWARF/symbols-only
dump (`from_headers` is `False`, where `dwarf_snapshot.py`'s own
DWARF-only extraction path populates `types`/`enums`/`typedefs`/
function-variable signatures directly from DWARF DIEs) may now keep
those fields wholesale; a header-derived snapshot always clears them at
`binary` depth, same as the no-DWARF-at-all case. A real DWARF-visible
struct/enum layout change on a header-derived snapshot is still caught
independently, through the untouched `snap.dwarf.structs`/`.enums`
fields via `diff_platform._diff_dwarf` (which this module never clears,
and degrades gracefully with no header model at all — "If the header
model is absent... fall back to comparing all DWARF types", that
function's own comment) — function/variable signature changes have no
equivalent independent DWARF-native detector, a real, separately-
justified gap (it would need a new per-declaration confirmation fact the
dumper does not currently record), not attempted here. (2) a function/
variable promoted from a header parser's own "declared public, without
contrary evidence" fallback (e.g. `dumper_castxml._variable_visibility`'s
un-emitted customization-point-object case) was promoted to `ELF_ONLY`
the same as a genuinely exported one, manufacturing a false
`*_removed`/`*_removed_elf_only` finding for a declaration no real
binary-only dump would ever have seen as a symbol at all. Fixed with a
new `_exported_symbol_names` (a small, local copy of the same "raw
export table" read this codebase's `cross_source_checks_base.py`/
`snapshot_exports.py`/`post_manifest.py`/`diff_unnamed_types.py` each
already keep their own independent copy of, since a `policy`-layer
caller may import `model`/`compare` but not `extract`, ADR-061, where
most of those live): a surviving function/variable must now also appear
in the snapshot's own raw ELF/PE/Mach-O export table before promotion to
`ELF_ONLY`; skipped (falls back to the prior, looser behavior) when no
platform table was parsed at all, so a synthetic/incomplete snapshot
isn't stripped to nothing. (3) nulling a `BuildSourcePack`'s
`source_abi`/`source_graph` between `build` and `source` left their own
`LayerCoverage` rows in `pack.manifest.coverage` still claiming
`PRESENT`/`PARTIAL` — `evidence_report.optional_coverage()` returns
those rows directly, so a report could still claim source-ABI/
source-graph evidence backed a comparison the depth ceiling actually
excluded it from. Fixed with `_mark_layers_not_collected`, demoting the
L4/L5 coverage rows to `NOT_COLLECTED` in the same place the payload
fields themselves are cleared.

### `--exclude-header` cannot narrow a `--dump-manifest` dump

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** abicheck/extract/header_exclusions.py:215 reject_exclusions_against_a_manifest exists, so the combination is now rejected as the entry states. The entry location in the doc is stale: it names workflows.input_resolution.


`dump`/`compare --exclude-header PATTERN` filters the resolved `-H` header
list. A manifest dump does not use that list: it parses the
`translation_units[]` and `public_header_paths` the manifest document itself
declares (ADR-050 D3). So the two together matched nothing, changed nothing,
and were still recorded on the snapshot as `excluded_header_patterns` and
reported as headers omitted -- a request recorded as achieved when it was
not, which is the failure `--exclude-header` was added to stop, reappearing
one layer down (Codex review on PR #1283).

The combination is now **rejected** (`workflows.input_resolution.
reject_exclusions_against_a_manifest`, a `ValidationError`) rather than
applied, and that is the part that is a gap rather than a fix. Excluding a
header from a manifest-driven dump is a real thing a user may want; what is
wrong is doing it from the command line. A manifest is an exact,
self-describing extraction contract, its roots are matched exactly by
`dumper_scoping.dump_manifest_header_roots`, and narrowing it from outside
would contradict the document the run was told to honour -- while dropping a
translation unit could change what compiles at all.

The right home for the capability is the manifest: an exclusion expressed in
the document, so the contract stays self-describing and the snapshot's
recorded patterns keep meaning "what this extraction actually did". Not
attempted here, since it is a manifest-schema change with its own migration
rather than a review-round fix.

### A pointee qualifier change on a *return* type is not reported

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** func_return_pointee_qualifier_added is registered (model/change_catalog/kind_names_1.py:475, symbols_2.py:79). The only leftover is an unrun conda-forge FP measurement.


**Closed (2026-10, catalog re-audit).** The return mirror of the parameter
split now exists (`compare/parameter_facts.return_pointee_qualifier_changes`,
detector `func_return_pointee_qualifier`): a qualifier gained on a returned
pointee at any level is `func_return_pointee_qualifier_added` (API_BREAK --
`char *p = get_name();` stops compiling), qualifiers only lost are
`func_return_pointee_qualifier_removed` (COMPATIBLE_WITH_RISK -- only a
function-pointer consumer of the old type breaks). Header-tier only, like the
parameter detector. Regression tests: `tests/test_return_pointee_qualifier_direction.py`
(exhaustive one/two-level domain) and
`tests/test_header_backend_parity_probes_integration.py` (real gcc build,
both header backends). Still open: the conda-forge FP measurement the
original entry asked for has not been run; if accessor returns gaining const
prove noisy in practice, the remedy is a policy default, not dropping the
fact.

### `compare --no-baseline` does not yet reproduce `scan`'s audit-mode findings

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry records both failure shapes closed 2026-09-09 and the gate closed 2026-09-10. The blanket assert in workflows/no_baseline_compare.py was replaced (policy/no_baseline_findings.py:26 references the removed `assert not diff.changes`).


Found while migrating the Phase 4 documentation and corpora of
[`plans/one-comparison-product.md`](../plans/one-comparison-product.md)
(ADR-068 D2, §3 row 2). `abicheck compare --no-baseline CANDIDATE` exists,
takes one operand, and correctly records the OLD side with ADR-065's
`declared_absent` acquisition state — but it does **not** report the
intra-version cross-source hygiene findings that are the entire reason the
single-build audit exists. It fails in two different ways depending on the
candidate's shape, both verified live against `main` at `fd6ba681`:

- **A stored `.abi.json` candidate crashes.** All eleven G20 audit fixtures
  (`catalog/cases/case14{3,4,5,6,7,8,9}_*`, `case15{0,1}_*`, `case181_*`,
  plus `case151`'s second `thin.abi.json` variant) abort with an unhandled
  `AssertionError` from `workflows/no_baseline_compare.py`'s
  `assert not diff.changes, "a snapshot compared against itself must never
  produce a change -- if this fires, a detector is reading non-identity
  state"`. `scan <same fixture>` reports its documented verdict and hygiene
  finding on every one of them.
- **A live binary plus `-H` renders an empty report.** Against
  `examples/workflows/audit-release`'s own built `libgreet.so`,
  `scan libgreet.so --header include` reports
  `crosscheck:exported_not_public present … undeclared_export=1` and
  `[warning] exported_not_public: 1`, while
  `compare --no-baseline libgreet.so --header include -o json=...`
  exits 0 with `"changes": []`, no `verdict`, and no cross-source or
  `pattern_preprocessor_scan` block in the document at all.

The root cause is structural, not a stray bug in one detector:
`run_no_baseline_compare` implements the audit as a *self-diff* (`_diff_pair(new,
new, …)`) and then asserts the diff is empty. That assertion was correct
when `compare()` had no per-side stages, but Phase 2a/2b moved all eleven
cross-source checks and the pattern/preprocessor pre-scan *into*
`compare()`, where they legitimately emit `persistent` findings on a
self-compared snapshot. So the audit path either trips its own invariant or,
where the checks stay dormant, yields a document with nothing in it — and
either way `--no-baseline` cannot express what ADR-068 D2 promises it
replaces.

Not fixed here: this documentation/corpora slice owns `docs/`, `examples/`,
`skills-src/evaluation/field/`, `skills-src/evaluation/validation/`, `catalog/`, and `skills-src/` only, and the fix is
in `abicheck/workflows/no_baseline_compare.py` plus whatever
`report/`-side projection has to carry a one-sided finding set. Consequences
recorded rather than papered over: `docs/integration/scenarios/single-build-audit.md`,
`docs/use/evidence-depth.md`, and `docs/start/choose-your-workflow.md` all
still name `scan` as the working spelling for a no-baseline audit, and every
G20 audit case's README keeps its `abicheck scan` reproduce command with an
explicit "blocked on this gap" note rather than being re-driven onto
`compare --no-baseline` or quietly dropped from the catalog.

Tractable when picked up: replace the `assert not diff.changes` invariant
with an explicit partition — identity-diff findings (which genuinely must be
empty, and where the assertion's original intent still holds) versus
per-side `CrossSourceEvolution`/`pattern_preprocessor_scan` output (which
must survive into the rendered document, marked `persistent`, with no
addition/removal/compatibility verdict per D2). ADR-068 D3's
`not_evaluated` rule is the constraint on the second half: with OLD
`declared_absent` there is no baseline evidence, so nothing may ever be
reported as `introduced`. The parity harness (`tests/parity/`) already has
the scan-vs-compare shape needed to gate it, and the eleven fixtures above
are a ready-made acceptance corpus.

**Closed (2026-09-09).** Both failure shapes were reproduced live against
`main` at `ed4e70e3` before anything was changed — the stored-fixture crash
on `catalog/cases/case143_audit_accidental_export/snapshot.abi.json`, and
the empty document from `compare --no-baseline libgreet.so --header include`
against `examples/workflows/audit-release`'s own build — and both are fixed.
The write-up's suggested partition is what landed, plus one root cause it
did not name:

- **The partition.** `abicheck/policy/no_baseline_findings.py` (new) owns
  it: `partition_no_baseline_findings` splits `diff.changes` on
  `Change.cross_source_evolution is not None or
  Change.candidate_side_enrichment` (the two markers `compare()` itself
  sets — deliberately not a `ChangeKind` allowlist, since a kind-keyed
  filter is a second place every new candidate-side kind would need
  teaching, exactly the drift `no_baseline_compare`'s own docstring already
  argues against). `check_no_baseline_partition` keeps the original
  assertion's real intent scoped to the identity half, and adds D3's rule
  (`NO_BASELINE_EVOLUTION_STATES = {persistent, not_evaluated}`) over the
  other. Both are **raised errors, not `assert`s**: the original guard was
  stripped under `python -O`, which would have turned an identity-half
  violation into a silently wrong report rather than a loud failure.
  `workflows/no_baseline_compare.py` returns the candidate-side half on
  `NoBaselineCompareResult.findings`, and `report/no_baseline.py` renders
  it.
- **The second root cause the write-up did not name.** Fixing the partition
  alone still left the live-binary case reporting nothing. The no-baseline
  CLI passed `-H`/`--header` as *parse* input only, never as public-header
  **provenance** — where a two-sided `compare` has always folded each
  side's headers into its public-header sets
  (`service_compare_pipeline._public_header_sets`, via
  `header_utils.split_public_header_inputs`). Without that fold every
  declaration stayed `ScopeOrigin.UNKNOWN`, so the four boundary-dependent
  checks (`exported_not_public`, `public_not_exported`,
  `rtti_for_internal_type`, `public_to_internal_dependency`) evidence-gated
  to `NOT_EVALUATED` and reported nothing — correctly, given what they were
  told. `frontends/cli/commands/compare_no_baseline.py`'s
  `_resolve_public_header_sets` now applies the same split-then-tag rule
  (split before tagging, never after: an unsplit directory entry corrupts
  `scope_fingerprint`).

**Evidence.** `tests/parity/test_no_baseline_audit_corpus_parity.py` is the
acceptance lane this entry asked for, over all eleven fixtures (twelve runs
— `case151` contributes its `thin.abi.json` variant too, the corpus's own
"weaker evidence narrows conclusions" case). Measured result:
`compare --no-baseline` and `scan` agree **exactly** on every fixture —
nothing lost, nothing manufactured:

| Fixture | `scan` `crosscheck.counts_by_check` | `compare --no-baseline` `findings[]` |
|---|---|---|
| case143 | `exported_not_public: 1` | `exported_not_public: 1` |
| case144 | `private_header_leak: 1` | `private_header_leak: 1` |
| case145 | `unversioned_exported_symbol: 1` | `unversioned_exported_symbol: 1` |
| case146 | `rtti_for_internal_type: 2` | `rtti_for_internal_type: 2` |
| case147 | `private_header_leak: 1` | `private_header_leak: 1` |
| case148 | `header_build_context_mismatch: 1` | `header_build_context_mismatch: 1` |
| case149 | `odr_type_variant: 1` | `odr_type_variant: 1` |
| case150 | `exported_not_public: 1`, `public_not_exported: 1` | same |
| case151 (`snapshot`) | `private_header_leak: 1` | `private_header_leak: 1` |
| case151 (`thin`) | `private_header_leak: 1` | `private_header_leak: 1` |
| case181 | `public_to_internal_dependency: 1` | `public_to_internal_dependency: 1` |

The table above is the *measured* result on today's fixtures. What the lane
**asserts** is deliberately weaker and directional, in two halves, because
checking only one is how this gap got in: *no capability loss* (every check
`scan` fires, `compare --no-baseline` fires at least as often, counted per
finding kind) and *nothing manufactured* (every reported finding is
genuinely candidate-side, in a D3-permitted state, with `changes == []` and
`verdict == null` still holding). It is not an identical-report assertion,
and must not be described as one: the contract this PR was gated on is
"the same or a strictly richer finding set", so pinning equality would
fail a future run that legitimately reports *more*.

**One capability `scan` has that the audit report does not** (found while
correcting that overstated claim, recorded rather than quietly reworded):
`scan`'s `crosscheck` block carries a per-check *coverage row* — status
(`present`/`skipped`/`partial`), its detail line, and the `providers` list
that corroborated it. The audit report states each finding and its D3
evolution state, and its `cross_source_evolution` block counts the four
states, but no per-check row: a check that ran and found nothing, and a
check that could not run at all, are both simply absent from `findings[]`
(the Markdown renderer says so in prose; the JSON does not distinguish
them). That is squarely against `vision.md`'s "record before disposing"
and ADR-068 D9, so it is a real gap, not a design choice — it is left open
here rather than folded into this PR because a coverage block is a report
schema addition with its own compute/render pair, not a bug in the
findings path this entry closed. `catalog/cases/case151_xcheck_provider_
matrix/README.md` reads the providers off `run_crosschecks()` directly in
the meantime, and says why.

**A second capability difference, on the exit code** (found the same way, by
regenerating the catalog's expected outputs against the real command instead
of trusting the prose): legacy `scan` derives a *verdict* from an audit's own
findings, so a hygiene finding whose `ChangeKind` is `API_BREAK`-classified
gates CI at exit `2` — verified live, `scan` on `case148`'s and `case149`'s
committed snapshots exits `2`, while `case143`'s `RISK`-classified finding
exits `0`. `compare --no-baseline` exits `0` for all of them: ADR-068 D2 says
an audit reports no compatibility verdict, and `2` is the compatibility
family's own source-break code, so emitting it would be manufacturing
exactly the signal D2 forbids. The CLI was at least loud rather than
silently ignoring the request — `--severity-preset` with `--no-baseline`
used to be a usage error (exit `64`) naming this reason, not a no-op.

**Closed (2026-09-10 ADR-068 amendment).** `policy/audit_gate_exit.py` adds
the orthogonal audit-gate axis this entry called for: its own exit code
(`3` — surveyed against every code `compare`/`scan --against` already use,
the one small integer none of them claims), folded with `max` exactly like
the coverage and evidence-contract axes, so it can raise a clean `0` and can
never lower or be mistaken for a `2`/`4`. It is **opt-in**, not on by
default (every pre-existing `compare --no-baseline` invocation's exit code
is unchanged), activated by passing `--severity-preset` (any value except
`info-only`) — that option is no longer a usage error under
`--no-baseline`; passing it is now the declaration. The axis's own gating
rule reproduces legacy `scan`'s exact partition — `BREAKING_KINDS |
API_BREAK_KINDS` gates, `RISK_KINDS`/`COMPATIBLE_KINDS` does not — read
directly off `checker_policy.py`'s registry-derived sets, deliberately
*not* routed through `severity.py`'s own four-bucket category model (that
model's `potential_breaking` bucket is `API_BREAK_KINDS ∪ RISK_KINDS`,
merged by design, which would have gated `case143`'s `RISK`-classified
finding the same as `case148`/`case149`'s `API_BREAK`-classified ones —
see the ADR amendment for the full reasoning this entry's fix rejected).
Verified against the exact reproduction this entry asked for:
`tests/parity/test_no_baseline_audit_corpus_parity.py`'s
`test_audit_gate_axis_matches_legacy_scan_gating` runs the whole G20 corpus
with `--severity-preset default` and asserts `case148`/`case149` exit `3`
while every other fixture, `case143` included, stays `0` — the same split
`test_scan_baseline_exit_codes_documented_for_the_gated_fixtures` pins for
legacy `scan` itself on the same committed snapshots. The typed Python API
carries no `--no-baseline` support at all yet, so there is no
`CompareResult`-shaped consumer to extend today. See
[ADR-068's 2026-09-10 amendment](../adr/068-one-comparison-product-and-scan-retirement.md#amendment-2026-09-10-the-audit-gate-exit-axis)
for the full account. `scan`'s retirement (§3 of
`docs/contribute/plans/one-comparison-product.md`) is no longer blocked on
this gap specifically; the plan's own tracking is updated to match.

**The composite Action's own translation has since landed too.** At the
time the paragraph above was written, `action/run.sh` did not invoke
`compare --no-baseline` for `mode: scan` at all -- audit-only requests
stayed on the legacy `scan` CLI unconditionally (the 2026-09-09 amendment's
own remaining routing condition), so there was no live call site to
translate onto. That has closed: audit-only `mode: scan` now routes to
`compare --no-baseline` unconditionally too, the same way a baseline scan
already routed to plain `compare AGAINST ARTIFACT`. The Action injects
`--severity-preset default` on the translated invocation whenever the
caller stated no preset of its own (neither the dedicated `severity-preset`
input nor an explicit `extra-args --severity-preset ...`), which is what
keeps `mode: scan`'s own documented default-gating behavior intact across
the migration -- a caller who already asked for a preset (`info-only`
included) keeps exactly the preset it asked for. Exit `3` is published as a
new `AUDIT_GATE` Action verdict output, alongside `COVERAGE_INCOMPLETE`/
`SEVERITY_ERROR`, and fails the step unconditionally (no `fail-on-*` input
governs it, since an audit reports no compatibility verdict to follow).
There is no `MODE == "scan"` condition left in `action/run.sh`'s own
command assembly that exists to serve a legacy-CLI fallback. See
`tests/test_action_run_sh_audit_gate.py` for the end-to-end coverage
(real `run.sh`, real `abicheck`, the same G20 corpus fixtures this entry's
own parity lane already used) and `action.yml`'s `verdict` output
description for the user-facing contract.

**An audit's contract-coverage ledger still names an `old` side.** Now that
`compare --no-baseline --contract public` publishes
`contract_coverage_failures` (it previously exited 1 carrying only the
numeric contribution), the entries are visible -- and half of them read
`"side": "old"` on a run whose OLD side is `declared_absent`. That is an
artifact of the audit being implemented as a self-compare: the evidence
collector sees two sides because it is handed the same snapshot twice.

Deliberately **not** filtered here. The same records produced the exit
contribution that gated the run, so dropping them from the listing would
make the ledger disagree with the number beside it -- exactly the "read,
don't re-derive" failure recorded elsewhere in this file. Doing it properly
means the collector knowing the run is one-sided, so it records one side's
observations rather than two identical ones, and the contribution follows;
that is an ADR-049/ADR-068 question about how a one-sided run collects
evidence, not a display fix. Until then the listing is honest about what
gated, and this entry is what says why an `old` row appears at all.

**The audit's SARIF and JUnit projections do not cross the canonical
`ReportDocument` boundary** (Codex review, P1, partially addressed). The
audit's JSON does: `report.no_baseline.no_baseline_report_document` wraps the
computed mapping in a real `ReportDocument`, so the structured output takes
that type's defensive immutable snapshot the way every two-sided format
does. SARIF and JUnit still read the typed `NoBaselineDocument` directly.
That is deliberate as far as it goes -- `ReportDocument` is an *untyped*
frozen mapping, and rewriting two renderers to index strings instead of
typed fields is a type-safety downgrade, which is why the two-sided HTML and
Markdown renderers also carry typed section structs -- but it does leave the
audit's two structured non-JSON formats outside the boundary the
`report/AGENTS.md` contract states for every format. The shared sections
Codex was concerned about (`run_outcome`, `comparison_scope`) are already
built by the same section builders the two-sided path uses, so those
specifically cannot drift; a *new* shared section added to
`ReportDocument` would still have to be added to the audit by hand.

Per `AGENTS.md`'s bug-class rule the D3 invariant is **also** stated as a
property test over generated inputs rather than eleven fixed cases:
`tests/test_no_baseline_d3_properties.py` generates candidate snapshots
across the whole evidence ladder (export table present or absent,
declaration origins drawn independently of the export list, versioning
scheme on or off, private types present or not) and asserts that no
candidate ever yields `introduced`/`resolved`, that the comparison half is
always empty, that the reported set is *exactly* the candidate-side one
(the "no longer crashes but silently drops findings" failure a
does-not-raise test would miss), and that the audit is deterministic. Its
oracle is the forbidden-state set written out literally, not derived from
the implementation's own permitted set, so widening one cannot silently
widen the other. The partition helpers additionally get primitive-level
contract tests (totality, disjointness, order preservation) decoupled from
any snapshot, per `AGENTS.md`'s "Primitive-level property tests" guidance.
Both suites were mutation-checked: silently dropping candidate-side findings
fails 14 tests, and permitting `introduced` fails 2.

Registered as bug class `invariant.blanket_assertion_over_widened_population`
in `tests/regressions/manifest.py` — the general shape being "a whole-set
emptiness assertion outlives the single population it was ever true of".

### `compare --no-baseline` accepts `--contract` but never wires it through

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** abicheck/frontends/cli/commands/compare_no_baseline.py:201 passes contract_mode=kwargs.get("contract_mode") through, and the entry records it as closed 2026-09-09.


Found in the same Phase 4 documentation slice, verified by reading
`frontends/cli/commands/compare_no_baseline.py` against `main` at the same
commit above (fd6ba681). `compare --no-baseline` accepts `--contract
public|exports|all|auto` — Click parses it, `--help` documents it — but
`_run_no_baseline_compare_cmd` never reads `kwargs.get("contract")` (or a
resolved `contract_mode`) and never passes anything contract-related to
`run_no_baseline_compare`. The contract-coverage ledger this axis's exit
contribution folds from is therefore never populated on this path: a
`compare --no-baseline NEW --contract public` run against a headerless
candidate exits `0`, where the equivalent two-sided `compare OLD NEW
--contract public` exits `1` for missing public-header coverage. The flag
is accepted but silently inert, not merely undocumented — a CI job relying
on it as a gate gets no warning that it never ran.

Not fixed here, same file-ownership boundary as the gap above. The fix is
threading the resolved `contract_mode`/`contract_evaluation` config through
`_run_no_baseline_compare_cmd` into `run_no_baseline_compare`, the same way
the two-sided `compare` path already does via
`compatibility_evaluation_frontend`/`contract_pipeline`. Recorded in
[`docs/reference/exit-codes.md`](../../reference/exit-codes.md#compare-no-baseline-single-artifact)
rather than left as a silent behavioral gap in the doc that would otherwise
claim the axis "applies exactly as it would for a two-sided run."

**Closed (2026-09-09).** Reproduced live first: on `main` at `ed4e70e3`,
`compare --no-baseline libgreet.so --contract public` against a headerless
candidate exited `0` where the two-sided `compare libgreet_old.so
libgreet.so --contract public` exited `1`. `_run_no_baseline_compare_cmd`
now resolves the flag through the *same two* `cli_options` helpers
`cli_compare_helpers.run_compare` uses — `resolve_contract_evaluation` (any
value activates the ADR-049 evaluator) and `resolve_contract_domain`
(`auto` maps back to `None` so D7's lower tiers decide, and its parameter
source is demoted from `COMMANDLINE`) — and passes both into
`run_no_baseline_compare`, whose `contract_evaluation`/`contract_mode`
parameters already existed and were simply never fed. Sharing the resolvers
rather than re-deriving is what makes an equivalent one-sided and two-sided
invocation activate identically.

Nothing downstream needed changing: `report/no_baseline.py`'s
`no_baseline_exit_code` already folded `coverage_exit_floor(result.diff)`,
which reads the ledger off the run's own persisted `contract_context` — it
had simply never been populated. Verified live after the fix:
`--contract public` on the same headerless candidate now exits `1`, and a
run without `--contract` still exits `0` (no domain to be short of evidence
for, so every pre-existing invocation is unchanged). The corpus lane's
`test_no_baseline_exit_code_is_clean_without_a_contract` pins that second
half across all eleven fixtures, so the exit-1 case is a real signal rather
than noise.

### `compare --no-baseline` parsed `--sources`/`--build-info`/`--depth`/`--dry-run` but read none of them

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** compare_no_baseline.py:169-184 now handles inv.output.dry_run through build_no_baseline_dry_run_result, and the entry is marked Closed 2026-09-09.


**Closed (2026-09-09), in the same pass that closed the two entries above.**
Recorded here rather than left unwritten because it is the same failure mode
as the `--contract` entry above — a flag Click parses and `--help` documents,
which the command body never reads — and it was found by auditing
`_run_no_baseline_compare_cmd` against `compare`'s full option set once
`--contract` turned out to be inert, not by a user report. Verified live on
`main` at `ed4e70e3` before the fix: `compare --no-baseline libgreet.so
--depth build` exited `0` where the two-sided `compare libgreet_old.so
libgreet.so --depth build` exited `7`; `--dry-run` ran a full audit and
printed a report; `--sources` collected nothing.

Four separate wirings, each reusing the primitive the two-sided path already
uses rather than growing a parallel one:

- **`--sources`/`--build-info`.** `resolve_no_baseline_candidate` called
  `workflows.input_resolution.resolve_input` directly, which resolves L0–L2
  only, so inline L3–L5 evidence was never embedded. It now routes through
  `workflows.artifact.execute.resolve_side_snapshot` — the *same* per-side
  primitive `dump` and each side of a two-sided `compare` resolve through —
  with an `InputSpec` carrying the candidate's `sources`/`build_info` and a
  `SideEvidence` whose collect mode comes from `collect_mode_for(depth,
  side)` (already variadic over a single input precisely because `dump` asks
  it over one operand too).
- **`--depth`.** Feeds that collect mode, and is separately held to the same
  evidence-contract floor. `policy/depth_evidence_contract.py` gained
  `record_no_baseline_depth_evidence_contract_error`, a named one-sided
  entry point that delegates to the existing two-sided
  `record_depth_evidence_contract_error` with the OLD side excluded — spelled
  as its own function rather than leaving each call site to write
  `old=candidate, new=candidate, old_is_live=False`, which reads as a bug at
  every call site. The stored-snapshot carve-out (`is_live`) carries over
  unchanged: a `.abi.json` candidate this run never extracted cannot have
  fallen short of a depth. Verified live after the fix, one-sided and
  two-sided now agree exactly: **exit 7** for `--depth build` with no
  build evidence, and **exit 0** for the same command once a real
  `compile_commands.json` is present.
- **`--dry-run`.** `frontends/cli/compare_dry_run.py` gained
  `build_no_baseline_dry_run_result`, a sibling of the two-sided builder
  rather than a call into it: that builder's whole shape is two operands, and
  rendering a single-build audit through it would print the candidate twice
  under `old:`/`new:` labels — exactly the "a baseline was consulted"
  misreading ADR-068 D2 forbids. It reuses the shared section titles
  (`dry_run.SECTION_ORDER`) so both reports still read the same way, and
  *blocks* (exit 1) on a pinned `--depth build`/`--depth source` with no
  `--sources`/`--build-info`, since the real run exits 7 on that same
  condition and a dry run that called it fine would be lying.
- **Two more silently-inert flags on the same path, found by the same
  audit.** `--write FORMAT=PATH` was accepted and dropped — a job asking for
  a JSON artifact alongside a human-readable report got neither the file nor
  a warning. It now renders from the *same* analysis rather than re-running
  it (ADR-068 D4's "a complete machine-readable result must always be
  obtainable without a second run"), reusing the shared
  `reject_incoherent_secondary_writes` guard rather than a second copy of its
  two coherence rules, and rejects an unsupported secondary format naming
  `--write` rather than `--format`. `--include-system-declarations` was
  dropped too: this path never passed `include_dependencies` at all, so it
  silently got `resolve_input`'s unfiltered default (`True`) where every
  two-sided `compare` and every `dump` passes the CLI's own filtered default
  (`False`). It now reads the flag, so a `--no-baseline` snapshot is scoped
  the same way a `dump` baseline is — verified live that the finding set is
  unchanged either way on the `audit-release` example.

- **An `old=`-scoped input is now a usage error, not a silent drop.** A
  `--no-baseline` run has no OLD side, so `--sources old=…`/`--header old=…`
  and siblings are rejected with a real message — the same guard
  `_reject_view_tokens_for_no_baseline` already applies to `--view`. The
  distinction matters and is easy to get backwards: a *bare* `--sources
  tree/` populates **both** per-side dests
  (`cli_options._split_sided_single`), so "the old dest is set" is not "the
  user scoped this to OLD" — the guard fires only when the old dest holds
  something the new dest did not also get. Rejecting the bare spelling would
  break the single most useful invocation on this path.

**`--format` ruling, dated rather than deferred.** The Phase 2e slice
supported only `json`/`markdown`, with the other five a usage error "until a
later phase". That is now decided per format rather than left open:

- **`sarif`, `junit`, `oneline` — implemented.** SARIF and JUnit are
  *findings* formats with no verdict slot to leave empty (a SARIF run is a
  list of results with rule ids and levels; a JUnit suite is a list of test
  cases), which is exactly what a single-build audit produces, and both are
  how a CI job consumes one. Rendered in `report/no_baseline.py` from the new
  frozen `NoBaselineDocument` rather than through `sarif.to_sarif`/
  `junit_report.to_junit_xml`, whose `exitCode`/`exitCodeDescription` and
  suite partitioning are derived from `DiffResult.verdict` — which on a
  self-compare reads `NO_CHANGE`, a compatibility claim D2 forbids this run
  from making. The per-finding *metadata* is reused, not reinvented:
  `sarif._rule_for`/`_parse_source_location` are imported, so a rule id and
  help URI mean the same thing a code-scanning consumer already expects.
- **`html`, `review` — remain a usage error, by ruling.** Both are narrative
  renderings of a *comparison*, not projections of a finding list: HTML's
  information architecture is a verdict badge, an OLD → NEW version headline
  and addition/removal/modification tables; `review` is literally "what
  changed between these two releases, and should you ship it" (verdict,
  counts by direction, release recommendation, manual-review banner). With no
  baseline there is no change to review and no release to recommend, so a
  one-sided rendering of either is a *new page/digest design*, not a
  projection — genuinely different work from the three above, none of which
  needed a layout decision. The CLI's error now names this ruling and points
  at `oneline` as the closest honest equivalent to a review digest, instead
  of promising an unspecified later phase. The reasoning lives with the code,
  in `report.no_baseline.NO_BASELINE_UNSUPPORTED_FORMATS`.

**Four review findings on the first push, all real, all fixed.** Recorded
because two of them are the *same* defect class this entry is about — a
derived answer disagreeing with the answer it derives from — and one
reproduces a bug this repository had already fixed once elsewhere:

- **A suppressed finding vanished (P1).** `checker.compare()` moves a
  matched finding out of `diff.changes` into `diff.suppressed_changes`; the
  partition read only the former, so a suppressed audit was
  indistinguishable from a clean one — no way to tell that policy hid a
  finding, or which rule did. That is precisely what `vision.md`'s "Record
  before disposing" forbids ("100 removals detected, 100 suppressed by rule
  X" stays visible on a passing run). The suppressed half now gets the
  *identical* partition and D3 check (a suppressed comparison finding would
  be just as impossible), rides on `NoBaselineCompareResult.
  suppressed_findings`, and is projected by every format with its own
  `suppression_rule`: a `suppressed_findings[]`/`suppressed_count` pair in
  JSON, a table in Markdown, a count in `oneline`, SARIF's own native
  `suppressions` array, and a `<skipped>` case in JUnit.
- **JUnit failed a build the CLI passed (P1).** Every finding got a
  `<failure>` while the same document reported exit `0` — audit hygiene
  findings are advisory (ADR-028 D3 / ADR-035 D1) and ADR-068 D2 gives the
  run no compatibility contribution at all. This is the identical bug
  `junit_report._is_failure`'s docstring records having already been fixed
  once for the two-sided report ("reporting one `<failure>` beside a
  `NO_CHANGE` verdict and a clean exit was the bug"). Failure is now
  derived from the run's own gate: each finding gets a **passing**
  `<testcase>` (D9 — the fact stays visible, it just is not a failure) and
  one `exit code` case fails when, and only when, an orthogonal axis
  actually gated the run.
- **`--write` bypassed the shared safe writer (P2).** A raw
  `Path.write_text()` raised `FileNotFoundError` after the analysis and the
  primary render had already completed, when the target's parent directory
  did not exist. Now routed through `_safe_write_output`, the same writer
  `-o/--output` uses, which creates parents and translates a failure into a
  clean Click error.
- **The dry run disagreed with the run it previews (P2).** A pinned
  `--depth build` on a *stored* candidate was reported as a blocker (exit
  1) while the real run exempts that operand from the depth floor
  (`candidate_is_live=False` — no extraction happened, so nothing can have
  fallen short) and exits `0`. The preview now takes the same carve-out
  from the same helper, so the two cannot diverge.

Generalized rather than patched per instance:
`tests/test_no_baseline_report_formats.py` states the invariants over every
format and every fixture — conservation (suppression only ever *moves* a
finding between the two lists, checked over generated rules that match all,
some and none), JUnit's failure count equalling the exit code exactly,
every format disclosing a suppression, every format agreeing on the exit
code, and JSON and SARIF agreeing on the finding set. Mutation-checked
against all four regressions, including the over-correction (making JUnit
pass by *dropping* findings satisfies the failure-count invariant while
losing the audit's whole content — a separate test catches that).

**A second review round, four more findings, all real (2026-09-09).** Two
of them are again the same class this entry is about, and one is that class
*inside the mechanism built to prevent it*:

- **The exhaustiveness test had a hole exactly where it hand-waved (P1).**
  `_CONSUMED_UPSTREAM` allowlisted the *pre-normalization* option names
  (`dump_manifest`, `version`, `debug_root`, `probe_matrix`), on the
  reasoning that `normalize_sided_options` consumes them before dispatch.
  It does -- but the `new_*`/`old_*` destinations it *generates* were never
  read, so the test passed **because the option had been renamed**. A bare
  `--dump-manifest` was accepted and dropped: even an invalid manifest
  exited `0` while the audit analysed a different surface than requested,
  and `--version new=` was equally inert. The test now expands each raw name
  into the destinations it generates and requires each one separately to be
  read or declared -- which immediately surfaced **16** silently-dropped
  destinations, not the four the review named. `--version`, `--debug-root`
  and `--include`'s per-path labels are now wired; `--dump-manifest`,
  `--probe-matrix`, `--debug-info` and `--devel-pkg` are rejected (their
  guards re-keyed onto the generated names, since a guard keyed on the raw
  name could never fire); `old_version` is recorded as inert *by
  construction* with its reason, since Click always populates it with the
  placeholder `"old"` and an explicit `--version old=` is therefore
  indistinguishable from the default by dispatch time.
- **`--depth binary` still ran the L2 header frontend (P1).** The audit
  hand-built its `SideEvidence` instead of going through
  `service_compare_evidence.resolve_side_evidence`, so it skipped that
  function's binary-depth clearing rules (headers *and* any dump manifest
  are dropped). Verified live: one-sided reported `evidence_tiers: [elf,
  dwarf, dwarf_advanced, header]` and an `exported_not_public` finding where
  two-sided reported no `header` tier at all — a **manufactured finding** at
  a depth documented as symbols-only. Fixed by reuse, not a second copy.
- **A failed evidence contract read as operationally successful (P1).**
  `no_baseline_exit_code` correctly returned `7`, while `run_outcome.
  operational` still serialized `none`. `compatibility`/`gate` genuinely
  never apply to an audit, but *operational* is a different axis, and
  `OperationalStatus.EVIDENCE_CONTRACT_ERROR` is its canonical state. Now
  read off the same flag the exit code folds, so the two cannot disagree.
- **A linker-script operand was misclassified as stored (P1).** The depth
  floor's live/stored carve-out asked `detect_binary_format` about the
  operand *as written*. A GNU ld `INPUT(...)`/`GROUP(...)` script is text, so
  it answered "stored" and exempted the run -- while `resolve_input` happily
  followed the script and performed a real extraction. Verified live: the
  same library named directly exited `7` under `--depth build`, and named
  through its script exited `0`. Now chain-resolved with
  `binary_utils.resolve_linker_script_chain` first, which is what the
  resolver itself does with the same operand, so the two agree by
  construction rather than by coincidence.
- **Extraction failure escaped as a traceback (P2).** A corrupt candidate
  printed a full stack trace where the two-sided path printed
  `Error: Failed to dump '...'` for the identical input. Now translated at
  the CLI boundary with the same `ValidationError` -> exit 64 /
  `SnapshotError` -> exit 1 mapping `cli_resolve`'s own `run_dump` wrapper
  uses.

One self-inflicted defect worth recording, since it is the same discipline
failure the entries above are about: splitting the SARIF/JUnit renderers out
to keep `report/no_baseline.py` under the 800-line cap created a real import
cycle (`no_baseline -> no_baseline_render -> no_baseline`, via the
renderer's `NoBaselineDocument` annotation -- the AI-readiness scan counts
``TYPE_CHECKING`` imports too). It reached CI because after that last change
`check_architecture` was re-run and `check_ai_readiness` was not; only one of
the two catches this. Fixed by giving the shared contract its own leaf,
`report/no_baseline_document.py`, which both halves depend on and neither
depends back through -- this package's own `document.py`-versus-`render_*`
pattern, and the remedy `AGENTS.md` prescribes ("move the shared logic to a
leaf module", never extend `IMPORT_CYCLE_ALLOWLIST`).

Each has a regression test in `tests/test_compare_no_baseline_cli.py`, and
the exhaustiveness rule itself gained
`test_every_generated_destination_is_wired_or_declared` -- the half the
original test missed. `report/no_baseline.py` crossed the 800-line new-file
cap in the process; its SARIF/JUnit renderers moved to
`report/no_baseline_render.py`, matching this package's own
`render_json.py`/`render_xml.py` split, rather than being trimmed to fit.

**Coverage and complexity follow-up (same pass).** Codecov's patch gate
and CodeFactor both flagged the first two commits, and both were acting on
something real rather than on noise:

- **The new CLI surface was verified by hand and pinned by nothing.** Every
  usage error, the `--write` path, and the `--dry-run` preview had been
  confirmed at a terminal while being written, and no test named any of
  them -- the same "confirmed once by the person who just wrote it" gap this
  entry's own history keeps producing. `tests/test_compare_no_baseline_cli.py`
  drives all of it through the real Click entry point (which formats render
  and how, which invocations are refused and what the message says, what
  reaches disk, what the process exits with), and
  `tests/test_no_baseline_report_formats.py` gained the *gating* half that
  the P1-2 fix's own correctness depends on: a run whose orthogonal axis
  fires must still fail exactly one JUnit case, and its advisory findings
  must still pass. Coverage of the five new/changed modules went from 90% to
  94%, with the CLI module 80% -> 95% and the dry-run builder 0% -> 100%.
- **The command body was one 159-line function** (cyclomatic complexity
  ~30) doing validation, resolution, execution and reporting in sequence,
  which is what CodeFactor's "complex method" pattern is for -- and it
  passed on the first commit, failing only once the review fixes grew it.
  Split into the four phases it always had: `_validate_no_baseline_
  invocation` (every refusal, before any work), `_resolve_no_baseline_
  invocation` (into a frozen `_ResolvedInvocation`, so a later phase cannot
  reach back for a raw parameter the validation phase already ruled on),
  the run itself, and `_emit_no_baseline_report`. Now 78 lines at complexity
  4. Behaviour-preserving: the CLI tests above were written *before* the
  split, precisely so it was protected.

**The class, not just the four instances.** "Accepted but never read" is
the single defect this path has now produced four separate times
(`--contract`; `--sources`/`--build-info`/`--depth`/`--dry-run`; `--write`;
`--include-system-declarations`), and every one was found by *reading the
code*, never by a failing test — a dropped option produces no output to fail
on. Fixing them one at a time leaves the class open, since the next option
added to `compare` inherits the same silence. So the rule is inverted:
`_UNSUPPORTED_OPTIONS` in `frontends/cli/commands/compare_no_baseline.py`
names every `compare` option this path does *not* implement, passing one is
a **usage error** rather than a no-op, and
`tests/test_compare_no_baseline_options.py` fails if any `compare` parameter
is neither read by that module nor declared in the table. A new flag is
therefore either wired or declared, never silent.

The table's reasons fall in three families, so the error message tells the
user which applies: the option *describes a comparison* this run never
performs (`--used-by`, `--used-by-manifest`, `--required-symbol`,
`--use-cases`, `--post-manifest`, `--diagnostic-comparison`,
`--old-variant`/`--new-variant`, `--bundle-facts-*`, `--since`/
`--changed-path`); it is for the *directory/package fan-out*
`--no-baseline` does not accept (`--select`, `--select-required`,
`--output-dir`); or it is genuinely applicable and *not wired yet*
(`--abi3` — candidate-side by definition and the obvious next one to close
— `--budget`, `--severity-preset`, `--pack`, `--config`,
`--instantiation-manifest`, `--follow-deps` and its search-path siblings,
`--debug-info`, `--devel-pkg`). That last family is recorded here rather
than left as an accepted no-op precisely so it is a visible decision.

### The Action's `mode: scan` still routes several request shapes to the legacy `scan` CLI

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** _SCAN_NEEDS_LEGACY_CLI no longer occurs in action/run.sh (grep count 0), and run.sh:462/2434 record that mode: scan was removed. The entry header says Closed 2026-10-01.


> **Closed (2026-10-01 triage).** `mode: scan` is now a hard error with a
> migration message in `action/run.sh`; nothing routes to the deleted
> `scan` CLI any more. The per-condition record below is history.

Found while closing ADR-068's baseline cross-source authority divergence
(see the ADR's 2026-09-09 amendment,
[`plans/one-comparison-product.md`](../plans/one-comparison-product.md) Phase
4). **Update (2026-09-09, Phase 4 commit 2):** the maintainer re-scoped this
work — the Action does not keep a compatible interface with every `scan`
capability, so each remaining condition below is now ruled (a) already
covered, (b) dropped as a documented breaking change, or (c) genuinely
required and implemented — see ADR-068's second 2026-09-09 amendment for the
full per-condition table. Two rows closed for real in this commit:

- **`--budget` — closed.** `compare` gained a `--budget` option
  (`frontends/cli/commands/compare.py`), enforced via
  `deadline.deadline_scope` around both the resolve and classify phases
  (remaining-time-aware, not a fresh full budget on each entry), setting
  `DiffResult.budget_overflow` (exit 5) on overflow. Verified live. Typed API:
  `CompareRequest.budget_s`. **Known, accepted narrowing**: bounds two
  coarser phases (resolve, classify) rather than `scan_engine.py`'s own
  finer per-stage checks — still a real, enforced ceiling, not a capability
  gap.
- **An explicit `--depth build`/`--depth source` with unreached evidence —
  closed.** This was a real, undocumented `compare` gap distinct from
  everything else in this entry: `compare --depth build old.so new.so` with
  no `--sources`/`--build-info` silently degraded to symbols-only evidence
  and reported `NO_CHANGE`/exit 0 (verified live) — `workflows.artifact.
  execute.enforce_requested_depth` already implemented this exact floor as a
  hard `ValidationError`/exit 64, but only for the typed-API resolution path
  (`resolve_compare_request`); the native CLI's own resolution
  (`cli_resolve._resolve_compare_snapshots`) never called it. New
  `policy/depth_evidence_contract.py` recomputes the same floor and records
  it as ADR-064's exit-7 axis instead of raising (mirroring `workflows.
  abi3_audit.record_abi3_evidence_contract_error`'s existing pattern),
  wired into both the native CLI and the typed pipeline. Verified live:
  `compare --depth build` on an evidence-free pair now exits `7`.

Ruled (b), dropped, documented breaking changes to `mode: scan` (see the
ADR amendment for the full reasoning each):

- `--risk-rules` and risk-driven `auto` depth selection (no explicit
  `--depth`) — omitting `--depth` on `compare` already deterministically
  defaults to `headers`, so dropping the risk-scoring auto-escalation
  removes a sometimes-deeper convenience, not a floor; a CI job wanting
  guaranteed source-level assurance must now pin `--depth source` itself.
- `--crosscheck KEY=error` promotion syntax — already superseded: all
  eleven cross-source checks reach `compare` as ordinary `ChangeKind`s, so
  `--policy`/`.abicheck.yml`'s `policy.overrides` already lets a user
  control any one check's severity; only the `KEY=LEVEL` *syntax* itself
  does not survive.
- `--build-target` — **stale, corrected below.** This entry originally said
  no config equivalent existed for either command; that was already false
  by the time it was written (`.abicheck.yml`'s `build.targets` key existed
  and `dump --build-target` already consumed it, CLI-override-wins). `scan`
  *did* have its own `--build-target` flag
  (`changelog.d/20260816_113000_noreply_abicheck_scan_build_target.md`
  records it being added), and it was retired first, in ADR-068's
  2026-09-09 amendment (the `scan` retirement) — `dump`'s own
  `--build-target` was deliberately left in place at that point precisely
  because `scan` still existed and shared its plumbing, so pulling `dump`'s
  copy first would have broken `scan`'s still-live flag. `dump`'s own
  `--build-target` CLI flag was later removed outright too (ADR-068 Phase 6
  follow-up, once `scan`'s full removal cleared the routing hazard that had
  deferred `dump`'s own removal) — `.abicheck.yml`'s `build.targets` is now
  the *only* front-end-reachable source for either command, with no CLI
  override left to take precedence over it.
- `--artifact-set`/`new-library-set` — **resolved on the CLI (2026-10-03,
  one-comparison-product acceptance F-23).** This entry recorded that ADR-065
  S3's package component inventories (plan Prerequisite P5) were not started,
  so routing the mode onto `compare --no-baseline DIR` would have narrowed
  its member-selection/coverage guarantees. P5 landed, and `compare
  --no-baseline DIR` now exists: members come from the one-sided release
  resolver (`workflows.release_inputs.resolve_release_side`, which the
  two-sided fan-out now also calls), the acquisition record is ADR-065's
  (`workflows/no_baseline_set.py` -- `--select`/`--select-required`,
  `scope.on_incomplete`, a directory `unproven`, a fully extracted archive or
  an asserting stored package `proven`), each member is audited by the same
  sequence a scalar audit runs, and the result is one `audit_set` document
  (`report/no_baseline_set.py`, `audit_set_report.schema.json`). Still open:
  the Action's `new-library-set` input keeps its usage error -- wiring it to
  this command is an Action-side slice, not done here.

Ruled (a), already covered by `compare` today, no Action-level gap left once
routed unconditionally: the additive-vs-overriding `--header`/`--include`
combination (fixed by unioning the shared root into each side in `run.sh`
itself, no `compare` change needed); a `.json`/`.gz`/`.zst`/content-sniffed
snapshot baseline's `dependency_scope` tag matching (`service.run_dump`'s
`include_dependencies` parameter already provides it); `--output-file` and a
bare `-o`/`--write` via `extra-args` (`compare` already has `-o` and a
repeatable `--write`); the compile-context-option family reaching through
`extra-args` (`--lang`/`--ast-frontend`/`--compiler*`/`--sysroot`/etc. were
already removed from `compare`/`dump`'s own CLI before this gap was first
written, in favor of `.abicheck.yml`'s `compile.*` — `scan` just hadn't
migrated yet, so this was never a `compare`-side gap); the default (or
`--no-pattern-verdicts`) pattern-verdict divergence (moot once routed:
`compare`'s modulation has been unconditional, with no off switch, since
D4). **A genuinely new (a) finding this commit's investigation also
surfaced, not previously documented:** the non-JSON/`json` `--format`
restriction and the scan-vs-compare *JSON schema shape* itself were never a
`compare` flag gap — `compare` already accepts every format `scan` did.
Once `mode: scan` routes to `compare` unconditionally, its JSON output
switches from `scan_schema_version`/nested `diff.findings` to
`report_schema_version`/root `changes` — **a documented breaking change to
the JSON shape**, not a remaining capability gap: any workflow step parsing
that file by its old shape must update to the canonical `ReportDocument`
shape.

**A separate, newly-confirmed gap this investigation surfaced, not yet
closed:** `compare --no-baseline` (the audit-only replacement for `scan`
without `--against`, ADR-068 D2) genuinely does not read `--sources`/
`--build-info`/`--depth` from its own CLI kwargs today
(`frontends/cli/commands/compare_no_baseline.py`'s `_run_no_baseline_compare_cmd`
only reads `headers`/`includes`/`lang`/`public_headers*`), does not honor
`--dry-run` (never checked), and supports only `json`/`markdown` formats
(`_SUPPORTED_FORMATS`) — confirming, not merely repeating, the narrower
audit-only capability set this entry's Action-routing comment already
described. This means the Action's *audit-only* `mode: scan` (no baseline)
cannot yet route to `compare --no-baseline` without narrowing what it
collects, and stays out of scope for this commit's file ownership
(`frontends/cli/commands/compare_no_baseline.py` is not among the files this
phase owns). Tractable when picked up: thread `sources`/`build_info`/
`depth`/`dry_run` from `_run_no_baseline_compare_cmd`'s own `kwargs` into
the audit pipeline the way the two-sided path already does, and extend
`_SUPPORTED_FORMATS`.

**Closed (2026-09-10 ADR-068 amendment).** The audit-only gap this
paragraph deferred to closed (see the 2026-09-10 amendment recorded on this
entry's own `compare --no-baseline` sibling above), and the mechanical
follow-up landed with it: `action/run.sh`'s routing predicate
(`_SCAN_AUDIT_ONLY_NEEDS_LEGACY_CLI`, the immediate successor to
`_SCAN_NEEDS_LEGACY_CLI` below) and every `MODE == "scan"` branch that
existed only to serve its legacy-CLI fallback are deleted -- there is no
`CMD+=(scan)` site left anywhere in `run.sh`. Every `mode: scan` request
(audit-only or baseline) now assembles `compare`/`compare --no-baseline`
through one shared branch, with `--severity-preset default` injected on the
audit-only shape whenever the caller stated no preset of its own (see the
sibling entry's own "composite Action's own translation has since landed
too" note for the full account, and `tests/test_action_run_sh_audit_gate.py`
for the end-to-end coverage against real fixtures).

Not fixed here (deferred to a follow-up in this same phase, once the audit-
only gap above closes -- historical text, kept for the record; see the
"Closed" note just above): `action/run.sh`'s `_SCAN_NEEDS_LEGACY_CLI` predicate
itself, and the ~17 `MODE == "scan"` branches it guards, are not yet deleted
— doing so safely requires the audit-only routing above to actually work
(today's Action still routes every audit-only `mode: scan` request to the
legacy CLI unconditionally, independent of this predicate), and a
line-for-line removal of a script this size without an Action e2e run to
verify against was judged higher-risk than shipping the two closed (c) items
and this ruling on their own. The routing predicate's *decision table* is
now complete (every condition ruled); what remains is mechanical: replace
the predicate's body with the two (b) hard-error checks plus the header/
include union fix, delete the legacy scan-CLI assembly branch and the now-
dead `_CLI_MODE == "scan"` downstream branches, once `compare --no-baseline`
closes the audit-only gap above.

### ~~`scan`'s JSON `diff.findings[]` entries never carry `gate_contribution`~~ — CLOSED (no longer applies)

**Status (re-verified 2026-10-09 at `456989f`): MOOT.** The scan command, cli_scan_baseline.py and the diff.findings[] envelope were deleted (ADR-068 Phase 6). The entry is already struck through as CLOSED, no longer applies.


**Closed (2026-09-11, ADR-068 Phase 6).** `scan` — and with it
`cli_scan_baseline.py`, `_baseline_finding_dicts`, and every `diff.
findings[]` JSON shape this gap described — was deleted outright when the
`scan` command was retired. There is no longer a second JSON envelope for
this field to be missing from: `compare`'s own `changes[]` entries already
stamp `gate_contribution` unconditionally (the half of this gap that was
never broken). Kept here, struck through, as the historical record; the
`tests/parity/runner.py` harness this entry describes (`RunOutcome.
gate_contributions`, the `None`-skip in what was `assert_full_parity()`)
was itself deleted in the same PR along with every other scan-vs-compare
comparison it existed to support — see `tests/parity/runner.py`'s own
module docstring for what `tests/parity/` is now.

Found in CodeRabbit review round 12 on PR #1172, while building
`tests/parity/runner.py`'s scan-vs-compare parity harness (ADR-068 plan
Phase 4). `compare`'s JSON `changes[]` always stamps a per-finding
`gate_contribution` field (ADR-049 D1, unconditionally — 0 included —
via `reporter.py`'s `_change_to_dict` calling
`severity.gate_contribution_for_change`). `cli_scan_baseline.py`'s
`_baseline_finding_dicts`, the equivalent builder for `scan --against`'s
`diff.findings[]`, never calls it and never emits the key at all — not "always
0", genuinely absent from the dict.

Not fixed here: `gate_contribution_for_change` needs the same
`severity_config`/`policy`/`kind_sets`/`policy_file` context `compare`'s
renderer already threads through, and `_baseline_finding_dicts` has five call
sites in `cli_scan_baseline.py` that would each need that context passed
in — CodeRabbit's own review flagged this as a "Heavy lift" beyond the scope
of a review-response round, and this PR's file ownership is scoped to
`checker.py`/`policy/**`/`cli_scan_baseline.py`/`service_scan.py`/
`action/run.sh`/`tests/parity/**`, not a redesign of that builder's call
graph. What *is* fixed here, within `tests/parity/**` ownership: the harness
itself no longer silently defaults a missing scan-side `gate_contribution` to
`0` before comparing it against compare's real (possibly nonzero) value, which
would have let a genuine divergence on this field read as false parity.
`RunOutcome.gate_contributions` is typed `dict[tuple[str, str], int | None]` —
`None` means "the raw finding dict never had the key" (scan's current
across-the-board state), a real `int` means the field was present and its
value read (compare's state, and scan's future state once this gap closes);
`assert_full_parity()` skips the comparison for a key where either side reads
`None` rather than coercing both to `0` first.

Tractable when picked up: thread the same evaluation context (severity
config, policy, kind sets, resolved policy file) `cli_scan_baseline.py`
already has in scope for the compare-parity verdict recompute into each of
`_baseline_finding_dicts`'s five call sites, and call
`severity.gate_contribution_for_change` per finding the way `reporter.py`
does. Once real values are emitted, the parity harness's own `None`-skip
becomes dead code (no test result changes, since a value that now compares
equal was previously skipped, not marked passing under a false default) — at
that point drop the skip and go back to an unconditional comparison so a
*future* regression on this field is caught structurally rather than by an
absent key silently reading as "no divergence".

### ~~`compare --format` repeated silently keeps only the last format~~ — CLOSED by the export grammar

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** Already struck through as closed: `--format` is gone and `-o FORMAT=DEST` is repeatable. The remaining text is a perf-harness note.


Recorded while building the full-CLI harness against `main` at `f6aa2aae`, where
`compare --format json --format markdown -o out` exited 0 and wrote markdown
only, with no way to get both artifacts from one invocation. ADR-068's slices
7m/7n landed before this branch merged and closed it outright: `--format` is gone
and `-o FORMAT=DESTINATION` is repeatable, with every export rendered from the
one completed analysis.

Kept as a closed entry rather than deleted because the *measurement* survives and
is worth knowing: `check_l2_cli_perf.py`'s `compare_two_formats` scenario now
verifies that promise rather than trusting it. A single `compare` exporting both
`json=` and `markdown=` over a stored-old/live-new pair performs **2 header
extractions — exactly one side's worth**, so the second renderer demonstrably
does not re-run the analysis. (Stored/stored would have been the easier operand
pair and a useless test: the count is zero either way, so it could not
distinguish one analysis from two.)

### Retired CLI spellings still named in runtime messages

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The repo-wide sweep is closed, and the residue is gone too: tests/test_diagnostics_name_live_flags.py:81 has `DEAD_CODE_ALLOWLIST ... = {}` (empty), so no test-only functions are excused any more.


**Status:** one instance fixed (PR #1184), the repo-wide sweep deliberately
not attempted. Bug class:
`cli_surface.retired_spelling_in_remediation` in
`tests/regressions/manifest.py` —
check there first before restating the invariant.

Codex review on PR #1184 caught
`project_snapshot_legacy.materialize_release_variant_artifacts`'s
multi-variant ambiguity error still telling the caller to "pass an explicit
variant id (`--old-variant`/`--new-variant`)" after plan Phase 7j retired
both spellings with no alias. A user who followed the tool's own advice
landed straight in an exit-64 usage error — the CLI actively misdirecting
them. Fixed — twice. The first fix named the live `--variant` flag but
hard-coded the side to `old=`, so a caller whose *NEW* operand was the
ambiguous one was told to run something that fails just as hard (Codex
review, second round). The flag-existence oracle passed on that bug,
which is the lesson: **a live flag aimed at the wrong operand is still
broken advice.** The invariant is now the stronger one — *run the
advice*: the seed test takes the message's own `(e.g. --variant new=v1)`
example, re-invokes `compare` with it, and asserts the ambiguity is
actually resolved, parametrized over which side is ambiguous. A bare
`--variant ID` is not a safe fallback either, since it applies to both
sides and the unambiguous side does not declare that id — which is why
the side must reach the message at all.

The layering fix that makes it possible: the engine
(`AmbiguousVariantSelectionError`) states the fact and carries the
declared ids but names **no CLI flag**, because only the front end that
resolved both operands knows which side this package is;
`cli_compare_release_matrix._resolve_release_package_side` appends the
side-correct example. That is the same engine-error/CLI-wrapper split
`AmbiguousLibraryMatchError` already uses.

**Repo-wide sweep: closed, one residue.** The deferral reason above no longer
holds: `scan` was deleted (ADR-068 Phase 6), so a flag retired from
`compare`/`dump` is no longer live anywhere, and the question became
mechanically decidable. Every diagnostic that named a removed flag was
migrated -- the compile-context family now points at `compile.*` keys (or
`ABICHECK_AST_FRONTEND`/`ABICHECK_ALLOW_AST_FALLBACK`), `--dwarf-only`/
`--debug-format`/`--pdb-path` at `debug.*`, `--show-only` at `--view show=`,
`--format`/`--write`/`--output-dir` at `-o FORMAT=DEST` -- and the guards that
read options no command registers any more were deleted
(`cli_resolve._reject_compile_context_for_set_inputs`,
`cli_options.sided_frontend_explicit`/`_shared_frontend_explicit`, and two
stored-bundle rejections; `compile.lang` folded into
`reject_explicit_compile_config_for_stored_pair`).

The gate is `tests/test_diagnostics_name_live_flags.py`. Its oracle is the
live Click tree, not `RETIRED_SURFACES` (a retired-spelling list only catches
the retirements someone recorded), and its scope is *diagnostic sinks*
(`raise`, click echo/fail, logging, warnings, `*Error` constructors), which
excludes the categories listed above by construction: external-tool argv, the
release engine's own option definitions, and catalog/ruling prose are not
sinks. A `(was --old-flag)` migration hint is allowed.

**Residue:** `DEAD_CODE_ALLOWLIST` there excuses six functions that only tests
reach -- `cli_buildsource_helpers.parse_from_specs`/`_run_adapters`/
`_enforce_strict_mode`, `cli_buildsource_merge._merge_handle_conflicts`, and
`bundle.discover_artifact_set`/`audit_bundle` -- kept alive by test-local
re-implementations of the deleted `collect`/`merge` commands and
`scan --artifact-set`. Their messages name those commands' flags. Deleting
that code and its tests is the durable close; until then the allowlist test
fails as soon as any of them gains a production caller, so it cannot hide
live advice.

### ~~`compare --depth binary` still performs a deep DWARF type walk the public evidence-depth contract says that rung skips~~ — CLOSED (docs corrected)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** Struck through and closed 2026-10-01 by correcting the docs; tests/test_depth_binary_reads_dwarf.py pins the result.


> **Closed (2026-10-01) by correcting the documentation, not the code** —
> the maintainer's ruling on the either/or below. `binary` means L0 symbols +
> binary metadata + the L1 debug info a binary already carries; it skips the
> L2 header AST, not DWARF. `docs/use/evidence-depth.md`,
> `docs/learn/evidence-and-detectability.md` and the `--depth` help text on
> `compare`/`dump` now say so, and `tests/test_depth_binary_reads_dwarf.py`
> pins the docs, the help text, and the behaviour on real `gcc -g` binaries
> so the two cannot drift apart again. The record below is history.

**Reopened 2026-09-11** (Codex review, PR #1220 doc follow-up) after an
earlier pass at that same PR incorrectly marked this entry CLOSED,
reasoning that deleting `scan` (ADR-068 Phase 6) made the gap moot because
the comparison it originally described needed a `scan` reading on one side.
That reasoning was wrong: `scan`'s own `--depth binary` behavior was never
the bug — it was only the *oracle* this entry originally used to show
`compare`'s behavior was inconsistent with something. Deleting `scan`
removes that oracle, not the underlying defect in `compare` itself, and the
entry's own body already said as much before being closed ("that question
is now `compare`'s alone to answer" — see below). Verified live against the
*current* code before reopening, not taken on Codex's word alone:

```
$ gcc -shared -fPIC -g old.c -o libold.so      # struct Point { int x, y; }
$ gcc -shared -fPIC -g new.c -o libnew.so      # struct Point { int x, y, z; }
$ abicheck compare libold.so libnew.so --depth binary -o json=-
```

reports `verdict: BREAKING` with a `type_size_changed` finding ("Size
changed: Point (64 → 96 bits)") and a `type_field_added_compatible`
finding, from DWARF alone (no headers passed on either side). That directly
contradicts `docs/use/evidence-depth.md`'s own published contract for this
rung (`| binary | L0/L1 exported symbols + binary metadata + debug-info
*presence* (no deep DWARF type walk, no L2 AST) + always-on pattern scan |`)
— "debug-info presence" promises only "is DWARF present", not "walk every
DWARF type and report on it". `policy/depth_projection.py`'s own module
docstring confirms this is by design, not an oversight: it documents `BINARY`
as covering both L0 *and* L1 ("no L2 AST", explicitly *not* "no debug info"),
and keeps layout/signature facts at the `binary` rung whenever DWARF
confirms them, "the way a real DWARF-informed binary dump would". The
*code*'s contract and the *docs*' contract disagree with each other, and
this entry's status must track that disagreement as still open — reworded
below to describe the `compare`-only shape of the question now that `scan`
is gone, but **not marked CLOSED**.

Original gap, kept for context: `scan --depth binary` extracted exported
symbols only, while `compare --depth binary` on the same two live binaries
still read DWARF and reported type-level findings that the symbols-only
view could not produce. The two commands were not wrong in the same way:
`scan` honoured the pin as an *extraction floor and ceiling*, while
`compare`'s `--depth` projection (`policy/depth_projection.py`) drops the
L3-L5 layers but does not restrict the L1 debug parse the resolution
already performed, so the pin acted as a ceiling on collected layers rather
than on read evidence. Which of the two was the intended contract at this
rung was left open as a `compare`-side question (ADR-063 Phase 8's
`--depth` ceiling) — that question is now `compare`'s alone to answer,
since there is no `scan` reading to compare it against any more, but it
remains unanswered: either `docs/use/evidence-depth.md`'s "no deep DWARF
type walk" promise needs fixing to match what `compare --depth binary`
actually does, or `policy/depth_projection.py`'s `BINARY`-keeps-L1 behavior
needs to change to match the documented promise. Neither has happened.

ADR-068 Phase 4's typed-API slice had fixed the adjacent *headers*-rung
divergence before scan's removal — `cli_scan_helpers._uses_debug_presence_only`
dropped DWARF at the `HEADERS`/`BUILD` rungs even when no headers existed to
replace it, so a header-less `scan` lost every type-level finding while
naming the rung it was asked for — and the parity suite pinned the `headers`
and unpinned rungs against `compare` as an oracle
(`tests/test_scan_depth_evidence_shortcut.py`). That decision function and
its seed tests were deleted with the rest of `cli_scan_helpers.py` in ADR-068
Phase 6. The `evidence.tier_shortcut_without_substitute` `BugClass` this gap
was registered against in `tests/regressions/manifest.py` was retired in the
same PR rather than retargeted: a repo-wide audit at retirement time found
`debug_presence_only` (the underlying dumper-level shortcut parameter,
still plumbed through `dumper.py`/`service_dump_cache.py`/
`workflows/dump/native.py`/`dumper_layout_backfill.py`/
`workflows/input_resolution.py`) has **no remaining production call site**
that ever passes `debug_presence_only=True` — every live caller forwards it
at its default `False`. The shortcut mechanism is therefore currently
unreachable from any command, so the bug class it protected has nothing left
to seed-test against. If a future change reintroduces a caller that computes
`debug_presence_only` from a depth/collect-mode decision (the "none of the
L3/L4/L5 collect-mode decisions has an equivalent... guard yet" gap the
original entry also named, which remains open and un-closed by this
retirement), re-add a `BugClass` entry for it — retargeted to that new
caller's own seed tests, not restored verbatim, since the invariant text
should describe the caller that actually exists rather than the deleted
`scan`-specific one.

### ~~A depth shortfall is a hard per-member `ERROR` on a directory `compare` but a soft assurance signal on a single-pair one~~ — CLOSED

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** Struck through and closed in PR #1195; resolve_compare_request no longer calls enforce_requested_depth. Its noted 'third behaviour' (bundle stored pair) is a residual aside.


**Closed in PR #1195**, in the same PR that surfaced it, after a Codex
review round showed the divergence was wider than this entry first
described. Kept here because the shape of the mistake is the reusable part.

Measured, the two paths disagreed on *every* row, not just hard-vs-soft:

| shortfall | scalar `compare` | directory `compare` |
|---|---|---|
| live, `--depth headers` | 0 | 4 (`ERROR`) |
| live, `--depth build` | 7 | 4 |
| live, `--depth source` | 7 | 4 |
| stored snapshot, `--depth source` | 0 (live-only carve-out) | 4 |

Two mechanisms existed and the fan-out reached the wrong one first.
`policy/depth_evidence_contract.py` is the one this repo documents as
closing the gap "for every `compare` caller from one place" — exit-7 axis,
recorded not raised, `build`/`source` only, live-extraction only — and
`service_compare_pipeline.classify_compare_pair` already called it with the
right `old_is_live`/`new_is_live` flags. But `resolve_compare_request`
called `enforce_requested_depth` unconditionally ~170 lines earlier, which
raised first and pre-empted that recording in exactly the cases it was
written for. The native scalar CLI escaped only because it does not route
through `resolve_compare_request` at all.

The fix: `resolve_compare_request` no longer calls `enforce_requested_depth`,
so the exit-7 axis governs every `compare` surface; the release fan-out
aggregates each member's `evidence_contract_error_contribution` with `max()`
the way it already aggregates the contract-coverage floor, and emits its own
stderr notice (the per-member `DiffResult` is discarded before the note a
single-pair run renders). All eight rows of the matrix now agree, and a run
without `--depth` is unchanged.

`dump`'s own floors and `workflows.bundle_stored_pair_compare`'s separate
`enforce_requested_depth` call were deliberately **not** touched — each is a
different command with its own tested contract, and
`test_cli_compare_bundle_facts_stored_pair.py` pins the bundle one
explicitly (a stored pair *does* hard-fail there on the `headers` rung).
That is a third behaviour for the same question, still open, and worth
reading before anyone unifies further.

The reusable lesson is the one the bug class
`cli_surface.capability_guard_diverged_from_pipeline` now records: when two
mechanisms implement the same rule and one is documented as "the one place",
the other one silently winning on a subset of surfaces is not a redundancy,
it is a divergence waiting for a front-end change to expose it.

### `compare --dry-run`'s cost preview does not reflect `--since`'s changed-path seeding

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry says closed 2026-10-03: the dry run resolves the seed through compare_enrichment.resolve_compare_changed_seed, and tests/test_compare_dry_run_compile_db.py asserts the result.


**Closed (2026-10-03).** The dry run now resolves the seed through the
run's own `frontends/cli/compare_enrichment.resolve_compare_changed_seed`
before it emits, localizes its collect mode with it, names the replay scope
from the replay's own table (`workflows.changed_paths.replay_scope`), and
passes the seed and mode to the cost preview. The same pass made the
preview count the compile DB the run reads (`buildsource.inline.
plan_compile_db`). `tests/test_compare_dry_run_compile_db.py` asserts the
stated scope and the L4 TU count change with `--changed-path` (3 of its 16
scope cases fail on the previous code). The account below is kept as the
record of what was wrong.

Found during the PR #1220 doc follow-up (Codex review): a two-sided
`compare old.so new.so --depth source --since origin/main --dry-run`
example claimed the dry run "prints the translation units the seed selects
and the projected per-layer cost without scanning". Verified live against
the current code before fixing the doc: it does not.

`build_compare_dry_run_result` (`frontends/cli/compare_dry_run.py`) always
renders `"source scope: target on each side (compare has no PR change
seed)"` whenever `collect_mode` is `source-target`/`source-changed`/
`graph-full`, and its "Cost preview" section is built from
`workflows.compare_cost_preview.estimate_compare_dry_run_cost`, whose
signature accepts no `since`/changed-path parameter at all — so the TU
counts and per-layer cost it prints are the *unseeded*, full-target
numbers regardless of `--since`. The reason is ordering, not a missing
render field: `cli_compare_helpers.py`'s `--dry-run` branch calls
`emit_dry_run(...)` (which raises `SystemExit`, per `dry_run.py`) **before**
`_enrichment.resolve_compare_enrichment_inputs(since=since, ...)` —
the call that actually interprets `--since` and localizes `collect_mode` —
ever runs. `--since` genuinely does narrow the real (non-dry-run) replay;
only the dry-run preview is blind to it.

Tractable when picked up: resolve the changed-path seed (and its resulting
localized `collect_mode`/TU set) *before* the `--dry-run` emit, the same
way `--dry-run` already reflects other resolved-but-not-yet-executed
decisions (depth, headers, tool discovery) rather than echoing raw CLI
input back. Needs a regression test asserting the dry run's own TU
count/cost preview actually changes between `--since origin/main` and no
`--since` on the same real inputs, not only that the flag is accepted.

### The `compare --no-baseline` audit report carries no per-finding provider attribution, so `provider_assertions` is unvalidated

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** abicheck/report/no_baseline.py:429-431 now projects `row["providers"] = list(change.cross_source_providers)` per finding, so there is a public per-finding provider signal. Not re-checked: whether the validation runner has dropped `unvalidated_assertions`.


Every G20 audit case in `catalog/ground_truth.json` declares
`provider_assertions` — which providers must corroborate each finding
(e.g. case151: `private_header_leak` from **both** `public_header_ast` and
`source_index`; case148: `header_build_context_mismatch` from
`build_config` + `public_header_ast`). Legacy `scan` published these as
`crosscheck.providers`, and `skills-src/evaluation/validation/scripts/run_special_cli_examples.py`
checked them. ADR-068 Phase 6 retired the whole-audit orchestrator
(`scan_engine.py`) that built that block, and no replacement projection
landed in `report/no_baseline.py`, so the assertion is now unchecked by
anything.

**Measured, not assumed:** the rich and thin fixtures of case151 —
the case that exists purely to show corroboration growing with evidence —
produce *byte-identical* public reports today. Both answer
`evidence_tiers: ["elf", "header"]` and a single `private_header_leak`
finding. There is no public signal to validate the assertion against, so
the runner records `unvalidated_assertions: ["provider_assertions"]` and
`collect_full_example_matrix.py` surfaces it on the row (the same
mechanism as `kinds_strict`). That makes the gap visible in the matrix
artifact; it does **not** detect a regression that drops a provider while
still emitting the expected finding kind, and those rows still count as
`COVERED` (Codex review, PR #1225, raised twice — the second time
correctly pointing out that metadata alone changes no coverage
accounting).

**Why it was not closed in PR #1225.** The data already exists and the
fix is small and precisely located: `CrosscheckResult.providers` is
computed and serialized today, and
`workflows/cross_source_evolution._run_one_side` already reads it
(`evaluated = check in result.providers`) and discards the list — so
closing this means stamping `result.providers[check]` onto the emitted
`Change` and projecting it. What makes it a separate change is not
size: it bumps a **published** report schema
(`AUDIT_REPORT_SCHEMA_VERSION`, plus the two-sided compare report, since
`compute_cross_source_evolution` runs on both paths) and adds a public
`Change` field. This file's own root contract is explicit that a schema
or public-interface change "still needs its ADR and migration" and is not
something a repair PR folds in as a side effect.

The alternative offered in review — mark rows with a non-empty
`unvalidated_assertions` as `UNRESOLVED` — was declined for a stated
reason, not skipped: `skills-src/evaluation/validation/CLAUDE.md`'s matrix contract requires one
`COVERED` row per ground-truth entry and no `UNRESOLVED` rows, and *all
ten* audit cases declare `provider_assertions`, so it turns a currently
green required lane red for a capability removed upstream in PR #1211.
That is a maintainer call about blocking on the follow-up, not a
repair-PR decision.

### `Visibility.PUBLIC` used to be a proxy for "declared in the public API", conflating source presence with dynamic export — **fixed**

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The heading itself says fixed: model/surface_facts.py splits declared_in_headers/in_public_contract/binary_exported Fact fields (schema v46).


Reported against a real comparison, alongside the three defects the
"stop inferring public-contract membership from symbol name shape" change
fixed. **This one is now fixed too** — by the model/schema change it always
needed, not by a call-site patch. The account below is kept because the
shape of the conflation, and the reasoning about what a fix had to do, is
the load-bearing part; what changed is the last section.

`model/vocabulary.py`'s `Visibility` has three states, and its own comments
showed the conflation:

```python
PUBLIC = "public"      # default visibility / exported
HIDDEN = "hidden"      # __attribute__((visibility("hidden")))
ELF_ONLY = "elf_only"  # present in ELF symbol table, not in headers
```

`PUBLIC` meant *both* "declared in a public header" and "dynamically
exported"; `ELF_ONLY` meant "exported but not declared". There was no state
for the fourth, entirely ordinary combination: **declared in a public header
and not dynamically exported** — an inline member, a function the optimizer
fully inlined away, or one a `-fvisibility=hidden`/version-script change
stopped exporting. Detectors then used `f.visibility != Visibility.PUBLIC:
continue` as if it answered "is this declaration part of the public API",
which it did not.

`diff_namespaces.py` showed the consequence (`_func_index_items`,
`_collect_public_declared_names`, `_batch_demangle_public` all filtered this
way): the removal *event* is "absent from the new-side index", and a
declaration that is still present in the header — byte-identical, still
compiling for consumers of both header sets — dropped out of that index
purely because its emission changed. The run then reported a source-API
removal for an API that was not removed. The reported case was a build whose
default and public-only artifacts had **byte-identical headers** and differed
only in whether one method was dynamically exported; the two sides classified
as `PUBLIC` and `ELF_ONLY` respectively, and the surviving overload was
reported as removed.

The fix was not to ignore export changes for inline functions: a disappearing
dynamic export can still break an already-linked binary. The two facts are
independent and both matter, so the result reads as two findings, not one
wrong one:

- source declaration — still present, no removal;
- binary export — changed, evaluated on the binary-contract axis.

#### How it was fixed

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** Subsection of entry 82 describing the fix (model/surface_facts.py accessors, storage/fact_codec.py); it records closure history.


`model/surface_facts.py` owns the split. `Function` and `Variable` each
carry three `Fact[bool]` siblings — `declared_in_headers_fact`,
`in_public_contract_fact`, `binary_exported_fact` (schema v46,
`storage/fact_codec.py`) — and that module's accessors are the only
supported way to read them, each named for the question it answers
(`declared_in_headers`, `in_public_contract`, `binary_exported`, plus the
consumer-shaped `in_public_surface`, `is_abi_visible`,
`in_source_declaration_index`, `declaration_confirmed_absent`,
`is_export_confirmed_absent`). There is deliberately no fourth merged
boolean. Because they are real `Fact[...]` fields, the
`fact-detector-misuse` AI-readiness gate covers every reader.

The three points the original entry named, and what each became:

1. **The model change.** Done as above; `AbiSnapshot`'s `SCHEMA_VERSION`
   went to 46. A pre-v46 snapshot has no such keys, and
   `model/surface_facts.py`'s legacy bridge derives all three from the
   stored `visibility` value, marked `PARTIAL` with a
   `derived-from-legacy-visibility` diagnostic — so a stored baseline
   classifies exactly as it did before (pinned by
   `tests/test_surface_fact_split.py::TestLegacyBridgeIsBehaviourPreserving`)
   and the weaker provenance stays visible rather than being laundered into
   a producer's own claim. No enum member establishes (a) in either
   direction: not a positive out of "it was exported", and not a negative
   out of `ELF_ONLY` either, since both header backends assign `ELF_ONLY` to
   a declaration they parsed *out of a header* whose symbol landed in
   `.symtab`. The record's own header provenance is what answers (a) — a
   positive when it is present, unknown when it is absent — and it is
   consulted for every member alike.
2. **Producers answer only what they observed**
   (`extract/surface_fact_producers.py`). A header-AST backend asserts (a);
   it asserts (c) only when a real export table was consulted, and leaves it
   unknown for a header-only dump. DWARF/BTF/CTF leave (a) unknown — debug
   info is not header evidence. An export-table-only entry leaves (a) unknown
   unless headers were actually parsed. `provenance.tag_provenance` adds the
   scope-aware half: a declaration whose defining header is in the supplied
   public set is positive evidence for (b) — and only ever a positive, since
   origin-based *exclusion* is already its own separately controlled scoping
   decision and asserting the negative here would apply it twice, silently.
   An evidence-depth projection returns both header-derived facts to unknown
   (`headers_discarded_surface_facts`) instead of asserting their negation.
3. **The guards were audited.** Every `visibility != Visibility.PUBLIC`
   filter now names its question: public-surface membership
   (`in_public_surface`), the binary union the old `(PUBLIC, ELF_ONLY)`
   tuples spelled out (`is_abi_visible`, replacing `diff_symbols._PUBLIC_VIS`
   and `diff_time64._ABI_VISIBLE`), export-table-only-ness
   (`is_export_table_only_record`), the *intersection* of (b) and (c) that
   the single `is PUBLIC` test used to mean (`is_public_export`), a confirmed
   non-export (`is_export_confirmed_absent`), or — for `diff_namespaces`' removal
   indexes, the site the report reproduced — the source-declaration
   population (`in_source_declaration_index`), which is blind to (c) by
   construction.

`compare/export_transition.py` is the second finding the entry asked for,
and it is symmetric in both directions: a matched pair whose export went
away while the declaration stayed reports `FUNC_VISIBILITY_CHANGED`/
`VAR_VISIBILITY_CHANGED` on the binary axis, and one that *gained* an export
reports the compatible `FUNC_EXPORT_ADDED`/`VAR_EXPORT_ADDED`. Both
directions are gated on *confirmed* evidence on both sides, so "exported
before, unknown now" (or its mirror) is never rendered as an observed
transition.

Three review findings on the fix are worth recording, because each is the
same mistake in a different place — reaching for a predicate that answers a
*neighbouring* question:

- **The gain direction was missing at first.** Before the split a
  promised-but-unexported declaration failed the old `(PUBLIC, ELF_ONLY)`
  filter outright, so the pair never matched and a newly exported
  declaration was reported as `FUNC_ADDED`. Once the declaration keeps its
  place on both sides the pair matches — and with only a loss-side detector
  the run reported *nothing at all*. Trading one wrong finding for a silent
  diff is worse than the bug being fixed, and an addition that vanishes is
  what "record before disposing" forbids. Hence the two compatible kinds,
  and one entry point per owner (`check_function`/`check_variable`) so a
  caller cannot enumerate the directions and miss the next one.
- **A binary-symbol subject needs the intersection, not the union.**
  Replacing a `visibility is Visibility.PUBLIC` test with
  `in_public_surface` silently widens the subject to declarations the
  artifact never exported. `diff_templates`' instantiation-survival index is
  the sharp case: its own docstring says a stale `extern template`
  declaration must not count as surviving, and that declaration is exactly a
  promised-but-unexported entity, so the union admitted it and suppressed
  `INSTANTIATION_MISSING_FROM_BINARY`. `is_public_export` is the named
  intersection, and it reproduces the legacy enum test exactly (`PUBLIC`
  true, `ELF_ONLY`/`HIDDEN` false).
- **`Visibility.ELF_ONLY` cannot establish header absence.** The legacy
  bridge originally read it as a confirmed "not declared in any header". It
  is not: both header-AST backends assign `ELF_ONLY` to a declaration they
  parsed *out of a header* whose symbol landed in `.symtab` rather than the
  dynamic table, and a headerless pre-v46 snapshot reaches the same member
  with no header parse at all. Fact (a) is now answered from the record's
  own header provenance for every member alike; which member it was is a
  different question, still answered by `is_export_table_only_record`, so
  the ELF-only removal kind and the stub-record consumers are unaffected.

Every finding with one declaration behind it
carries a `surface_facts` block (report schema 4.4) stating all three as
`"true"`/`"false"`/`"unknown"` — always complete when present, because an
omitted key is what a reader mistakes for a negative.

Bug class: `evidence.export_presence_as_declaration_presence`
(`tests/regressions/manifest.py`), whose one registered residual is that the
generalized test builds its snapshots in-process; a live castxml/clang dump
of two builds differing only in a version script is owned by the
`integration` lane. The sibling name-shape defects stay under
`classification.name_shape_as_contract_membership`.

### ~~The native `dump` CLI does not stamp `excluded_header_patterns` on the snapshot it writes (2026-09-16)~~ — CLOSED

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry is struck through as closed, and that holds: tests/test_dump_records_header_exclusions.py exists and pins the stamp through the dump CLI.


> **Closed (re-verified 2026-10-01).** No longer reproduces: `dump` now
> executes through `workflows.input_resolution.resolve_input`, whose wrapper
> stamps the *achieved* patterns (`record_achieved_header_exclusions`). The
> 2026-09-16 measurement below predates that. An earlier triage misread the
> sectioned snapshot envelope as "not stamped"; read it through
> `load_snapshot`. `tests/test_dump_records_header_exclusions.py` now pins the
> stamp through the `dump` CLI and the comparability gate it feeds (a `dump`
> baseline vs a `compare` candidate, across symmetric and asymmetric
> exclusion sets). The record below is history.

**Measured, not inferred** (2026-09-16, while wiring
`scope.exclude_headers` through `dump`): a snapshot written by
`abicheck dump ... --exclude-header c2.h -o base.json` carries
`excluded_header_patterns: null` and `excluded_header_matching: null`. The
config spelling behaves the same way, because both reach the same place. A
`compare` operand, by contrast, *is* stamped -- `cli_resolve.
_resolve_compare_snapshots` builds an `InputSpec` that reaches
`workflows.input_resolution.resolve_input`, which calls
`model.header_exclusion_record.record_header_exclusions`. The native `dump`
CLI executes through `service_dump_pipeline.execute_dump_request` instead
and never reaches that recorder.

**Why it matters.** The recorded field is the entire input to two
mechanisms, and both are blind to a `dump`-produced baseline:

- `extract.header_exclusions.exclusion_asymmetry_reason`, the comparability
  gate that refuses a pair whose two sides were narrowed by different rules.
  A baseline dumped under `--exclude-header a.h` compared against a
  candidate narrowed by `b.h` reads as `((), ("b.h"))` -- asymmetric in the
  data, but the gate sees the baseline's `()` as "excluded nothing", which
  is exactly the false-symmetry case `record_header_exclusions`'s own
  `extracted_now` parameter was added to prevent in the other direction.
- `confidence.header_exclusion_warnings`, so such a run reports full
  header-aware assurance over a surface nobody looked at -- the promise
  `--exclude-header`'s help text makes ("reported as reduced evidence") and
  that this field exists to keep.

**Not fixed here.** It is a pre-existing gap in `dump`'s own execution path,
orthogonal to the three defects this change addresses, and the fix is a
question about where the recorder belongs rather than a line to add: the
`compare` path stamps inside `resolve_input`, which `execute_dump_request`
deliberately does not go through, so either the recorder moves to a point
both share or `execute_dump_request` grows its own call and the two can
drift. Both are real choices with their own blast radius (the second
reintroduces exactly the "two places record the same fact" shape the
`extracted_now` fix had to reason about).

Until it is fixed, consistency between a `dump` baseline and a `compare`
candidate rests on both commands *resolving* the same rules, which is why
`scope.exclude_headers` is honored by `dump` as well as `compare`
(`frontends/cli/dump_debug_config.resolve_dump_scope_exclude_headers`) --
that wiring is load-bearing, not a convenience on top of a gate that would
otherwise have caught the divergence.

### ~~An `--exclude-header` pattern spelled as a path matches nothing on Windows (2026-09-16)~~ — FIXED

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry is struck through as FIXED. tests/test_header_exclusion_separator_invariance.py exists, and glob_header_matches now also matches the /-spelled form.


> **Fixed (2026-10-01), and the premise was partly wrong.** `fnmatch` applies
> `os.path.normcase` to both sides, and on Windows that already turns `/` into
> `\`, so a forward-slash pattern against a *live* Windows path did match. The
> real defect was elsewhere and host-dependent: a stored snapshot's
> `source_header` keeps the *dumping* host's separators, while normalization
> depends on the *reading* host -- POSIX normalizes nothing, so a
> Windows-dumped `C:\inc\fftw\fftw3.h` read on Linux never matched
> `fftw/*` (and a backslash pattern never matched a Linux path).
> `model.header_exclusion_record.glob_header_matches` now also matches the
> `/`-spelled form of both sides. It only *adds* matches where a backslash is
> present, so every POSIX path/POSIX pattern pair is decided exactly as before
> -- the migration concern below does not arise for any input that already
> matched. `tests/test_header_exclusion_separator_invariance.py` states it as
> invariants over generated spellings on both simulated hosts, against the
> previous rule as oracle; the `skipif(win32)` in
> `tests/test_header_exclusion_primitives.py` is removed. The record below is
> history.

`extract/header_exclusions.apply_header_exclusions` tries each pattern against
three spellings of a header: the bare name (`fftw3.h`), the full path
(`str(path)`), and `*/<pattern>`. Only the first is platform-independent.
`str(path)` is backslash-separated on Windows, and `fnmatch` treats `/` and
`\` as ordinary characters, so a forward-slash pattern (`include/fftw3.h`,
`**/fftw/*`) matches no header there while matching on POSIX. A Windows user
must either spell the pattern with backslashes or fall back to the bare name.

**Not fixed here**, and deliberately not papered over in the test that found
it (`tests/test_header_exclusion_primitives.py` states the bare-name spelling
as an unconditional contract and marks the path-shaped spellings
`skipif(win32)` pointing at this entry, rather than asserting a POSIX-only
claim as if it were universal). The fix is a real decision with its own blast
radius: normalizing both sides to POSIX separators would change which headers
an existing Windows invocation excludes, and the patterns are hashed into
`excluded_header_patterns`, the comparability gate
(`exclusion_asymmetry_reason`) and the effective-configuration digest -- so a
normalization that alters matching also alters run identity, and a baseline
dumped before it would stop being comparable against a candidate dumped
after. That is a migration, not a one-line `as_posix()`.

### ~~`effective_depth` reports `source` for a `--depth headers` dump whose only L5 is the header-only graph (2026-09-16)~~ — FIXED (2026-09-17)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry is struck through as FIXED 2026-09-17. tests/test_analysis_assurance_implicit_depth.py exists and pins the report depth equal to the gate's label.


Same PVXS run as the entry above: `dump --depth headers` produced a snapshot
whose `compare` reported `L5 source graph summary: present` on both sides and
`effective_depth: source`, while L3/L4 correctly read `not_collected`.

**The L5 summary itself is expected and is not the bug.**
`service_header_graph_attach._attach_header_graph` has attached a *header-only
(L2) semantic graph* to `AbiSnapshot.surface_graph` on every headers-depth
dump since G29 Phase A (ADR-041 addendum). It is a declaration graph, not
source-tier evidence.

**The over-claim is the depth label.** `evidence_depth.py` already carries two
labels and documents this exact trap:

- `depth_label_for` reports `source` whenever L4 **or** L5 carries facts;
- `gated_source_label` refuses that for the header-only case, discriminating
  on `l4_source_abi_was_attempted(pack)` — "a non-empty L5 can also come from
  a header-only (L2) declaration graph that never ran any source-tier replay
  at all".

`buildsource/check_report.derive_effective_depth` reads
`old_evidence_depth`/`new_evidence_depth` off the compare report, which come
from the *honest* label — so the strict gate knows the dump only reached
`headers` while the reported `effective_depth` says `source`. A consumer
reading the report (an assurance policy, a CI gate, a reviewer) is told
source-tier evidence was collected when none was.

**Fixed (2026-09-17).** `evidence_depth.reported_depth_label` is the one
rule every *report* now uses -- it is `gated_source_label`, the gate's own
function, not a parallel copy of its reasoning. Both consumers were
converted together, because a document whose
`analysis_assurance.effective_depth` and whose
`old_evidence_depth`/`new_evidence_depth` disagreed would have been worse
than the original over-claim:
`analysis_assurance.compute_analysis_assurance` (which calls it directly;
the `_effective_depth_label` wrapper that used to stand in front of it was
deleted along with two other pure-delegation aliases) and
`cli_dump_helpers.evidence_depth_label` (the compare report's two side
depths and `dump`'s own provenance stamp) both go through it.
`buildsource/check_report.derive_effective_depth` needs no change and gains
a correct answer: a
`--depth source` check over header-only-L5 evidence now reads `degraded`
instead of `complete`.

Two values move, as this entry predicted. A run whose only L5 is the
header-only graph reports `headers`/`build` where it reported `source`.
And -- the direction that is easy to miss -- the gate's rule is *not*
uniformly stricter: a zero-match source-only dump (replay parsed TUs and
linked nothing, because there is no binary to link against) leaves L4 and
L5 both empty, so `depth_label_for` answers `build` while the gate
correctly answers `source`. That case now reports `source` too. Neither
direction moves a verdict or an exit code; `check_requested_depth_satisfied`
already gated on this rule, which is the whole reason a report disagreeing
with it was a bug. `tests/test_analysis_assurance_implicit_depth.py`
states the invariant as equality with the gate over the whole
snapshot/pack cross product, rather than as a one-sided bound a second
quietly-different rule would also satisfy.

### Nothing stopped a test fabricating a `Change` state the types forbid (2026-09-16)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** Closed by the test-change-symbol-typed gate, which is registered at scripts/check_ai_readiness.py:3299.


An earlier revision of this section claimed, under the heading "`Change.symbol`
is annotated `str` but a real detector emits `None`", that a real
`public_surface_shrank` finding carries `symbol=None`. **That claim was
wrong**, and the way it was reached is the actual gap worth recording.

The `None` was observed by instrumenting `report/review_groups.py` during
`tests/test_policy_registry_and_summary.py`, and read as evidence about
production. It was not: it came from three hand-written fixtures in that
test file (`Change(..., symbol=None)`). The real producer of that kind,
`diff_surface_metrics.py`, passes the `"<surface>"` sentinel — a `str`, as
the annotation requires. Verified: `make_change(symbol: str)` in
`diff_helpers.py`, no `# type: ignore` on any `Change(` construction under
`abicheck/`, and `mypy abicheck/` clean. So there is no production/annotation
disagreement, and widening the field to `str | None` (measured: 84 new mypy
errors across 24 files) would have been a breaking change to a public type in
service of a defect that does not exist.

**The real gap.** `mypy` runs over `abicheck/` only, so `tests/` could
construct a `Change` in a state the types forbid and nothing anywhere would
say so — and a downstream reader could then be "fixed" against an impossible
input, or, as here, a false gap recorded from one. Typechecking `tests/`
outright is not available as a fix (`mypy --explicit-package-bases tests/`
reports 26,780 errors against an entirely unannotated suite).

**Closed by** a narrow structural gate instead: `check_ai_readiness.py`'s
`test-change-symbol-typed` rejects `Change(..., symbol=None)` and
`make_change(symbol=None)` anywhere under `tests/`. The three fixtures now
pass `"<surface>"`, matching the producer.

**What remains true about `review_groups`.** The `display_name` fallback to
the finding's kind stays, and still earns its place — the *reachable*
nameless case is the empty string, which is type-valid and which
`qualified_name or demangled_symbol or symbol` resolves to unchanged, leaving
a group rendered under an empty heading. `tests/unit/report/test_review_groups.py`
states that as an order-independence property over every mix of named and
nameless findings, with a vacuity guard asserting the fixture really is
nameless, and constructs it with `""` rather than a fabricated `None`.

### `SurfaceGraph.reached_by` was built for every graph and read by nobody (2026-09-17)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** reached_by was removed. grep finds no SurfaceGraph.reached_by in abicheck/; the only hit is an unrelated local, reached_by_option, in buildsource/source_graph_findings.py:1302. The entry is mostly a lesson about eagerly built indexes.


Recorded as a class, not an incident. `reached_by` was materialised eagerly at
`build_surface_graph()` time from ADR-027's original design sketch, at
O(roots x types) cost, and a full-tree audit found its only readers were three
test assertions and the ADR's own code block — no production consumer ever
existed. It has been removed; the relation is still derivable in two lines from
`public_roots()` and `reachable_types()`.

The general point for the next index added to a shared structure: an eagerly
materialised derived relation on a widely-constructed object is paid for by
every construction, including the overwhelming majority that never read it, and
nothing in the type system or the test suite reports that. When adding a field
like this, either give it a consumer in the same change or compute it at the
query that needs it.

### A record's triviality change is invisible without DWARF (2026-10-08)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** The entry says closed, and tests/test_castxml_record_traits_integration.py exists.


**Closed (2026-10).** CastXML records now carry `is_trivially_copyable`,
derived tri-state from the special members CastXML emits
(`extract/headers/castxml/record_traits.py`): `True` only when every
copy/move constructor, copy/move assignment and the destructor is implicit,
a copy constructor exists, there is no virtual function or base, and every
base and member is itself proven; `False` on a virtual or a user-provided
special member (a declaration not ending in `= default`) or a non-trivially
copyable base/member; otherwise unknown. A stripped binary plus headers now
reads `case69_trivial_to_nontrivial` as `BREAKING` (`trivially_copyable_lost`)
under both header backends; a `= default` destructor stays unknown and
reports nothing. Oracle test against g++'s own `__is_trivially_copyable`:
`tests/test_castxml_record_traits_integration.py`.

### `compare --used-by` prints a traceback for an unreadable REQUIRED consumer (2026-10-08)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** abicheck/frontends/cli/compare_report.py:107 catches ConsumerUnreadableError and translates it.


**Closed (2026-10, Lane C stage 5, CodeRabbit review).** A REQUIRED
`--used-by` consumer that cannot be read (unrecognised format, digest
mismatch, unparseable import table) raised `ConsumerUnreadableError` out of
the compare command uncaught: exit 1 with a Python traceback.
`frontends/cli/compare_report._apply_scoped_gating` now translates it to a
`click.ClickException` -- the same exit status, 1, with a one-line
`Error: --used-by consumer: ...` message -- without growing the
over-baseline `cli_helpers_compare.py`.

### `appcompat_consumer_impact.py` cannot move into `workflows/` yet (2026-10-08)

**Status (re-verified 2026-10-09 at `456989f`): CLOSED.** abicheck/workflows/consumer_impact.py exists and abicheck/appcompat_consumer_impact.py is gone.


**Closed (2026-10, Lane C stage 3).** The module is now
`workflows/consumer_impact.py`, beside `workflows/consumer_scope.py`. Both of
its unclassified dependencies were resolved first: `format_dependency_path`
moved to the `compare`-classified `buildsource/source_graph_compare.py`
(stage 2), and `buildsource/graph_impact.py` was classified `compare` once its
call-edge label constants moved to `model/graph_vocabulary.py` (stage 3; see
its entry above).
