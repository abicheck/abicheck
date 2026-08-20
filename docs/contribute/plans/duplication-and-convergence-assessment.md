---
doc_type: contributor
level: advanced
lifecycle: active
---

# Project-wide duplication assessment and convergence plan

**Type:** Architectural assessment plus a phased convergence plan. Not a
gap-closure plan against a `usecase-registry.yaml` entry — this is a
cross-cutting initiative plan, alongside
[CLI cleanup, phase two](cli-cleanup-phase-two.md),
[G33 (typed API convergence)](g33-typed-api-and-mcp-convergence.md), and
[G32 (comparability contract)](g32-comparability-contract-and-multi-tu-manifest.md),
all three of which this plan builds directly on and names throughout.
**Related:** [ADR-037](../adr/037-cli-interface-contract.md) (CLI interface
contract), [ADR-049](../adr/049-contract-relevance-and-compatibility-configuration.md)
(contract-relevance/compatibility configuration), [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md)
(comparability contract), [ADR-054](../adr/054-cli-project-integration-surface-consolidation.md)
(root-surface admission), [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md)
(typed request/result completeness), [ADR-056](../adr/056-multi-artifact-library-set-scan.md)
(`scan --artifact-set`)
**Effort:** XL (five phases, each independently landable; Phase 1 alone spans
several PRs) · **Risk:** high for Phase 1 (touches every extraction call
site — `dump`, both `compare` operands, `scan` candidate and baseline,
release fan-out); lower for later phases, which mostly consume what Phase 1
and Phase 2 produce.

## Why this document exists

This assesses `abicheck` as of `main` at commit `e674d3127634176dc7a41566bd1943bf6f481176`
(2026-08-20) for **semantic duplication**: places where the same
responsibility — a decision, a normalization, a workflow — exists in more
than one codepath, expressed as different code that is intended (and mostly
succeeds, at real correctness cost recorded in `AGENTS.md`'s "Known gaps")
to produce equivalent results. That is a materially more dangerous class of
duplication for this codebase than literal copy-paste, because two
independently-evolving implementations of "resolve this input" or "compute
this exit code" drift silently, one bug fix at a time, until a real-world
report surfaces the divergence (see AGENTS.md's L3→L2-fold and
`include_sequence` entries for concrete incidents of exactly this).

The verdict: **the project is midway through convergence, not badly
designed.** The compare pipeline (`service_compare_pipeline.py` +
`service_input_resolution.py`) already demonstrates the right architecture —
parse frontend input, build a typed request, resolve once, execute once,
classify once, project into different outputs. The problem is that this
pattern is not yet the *mandatory* architecture for `dump`, `scan`, release,
aggregate, and `compat`. This plan's job is to make it mandatory.

## Scope summary

| Area | Current state | Duplication risk |
|---|---|---:|
| Binary/debug/header parsers | Mostly appropriately separated by backend | Low |
| Diff detectors and policy classification | Generally shared and registry-driven | Low–medium |
| `compare` resolution | Properly converged around typed service APIs | Low |
| Artifact extraction for `dump`, `scan`, and `compat` | Several partially equivalent paths | **High** |
| Configuration and pack application | One resolver, several runtime representations | **High** |
| Exit and gate calculation | Partially unified, with operation-specific parallel folds | **High** |
| Report construction | Significant post-render mutation and format-specific repair | **High** |
| Release and matrix aggregation | Reinterprets outputs rather than consuming a common result model | Medium–high |
| CLI/service dependency direction | CLI concepts still leak into engine/service layers | **High** |

## What is already right — and why it's the model

**The compare pipeline.** `service_compare_pipeline.py` splits comparison
into `resolve_compare_request()` and `classify_compare_pair()`. The CLI, the
typed Python API, and the former MCP path (see ADR-021, retired) share
resolution and classification without forcing CLI-specific configuration
work into the service layer — `cli_compare_receipt.py`'s ADR-049 resolution
step runs *between* the two phases, exactly because they're split. This is
the template: **parse frontend input → build typed request → resolve once →
execute once → classify once → project into different outputs.**

**Per-side resolution.** `service_input_resolution.py` exists specifically
so a comparison pair and a standalone dump don't each carry their own copy
of "resolve one input into a snapshot" — it owns `resolve_side_snapshot`,
`embed_side_build_source`, `enforce_requested_depth`, and
`reject_hybrid_source_frontend`. That is the correct abstraction boundary;
the gap (Phase 1 below) is that not every caller uses it yet.

**Working single-source-of-truth efforts** worth preserving as patterns:
`snapshot_io.py` (canonical snapshot storage envelope), the `ChangeKind`
registry deriving `BREAKING_KINDS`/`API_BREAK_KINDS`/`COMPATIBLE_KINDS`/
`RISK_KINDS` rather than hand-maintaining four sets, shared CLI option
decorators (`cli_options.py`) for cross-command concepts, and the stored
SONAME mapping in bundle resolution that prevents a later graph traversal
from independently reconstructing a subtly different one.

## Hotspots, in priority order

### P0 — Artifact extraction and evidence resolution

The largest duplication and correctness risk. At least ten
partially-equivalent paths exist today:

1. Typed dump: `DumpRequest → resolve_dump_request() → execute_dump_request()`
   (`service_dump_pipeline.py`)
2. Native ELF CLI dump: `dump_cmd → perform_elf_dump()` (`cli_dump_helpers.py`)
3. Native PE/Mach-O dump: a separate non-ELF path (`handle_non_elf_dump`)
4. Scan candidate/baseline: `scan_engine._build_new_snapshot()`, which calls
   `service.resolve_input()` directly rather than routing through
   `service_input_resolution`
5. Dump dry-run: `render_dump_dry_run()`, a second, independent
   approximation of resolution — its own docstring calls this "Cheap,
   read-only resolution only... never runs castxml/clang"
6. Standalone application-compatibility: `appcompat.check_appcompat()` calls
   `dumper.dump()` directly for both sides (its own docstring: "Dumps and
   compares the two libraries itself"), bypassing every one of the five
   paths above — a caller who reaches `check_appcompat()` directly (rather
   than through `compare`'s own app-usage scoping, which calls
   `scope_diff_to_app()` against an already-resolved diff instead) gets none
   of what `ResolvedArtifactPlan` would eventually centralize: resource
   lifetime, the L3→L2 compile-context fold, cache-relevant paths, or
   post-processing hooks.
7. `deps compare`: `stack_checker._run_abi_diff()` calls `dumper.dump()` for
   both sides and `checker.compare()` directly (`abicheck/stack_checker.py`),
   and `cli_stack.py`'s `deps_compare_cmd` independently computes its own
   process exit code from `result.loadability`/`result.abi_risk` rather than
   through any shared exit model — Phase 3 covers this with two new axes
   and a `DepsCompareExitPolicy` (see that section).
8. The probe harness: `probe_harness._snapshot_object_file()` (used by
   `run_probe_matrix(..., snapshot=True)`, the header-only-library
   compile-and-snapshot driver behind G25/G26-family evidence-tier work)
   also calls `dumper.dump()` directly on each compiled probe object.
   Deliberately called out separately from the seven user-facing operations
   above, since it isn't one — see "backend-level exception" below.
9. L0 export-delta re-extraction: `l0_export_delta.collect_l0_export_delta()`
   — invoked by both native `compare` and scan baseline reconciliation —
   independently calls `service.resolve_input()` twice with
   `symbols_only=True`, a *supplementary* extraction distinct from either
   side's primary resolution. Missing this from the migration would let
   `compare`/`scan`'s primary-side equivalence tests pass while this
   secondary path still misses the centralized lifetime, fingerprint, and
   post-processing behavior.
10. The plugin-host API: `appcompat.check_plugin_host_contract()` — the
    plugin-host counterpart to `check_appcompat()` (path 6 above) — also
    calls `compare_snapshots()` directly rather than through a typed
    comparison request, for the identical reason path 6 is listed.

`service_dump_pipeline.py` documents directly that native dump behavior
historically lived around `resolve_input()` in CLI code, forcing non-CLI
consumers to reimplement or omit those steps. AGENTS.md's "Known gaps"
section records the concrete cost of this divergence in detail: duplicate
inferred build queries contending on the same lock for up to 600 seconds
(fixed by `seed_includes_and_fold_compile_context`); the L3→L2 compile-
context fold reaching `dump` and `compare`'s implicit-dump operand but not
`scan`'s candidate or baseline resolution (three separate follow-up fixes,
numbered 8/12/13/15 in that entry); derived include directories
participating in some AST cache keys but not others (findings 2, 10, 17);
and a real, still-open `include_sequence` mismatch between a `dump` baseline
and a `scan --against` of it, confirmed against live Bazel/castxml CI
evidence and still not fully root-caused as of this writing.

**Target shape:**

```text
ArtifactRequest
    ↓
resolve_artifact_request()
    ↓
ResolvedArtifactPlan
    ↓
execute_artifact_plan()
    ↓
ArtifactResult
```

`ResolvedArtifactPlan` carries: normalized input type and binary format;
requested and effective evidence depth; selected frontend and compiler;
headers and public-header scope; effective include search; effective
compile context; build/source collection plan; dependency scope;
cache-relevant paths; post-processing steps; provenance inputs.
`ArtifactResult` carries: the snapshot; achieved evidence depth; extraction
contract and fingerprints; effective compiler context; coverage and
degradation; executed stages and timings; advisories; post-processing
results. The same pipeline must serve `dump`, each side of `compare`, scan
candidate and native baseline extraction, release per-library extraction,
ABICC descriptor extraction, standalone appcompat's own dump-both-sides
path, and `deps compare`'s per-dependency-pair extraction.

**Lifetime problem.** Some effective include paths can point into a
temporary inferred-build directory that is deleted once the resolving
function returns (the deferred-cleanup design AGENTS.md's L3→L2-fold entry
documents at length). Returning more paths from a resolve step is not
sufficient — and scoping the resource to `execute_artifact_plan()` alone is
*also* not sufficient, for two reasons the design has to account for
together: the directory can already be at risk of cleanup by the time
`resolve_artifact_request()` returns — before any `execute_...` call ever
starts — and the dry-run path below resolves a plan but deliberately never
executes it, so a scope that only opens at `execute_artifact_plan()` would
never close for dry-run at all. Ownership has to span resolution through
execution (or through dry-run's own inspection), not begin partway through:

```python
with resolve_artifact_request(request) as plan:
    # plan.session owns the inferred-build directory (if any) from here
    if dry_run:
        return render_plan(plan)          # closes on context-manager exit
    with execute_artifact_plan(plan) as result:
        run_header_graph(result)
        attach_build_context(result)
        persist_snapshot(result)
        # result borrows plan.session's resources; still open here
```

`resolve_artifact_request()` returns a context-managed
`ResolvedArtifactPlan` whose `__exit__` releases whatever resources
resolution itself allocated (regardless of whether execution ever runs), and
`execute_artifact_plan()` borrows that same session rather than opening a
second one — so cleanup happens only after every extraction and
post-processing consumer, *and* dry-run's own inspection, has finished. This
removes the need for each call site to re-derive its own ordering and
deferred-cleanup rules — the exact class of bug the L3→L2-fold entry's fifth
finding (self-deadlocking duplicate inferred queries) had to be fixed for
one call site at a time.

One more failure mode this shape has to close, not just the two above:
Python fully evaluates `resolve_artifact_request(request)` — the function
body runs to completion or raises — *before* `with` ever calls `__enter__`
on whatever it returns. If resolution allocates the inferred-build
directory and only *then* fails a later validation step within its own
body, no `ResolvedArtifactPlan` is ever returned for a `with` block to call
`__exit__` on — the directory and its lock leak regardless of how carefully
the caller-side `with` is written, since the caller never sees an object at
all. `resolve_artifact_request()` therefore can't be "allocate, then
return a context manager" — its own body needs a `try`/`finally` (or an
`ExitStack` it owns and only hands off to the returned plan on success) so
a failure partway through resolution tears down whatever it had already
allocated before the exception propagates, rather than leaving that
responsibility for a caller who received nothing.

**Dry-run renders the plan, not a second prediction.** `resolve_dump_request`
(added by the CLI-cleanup-phase-two "PR C" slice) already provides a real
"resolve without executing" mode — the missing piece is wiring
`render_dump_dry_run()` to build from it, through the same
context-managed `ResolvedArtifactPlan` described above, instead of
independently re-deriving depth/collect-mode/backend feasibility (and
instead of any bespoke cleanup of its own — dry-run closes the same session
resolution opened, on the same `with` exit, whether or not execution ever
runs). Dry-run should report one of: definitely valid; definitely invalid;
unresolved until execution; requires trusted build execution — never
maintain its own approximation of execution semantics.

### P0 — Effective configuration and pack application

`pack_application.py` follows the right rule: it reads pack contributions
from the already-resolved `CompatibilityEvaluationConfig` and its
per-field `ValueProvenance` rather than reimplementing precedence or
conflict resolution (see AGENTS.md's own extensive documentation of D7/D8
in the module map). But the resolved answer still gets translated into
several different runtime shapes: single-pair `compare` uses a typed
resolved configuration; `scan` has its own receipt/configuration flow;
policy packs fold into `PolicyFile`; gate packs fold into `SeverityConfig`
and raw scheme strings; and the release fan-out has no equivalent typed
object at all — `apply_release_gate_pack()`'s own documentation states it
manually mirrors pack-application logic against six raw gate/severity
strings because release has no `ResolvedCompareConfig`-shaped object to
read from.

**Target:** one runtime object,

```python
@dataclass(frozen=True)
class EffectiveGate:
    exit_code_scheme: str      # e.g. "legacy" or "severity"
    severity: EffectiveSeverity
    require_complete_analysis: bool

@dataclass(frozen=True)
class EffectiveEvaluationConfig:
    policy: EffectivePolicy
    gate: EffectiveGate
    contract: EffectiveContract
    assurance: EffectiveAssurance
    surface: EffectiveSurface
    evidence: EffectiveEvidencePolicy
    suppressions: EffectiveSuppressions
    provenance: ConfigProvenance
    digest: str
```

`suppressions` carries resolved rule identity, not just a policy summary —
the existing `CompatibilityEvaluationConfig` already models `suppressions`
as its own field, separate from `policy`, precisely because a suppression
rule directly changes which findings reach verdict and gate calculation.
Without it here, two runs given different `--suppress` inputs could share
an identical `EffectiveEvaluationConfig` and digest while producing
different findings and exit codes — defeating the digest's whole point as
a parity key.

`gate` carries the resolved `exit_code_scheme` alongside severity, not
severity alone — for a run combining `--exit-code-scheme legacy` with a
severity-only gate pack, severity by itself can't recover which scheme was
selected, so a consumer would otherwise have to keep an out-of-band raw
string (defeating the point of one runtime object) or re-derive the scheme
from severity, reintroducing the exact bug CLI-cleanup-phase-two's PR B
already found and fixed once (a re-derived scheme let a severity-only gate
pack silently override an explicit `--exit-code-scheme legacy`).
`require_complete_analysis` belongs in the same object for the identical
reason, not as a separate follow-on: two otherwise-identical runs differing
only by `--require-complete-analysis` exit successfully in one and fail in
the other on incomplete evidence, and the existing digest implementation
(`effective_config_digest.py`) already records this input as its own
`gate.require_complete_analysis` key — leaving it out of the sole runtime
object this section proposes would mean two runs with genuinely different
gate behavior could still land on the same `EffectiveEvaluationConfig` and
digest. All three `gate` fields feed the digest.

This object is consumed directly by `compare`, `scan`, the release
fan-out, and bundle/matrix findings alike, with the resolver remaining the
*only* place D7's precedence order (`explicit_cli/api_request >
legacy_alias > run_recipe > run_profile > project_config >
built_in_default`) is decided. The digest becomes a real parity key: same
normalized request + same effective-config digest ⇒ same policy/gate
interpretation, everywhere. (The reporter's existing effective-configuration
digest, landed for PR B, is a real, narrower precursor to this — see
"Relationship to in-flight work" below.)

"Bundle findings alike" is not aspirational here — it names a real,
already-documented gap this phase has to close, not just avoid repeating.
AGENTS.md's own "Known gaps" entry ("Bundle-level (cross-library) findings
on a directory/package `compare` never respect any policy override")
records that `bundle.compare_bundle()` computes `BundleDiffResult.
bundle_verdict` via `checker_policy.compute_verdict(changes)` with no
`policy=` argument at all — always the hardcoded `strict_abi` default —
while `_run_bundle_analysis`/`_collect_bundle_result`
(`cli_compare_release_helpers.py`) have no policy/`PolicyFile` parameter
either. So a release-wide `--policy`, `--policy-file`, or a `kind: policy`
pack overriding a `BUNDLE_*` kind reaches every *per-library* finding
correctly but silently leaves bundle-level findings (`bundle_library_
removed`, `bundle_intra_dep_removed`, ...) governed by the built-in policy —
a bundle finding can keep a release's worst-of verdict at `BREAKING` even
after the same kind was demoted or ignored everywhere else. Phase 2 closes
this specifically by having `compare_bundle`/`_run_bundle_analysis`/
`_collect_bundle_result` accept and classify against the same
`EffectiveEvaluationConfig` every per-library comparison already uses,
rather than a bundle-specific policy parameter threaded through in
isolation — with test coverage asserting policy parity between a release's
per-library and bundle-level verdicts for the same run, which is the gap
the AGENTS.md entry names as still open.

### P0 — Exit and gate decisions

`ExitDecision` (from CLI-cleanup-phase-two's "PR G1") models compatibility/
scoped-gate contribution, contract-coverage contribution, analysis-assurance
contribution, and promoted scan-crosscheck contribution — but explicitly not
yet `NOT_COMPARABLE`, scan budget overflow, release removed-library policy,
release operational errors, or aggregate's missing/unexpected-target
policies. Release therefore still owns its own precedence chain (not
comparable first, then removed-library exit 8, then operational-error
floor, then severity/verdict, then contract coverage folded separately), and
`aggregate` has to reverse-engineer upstream results — e.g. distinguishing
whether a scan's published exit `1` means contract coverage or a genuine
scan failure, because `scan`'s exit code is already an opaque fold of
several axes by the time aggregate sees it.

**Do not** simply extend the current implementation with `max()` over more
numeric codes — exit-code integers are not a reliable cross-operation
priority ordering (a `compare` `4` and a `scan` `4` do not always mean "the
same severity of thing is wrong"). Instead, make contributions and their
precedence explicit:

```python
@dataclass(frozen=True)
class ExitContribution:
    axis: ExitAxis
    active: bool
    code: int
    priority: int
    details: Mapping[str, object]

@dataclass(frozen=True)
class ExitDecision:
    code: int
    primary_reason: ExitAxis  # CLEAN when every contribution is inactive
    contributing_reasons: tuple[ExitAxis, ...]
    contributions: tuple[ExitContribution, ...]
```

with axes covering `clean` (mirroring the existing `ExitDecision`'s own
`ExitReason.CLEAN` — a successful run still needs a real, non-optional
`primary_reason` rather than one implementation fabricating a failure axis
and another making the field optional), `compatibility_gate`,
`scoped_gate`, `contract_coverage`, `analysis_assurance`,
`crosscheck_promotion`, `not_comparable`, `budget_overflow`,
`evidence_contract_error` (`scan`'s own `EVIDENCE_CONTRACT_ERROR` verdict —
`service_scan.run_scan()` returns it, with `exit_code=1`, when an explicitly
pinned `--depth` can't collect its required evidence; documented separately
from `not_comparable`/`budget_overflow` in `run_scan_core()` today, and
without its own axis it can only collapse into a generic
`operational_error` or stay the opaque exit-1 heuristic this phase exists
to remove), `bundle_incomplete` (`scan --artifact-set`'s own
`BUNDLE_INCOMPLETE` verdict — `service_scan.run_scan_set()` returns it,
also `exit_code=1`, when the member scans complete but not every snapshot
needed for the cross-library audit could be built; a second, distinct
scan-only incompleteness signal from `evidence_contract_error`, not a
duplicate of it), `operational_error`, `removed_required_artifact`,
`missing_required_target`, `unexpected_target`, and two axes for `deps
compare`'s own dependency-loadability result — `dependency_load_failure`
and `dependency_abi_risk` — since `cli_stack.py`'s `deps_compare_cmd`
today distinguishes three outcomes (loadability/ABI-break failure → 4,
ABI risk or loadability warning → 1) that don't map onto any axis above;
and per-operation policies (`NativeCompareExitPolicy`, `ScanExitPolicy` —
now also covering `evidence_contract_error` and `bundle_incomplete` —
`ReleaseExitPolicy`, `AggregateExitPolicy`, `AbiccExitPolicy`, and
`DepsCompareExitPolicy` for the two new dependency axes plus the existing
`not_comparable`) that read the same evaluated result but keep each
operation's own external exit-code contract — `compat`'s `0/1/2/...`
mapping in particular should
be derived through `AbiccExitPolicy`, not bypass the shared model.

### P1 — Reporting composes too late

Several renderers currently render a string, then reparse and patch it:
`service_render._render_json_output()` serializes via `to_json()`, parses
the JSON back, inserts dependency information, and re-serializes; dump
provenance is folded into already-rendered JSON text
(`fold_dump_provenance_into_dict`/`_into_json`); scoped-gate handling parses
rendered JSON, swaps full/scoped verdicts, adds findings not present in the
original `changes` array, recomputes summaries, and carries separate repair
logic per format (one-line, Markdown, review, SARIF, JUnit). Each of these
is well-commented precisely because each documents an incident where one
format disagreed with another because business semantics were applied
during or after formatting rather than before it.

**Target:** a canonical report intermediate representation, computed before
any serialization:

```python
@dataclass(frozen=True)
class ReportEnvelope:
    operation: OperationKind
    schema_version: str
    operational_state: OperationalState  # SUCCESS / NOT_COMPARABLE / ERROR / UNAVAILABLE
    inputs: InputReport
    resolution: ResolutionReport
    effective_config: EffectiveConfigReport
    evidence: EvidenceReport
    findings: tuple[ReportFinding, ...]
    full_evaluation: EvaluationSummary
    effective_evaluation: EvaluationSummary
    exit_decision: ExitDecision
    dependencies: DependencyReport | None
    timings: StageTimings
    advisories: tuple[Advisory, ...]
```

with `full_evaluation` (the whole-library result) and `effective_evaluation`
(post-scoping, e.g. `--used-by`/`--required-symbol`) kept distinct, and
every renderer (JSON, Markdown, SARIF, JUnit, HTML, one-line) as a pure
projection — none of them modifying verdicts, inventing findings,
recomputing gate status, or parsing another renderer's output.
`operational_state` is its own field, not folded into `exit_decision` or
either `EvaluationSummary` — the "Smaller, concrete duplication" section
below states the rule this field exists to satisfy: `OperationalState`
(`SUCCESS`/`NOT_COMPARABLE`/`ERROR`/`UNAVAILABLE`) must stay a distinct
axis from `CompatibilityVerdict` ordering, never spliced into it. Encoding
it only as an `ExitDecision` contribution would still leave aggregate and
renderers reconstructing operational status from exit-code semantics — the
exact drift this envelope exists to end; placing it inside either
`EvaluationSummary` would conflate a compatibility result with whether a
comparison could be evaluated at all, which is precisely what
`aggregate.py`'s existing, correct separation of these concerns already
gets right and this envelope must not regress.

### P1 — Aggregate consumes representations, not decisions

`aggregate.py` correctly keeps compatibility, gate, target availability,
contract coverage, and analysis assurance conceptually separate, and
correctly refuses to treat a missing required report as a compatibility
verdict. But because upstream report shapes differ, it maintains
format-specific extraction rules for native-compare severity blocks, scan's
nested diff decisions, scan's top-level exit mapping, contract-coverage and
analysis-assurance contribution recovery, legacy-verdict fallbacks, and
release's operational-error sentinel. Once the `ReportEnvelope`/
`ExitDecision` work above lands, aggregate should read one uniform
`evaluation`/`exit` shape and stop inferring meaning from an integer based
on which command produced the document.

### P1 — ABICC compatibility is a parallel frontend and engine path

`abicheck compat` intentionally keeps a distinct user-facing contract — that
part is correct and should stay. But its implementation calls
`dumper.dump` and `checker.compare` directly rather than through the typed
dump/compare pipelines, then applies its own post-comparison
transformations, report dispatch, and exit mapping — so it structurally
cannot receive fixes that live only in the typed orchestration layer (evidence
resolution, contract/assurance processing, canonical exit decisions). Target:
an ABICC adapter that builds `DumpRequest`/`CompareRequest`/
`EffectiveEvaluationConfig` and hands off to the shared pipelines, keeping
only genuinely ABICC-specific concerns (flag aliasing, XML descriptor
parsing, suppression translation, report shape, exit-code mapping) in the
adapter itself. Post-comparison transformations (strict mode, source-only
filtering, warn-on-new-symbol) should become declared evaluation-policy
inputs rather than `DiffResult` mutations applied after classification.

### P1 — Compiler invocation handling needs one typed model

Recent fixes (referenced in this repository's git history and in
AGENTS.md's compiler-invocation and toolchain-profile entries) centralized
launcher stripping, environment-prefix handling, driver recognition,
split-operand decoding, and canonical encoding across build adapters,
header-compile-context derivation, L2 replay, L4 source replay, and include
graph collection — because equivalent compiler commands were being
interpreted differently by different consumers. The next step is moving
from shared *helper functions* to a shared *parsed object*:

```python
@dataclass(frozen=True)
class CompilerInvocation:
    original_argv: tuple[str, ...]
    recorded_directory: Path
    effective_directory: Path
    environment: EnvironmentOverlay
    launchers: tuple[str, ...]
    driver_token: str
    resolved_driver: Path | str
    driver_mode: DriverMode
    language: Language
    standard: str | None
    target: TargetConfig
    defines: tuple[DefineOp, ...]
    include_search: tuple[IncludeSearchEntry, ...]
    forced_includes: tuple[Path, ...]
    abi_flags: tuple[AbiFlag, ...]
    opaque_flags: tuple[str, ...]
```

`recorded_directory` is the compile-database entry's own `directory` field
(or the equivalent for a live build-adapter query) — not optional, since a
relative source or include operand's meaning depends on it. Two
otherwise-identical `argv` values executed from different directories can
resolve entirely different headers, so recording `original_argv` alone
would force replay and include normalization to either fall back to
abicheck's own process cwd (silently wrong whenever that differs from the
compile unit's own recorded directory) or keep an out-of-band value next to
the parsed object — exactly the "parse once" contract this model exists to
establish. It participates in the invocation's own identity, the same way
this field already has to for the L3→L2 fold's cache-key/relative-path
handling described in AGENTS.md's own entry for that work. `recorded_directory`
alone is not sufficient, though — the two fields split for a real reason
the existing `_argv.py`/`clang.py` parsing machinery already had to solve:
a launcher prefix can itself change the effective directory (`env -C build
clang ...`, GNU `env`'s documented `-C`/`--chdir`) independently of any
`recorded_directory` compose (`env -C a env -C b ...` → `a/b`), so
`effective_directory` — mirroring the existing `effective_directory()`
helper — is what replay must actually resolve relative operands against,
**including a compile-database entry's own `@response-file` expansion**:
`build_context.py`'s existing parser currently expands response files
against the recorded directory before stripping/applying launcher
prefixes, which is the identical class of bug this field exists to
close — response-file expansion belongs after launcher-prefix resolution,
against `effective_directory`, exactly like every other relative operand.
`recorded_directory` stays the raw, unmodified compile-database value for
identity/provenance. `environment` is a small structured type, not a bare
mapping (or a mapping-vs-cleared-sentinel union — a first pass at this
model tried exactly that and it still couldn't represent GNU `env`'s real
grammar, `env [OPTIONS] [NAME=VALUE]... COMMAND`, which lets `-i`/`-u` and
inline assignments compose in one invocation: `env -i CPATH=/sdk clang
...` clears the inherited environment and then sets one variable on top;
`env -u CPATH clang ...` removes a single named variable, independent of
any clearing; either shape can carry any number of ordinary `NAME=VALUE`
assignments alongside it):

```python
@dataclass(frozen=True)
class EnvironmentOverlay:
    clear_inherited: bool                    # a clear occurred somewhere in the prefix chain
    unset: frozenset[str]                    # names removed AND NOT later reset — final state
    assignments: tuple[tuple[str, str], ...] # NAME=VALUE surviving to the driver, in argv order
```

A plain `{}` (or a single cleared-vs-not sentinel) can't carry this: it
can't distinguish "no override recorded" from a real `env -i` clear, can't
hold an assignment applied *on top of* a clear in the same invocation, and
can't name which variable(s) an `-u` removed — and the distinction matters
concretely, since `CPATH`/`C_INCLUDE_PATH`/`CPLUS_INCLUDE_PATH` directly
alter compiler include search, not merely driver lookup the way `PATH`
does. Without this, replay would have to rescan `original_argv` itself
(defeating the parse-once contract again) or risk resolving a different
include search than the real build used. This generalizes, rather than
replaces, `_argv.py`'s existing `_EnvPathCleared` sentinel and
`path_cleared` state — but the equivalence is narrower than "OR the two
flags": `"PATH" in unset` alone is always correct (an explicit unset always
means "no `PATH`"), while `clear_inherited` alone is **not** — a clear
followed by a later `PATH=...` assignment in the same folded chain (`env -i
PATH=/sdk clang ...`) leaves `PATH` genuinely set, not cleared, since the
fold (per the ordering note above) already applied the assignment on top
of the clear. The correct equivalence is `"PATH" in unset or
(clear_inherited and "PATH" not in {name for name, _ in assignments})` —
"cleared and never reassigned." The parsed model should carry what a real
`env` invocation can express, not a simplified projection of it.

`unset` and `assignments` are **not** independently-collected sets that a
consumer merges later — nested launchers can make identical raw field
*contents* describe opposite final environments depending on order (`env
FOO=x env -u FOO clang ...` ends with `FOO` absent; `env -u FOO env FOO=x
clang ...` ends with `FOO=x`; both are two real, distinct, legal GNU `env`
invocations). The single parse must fold the whole launcher-prefix chain
*in argv order* — the same left-to-right traversal `_argv.py`'s
`_traverse_env_and_launcher_prefix()` already performs — into one
normalized final state before populating this dataclass: `assignments`
holds only the value each name has *after every later operation in the
chain*, `unset` holds only a name removed and never subsequently
reassigned, and a later `-i` clears whatever `assignments`/`unset` state
the fold had accumulated so far, not merely the ones this dataclass would
otherwise expose. Once folded, the two fields are safe to read
independently — the ordering risk lives entirely in how they are
*produced*, not in what they represent once parsing is done.

Raw compiler-command parsing happens once; replay, ambiguity detection,
build-option drift, and reporting all consume the structured fields instead
of re-scanning argv.

### P1 — Dependency direction and CLI leakage

`scan_engine.py` — documented as the shared engine for both CLI and typed
API — still imports `click`, raises `click.ClickException`, prints via
`click.echo`, and imports helpers from `cli_scan_baseline`/
`cli_scan_helpers`. `service_input_resolution.py` imports
`_is_inputs_pack_dir` from a CLI helper module. `service.py` uses a dynamic
`importlib` import specifically to stay invisible to the static
import-cycle checker rather than resolve a real cycle. Target architectural
rule, to become a real `check_ai_readiness.py`-style gate (Phase 0 below):

```text
models / leaf utilities
        ↑
domain primitives
        ↑
artifact / compare / scan engines
        ↑
service/application operations
        ↑
CLI / Python facade / Action / compat adapters
```

Engine modules may not import `click`; engine/service modules may not
import `cli_*`; CLI modules may not call `dumper.dump`, `checker.compare`,
or `service.resolve_input` directly (mirroring the existing `cli-contract`
gate, which already enforces the last rule for `checker.compare` — the
other two constraints are new); frontends only build requests, call
application operations, render results, and translate exceptions;
pack detection belongs under `buildsource`, not a CLI helper module;
progress notification uses callbacks/events, not `click.echo`.

## Smaller, concrete duplication

**Verdict ordering** is independently re-derived in `BundleDiffResult`,
the release summary rollup, and the aggregate rollup (aggregate additionally
carries its own legacy exit map). Separate `CompatibilityVerdict` ordering
(`NO_CHANGE < COMPATIBLE < COMPATIBLE_WITH_RISK < API_BREAK < BREAKING`)
from `OperationalState` (`SUCCESS / NOT_COMPARABLE / ERROR / UNAVAILABLE`) —
an operational state should never be spliced into a string-keyed
compatibility ordering; let rollup policy decide how one dominates or
coexists with the other.

**Bundle findings are lowered too early.** `BundleFinding` mirrors `Change`
and then flattens bundle attribution (consumer/provider) into the
description string purely to reuse existing reporters. A `FindingLike`
protocol that keeps `subject`/`attribution` as structured fields would let
bundle-specific data stay data instead of becoming a formatted prefix.

## What should explicitly *not* be unified

This plan is about collapsing decisions that are duplicated, not about
erasing legitimate domain differences. Keep separate:

- ELF, PE, Mach-O, DWARF, PDB, BTF, and CTF parsers
- CastXML and Clang extraction backends
- pair-only decisions (old/new extraction concurrency, pair-wide
  language-standard reconciliation) — `service_compare_pipeline.py`'s own
  module docstring already explains why these stayed out of the per-input
  primitives in `service_input_resolution.py`
- bundle symbol-resolution analysis versus per-library ABI detection
- ABICC's report shape and external exit-code compatibility contract
- JSON, Markdown, SARIF, JUnit, and HTML serializers
- identity rules that genuinely differ by entity type (functions,
  variables, types — see `finding_identity.py`'s own tiered design)

The boundary: different backends may collect facts differently, but must
return the same typed domain models, and must never independently decide
configuration, evidence depth, verdicts, gates, or report semantics.

## Target architecture

```text
CLI / Python API / ABICC / GitHub Action
                  │
                  ▼
           Frontend adapter
        parse syntax; no decisions
                  │
                  ▼
           OperationRequest
                  │
                  ▼
         resolve_operation()
                  │
                  ▼
         ResolvedOperation
     ┌────────────┼────────────┐
     │            │            │
 artifact plan  effective    set/matrix
                config        plan
     └────────────┼────────────┘
                  ▼
          execute_operation()
                  │
                  ▼
             RunResult
       snapshots + raw findings
                  │
                  ▼
          evaluate_result()
                  │
                  ▼
           EvaluatedResult
     verdicts + scope + coverage
          + ExitDecision
                  │
                  ▼
          build_report_model()
                  │
                  ▼
           ReportEnvelope
     ┌──────┬──────┬──────┬──────┐
     ▼      ▼      ▼      ▼      ▼
   JSON    MD    SARIF   JUnit   HTML
```

One producer, many projections — not several producers kept equivalent
through ongoing parity fixes.

## Implementation sequence

### Phase 0 — Architectural guardrails first

Add tests establishing the desired ownership *before* moving more code,
mirroring how `scripts/check_ai_readiness.py`'s `import-cycle-growth` and
`cli-contract` checks already work (baseline-and-shrink, not
block-everything-immediately):

1. No `scan_engine`, `service_*`, `artifact_*`, or `buildsource` engine
   module imports `click` or `cli_*`.
2. No CLI or `compat` module calls `checker.compare`, `dumper.dump`, or
   `service.resolve_input` directly (extends the existing `cli-contract`
   gate, which today only covers `checker.compare`).
3. Every artifact extraction call site *for the seven user-facing
   operations named in Phase 1* routes through the future artifact
   application service. Deliberately excludes `probe_harness.
   _snapshot_object_file()`: it has no CLI/API entry point today (nothing
   outside `probe_harness.py` and its own tests calls
   `run_probe_matrix()`), so it is a backend-level exception recorded here
   explicitly, not a call site this guardrail can silently forget — if a
   real user-facing command starts calling `run_probe_matrix()`, that
   command's routing becomes a Phase 1 item at the same time, not a
   drive-by addition to this guardrail's allowlist.
4. Every *completed-operation exit of a modeled compatibility-analysis
   command* — one of the operations `ExitDecision`'s axes actually cover
   (`compare`, `scan`, release, aggregate, `compat`, appcompat, `deps
   compare`) — derives from an `ExitDecision` (Phase 3). `dump` is
   deliberately excluded from this list, not merely unmentioned: a plain
   `dump` performs no compatibility evaluation at all — its own target
   pipeline (P0's artifact-resolution section above) ends at
   `ArtifactResult`, never at an evaluated compatibility result, and Phase
   3's per-operation policy list has no `DumpExitPolicy` for exactly that
   reason. Requiring `dump`'s exit to derive from `ExitDecision` would force
   fabricating compatibility-evaluation state a bare extraction command
   never has. Scoped deliberately in two further directions: (a) a bad
   invocation or an aborted run has no evaluated result to derive a
   decision from, and `cli.py`'s `_AbicheckGroup.main` already, correctly,
   maps those before any operation runs (Click `UsageError` →
   `_EXIT_USAGE_ERROR`/64, `click.exceptions.Abort` → 1); (b) a `project
   validate`/`project validate-build`/`project plan`-family command
   (`cli_project.py`) builds its own evaluated report and exits `0 if
   report.ok else 1` on a question `ExitDecision`'s axes don't model at all
   (config/build-manifest validity, not ABI compatibility) — this guardrail
   must not force any of these three shapes into `operational_error`, or
   demand a permanent, unreviewable exception for any of them.
5. Every persisted *compatibility-analysis* report (the same modeled
   operations as item 4) is built from a `ReportEnvelope` (Phase 4).
   Scoped identically and for the identical reason: `project validate` and
   `project validate-build` (`cli_project.py`) persist their own validation
   report via `--output` too, but that report has no ABI findings, no
   full/effective evaluation, and no compatibility `ExitDecision` to carry
   — it answers a config/build-manifest validity question, not this
   guardrail's question. Forcing it through `ReportEnvelope` would mean
   fabricating compatibility semantics a config-validity report doesn't
   have; a project-config report is either out of scope for this check or,
   if it should eventually gain its own generic envelope, that is its own
   design question left to a `project`-specific follow-up, not solved by
   stretching `ReportEnvelope` to cover it.
6. Every effective evaluation carries a digest (Phase 2).

Each check starts with a reviewed allowlist of acknowledged pre-existing
violations, the same pattern `IMPORT_CYCLE_ALLOWLIST` already uses — the
list must only shrink, and a new entry requires the same sign-off bar
AGENTS.md already sets for that allowlist (an ADR or explicit maintainer
sign-off, not a routine PR).

### Phase 1 — Finish artifact-resolution convergence

1. Introduce `ResolvedArtifactPlan` as a context-managed session that owns
   any resource resolution itself allocates (e.g. an inferred-build
   directory) from `resolve_artifact_request()` onward — not scoped to
   `execute_artifact_plan()` alone, since dry-run resolves without ever
   executing and must still close the same session.
2. Move `perform_elf_dump`'s remaining post-processing hooks (ADR-039
   build-context collection, the header-graph second pass, the optional
   clang-layout-tool attach) into explicit post-processing stages against
   the new plan/result shape.
3. Route native `dump` through the typed artifact pipeline (closing the
   long-open "`dump` doesn't build a `DumpRequest`" gap named in G33 and
   CLI-cleanup-phase-two's PR C).
4. Route scan candidate and native baseline through the same pipeline.
5. Route PE/Mach-O through the same orchestration while preserving
   backend-specific extraction.
6. Route `appcompat.check_appcompat()`'s standalone dump-both-sides path
   through the same pipeline too, so a direct caller of that function gets
   the same resource lifetime, compile-context fold, and cache-relevant
   paths `compare`'s own app-usage scoping already benefits from.
7. Route `deps compare`'s per-dependency `_run_abi_diff()` through the same
   pipeline, and fold its loadability/ABI-risk exit computation
   (`cli_stack.py`'s own `sys.exit` calls) into Phase 3's `ExitDecision`
   work rather than leaving it as yet another independent exit-code path.
8. Make dry-run render the resolved plan.
9. Delete the now-redundant duplicated seed/fold/resolve paths this closes
   over (several are already named as follow-ups in AGENTS.md's L3→L2-fold
   entry).

Highest-value phase: it removes both correctness duplication (the
`include_sequence`/comparability-mismatch class of bug) and real
performance duplication (redundant inferred build queries).

### Phase 2 — Make resolved configuration the runtime contract

1. Introduce `EffectiveEvaluationConfig`.
2. Move `compare` to consuming it directly.
3. Move `scan` to the same object.
4. Move the release fan-out off its six raw gate/severity strings.
5. Include the effective-config digest in every report and every
   aggregate input (building on the reporter's existing digest work from
   CLI-cleanup-phase-two's PR B).
6. Keep compatibility wrappers only at public API boundaries (the typed
   Python API's existing dataclasses stay stable; only their internal
   plumbing changes).

### Phase 3 — Complete `ExitDecision`

1. Model every exit axis named above.
2. Separate priority from numeric code.
3. Add the per-operation exit policies.
4. Publish all contributions in reports.
5. Remove aggregate's scan/report-type-specific heuristics.
6. Keep ABICC's external exit-code mapping as `AbiccExitPolicy`.

### Phase 4 — Introduce the canonical report model

1. Build findings, scope, verdict, coverage, assurance, dependencies, and
   exit before any rendering happens.
2. Migrate JSON rendering first (it's the format every other renderer and
   `aggregate` itself already treats as authoritative).
3. Migrate SARIF, JUnit, Markdown, review, and HTML.
4. Remove the "render → parse → patch → render" functions this obsoletes.
5. Make release and aggregate consume or embed the same envelope.

### Phase 5 — Migrate compatibility and multi-artifact operations

1. Make ABICC descriptors adapters into typed requests.
2. Express `compat`'s strict/source-only/new-symbol behavior as evaluation
   configuration where the shape allows it.
3. Introduce shared `ArtifactSet`, `ArtifactPair`, and
   `SetComparisonResult` types.
4. Share matching and rollup primitives between release, `scan
   --artifact-set` (ADR-056), and bundle operations.
5. Keep distributed report aggregation a distinct operation, but have it
   consume the same envelope as everything else.

## Acceptance tests

The highest-value tests here are cross-path *equivalence* tests, not more
example-specific regression tests — this is what a token/AST-based clone
detector cannot catch, since the class of bug this plan targets is
different code intentionally computing the same thing.

**Artifact-resolution equivalence.** For one artifact and equivalent
options, `dump`, compare-side resolution, scan candidate resolution, ABICC
descriptor resolution, `appcompat.check_appcompat()`'s own per-side
resolution, `deps compare`'s per-dependency-pair resolution, release's own
per-library extraction, and `l0_export_delta.collect_l0_export_delta()`'s
`symbols_only=True` supplementary re-extraction (invoked by both native
`compare` and scan baseline reconciliation, independent of either side's
primary resolution) must produce identical: snapshot semantic fingerprint;
extraction-contract fingerprint; effective evidence depth; effective
compile-context digest; public-surface scope fingerprint; dependency
scope; build/source coverage; cache-relevant directory set. Release
belongs here specifically because its adapter does real, release-only work
ahead of the shared resolution step:
`cli_compare_release._run_compare_pair()` follows GNU ld linker scripts and
applies its own per-library header/include mapping before ever calling
`service.run_compare()` — the target shape's promise to "serve... release
per-library extraction" is otherwise untested at the one place release's
extraction can still diverge even when ordinary compare-side resolution
passes cleanly. Standalone appcompat, `deps compare`, release, and the L0
re-extraction all belong in this matrix, not just in Phase 1's routing
list — a phase that satisfies every equivalence test here except one
direct caller's path would still leave that caller resolving depth,
compile context, cache paths, or resource lifetime differently from
everything else.

**Comparison equivalence.** For one comparison, native `compare`, `scan
--against`'s own nested baseline comparison, release per-library compare,
release's own global probe-matrix comparison (when `compare-release`
receives `--probe-matrix-old`/`--probe-matrix-new`,
`cli_compare_release._collect_matrix_result()` separately loads
suppression/policy/pack state and calls `compare_snapshots()` over
synthetic snapshots carrying `extra_changes` — a comparison distinct from
every per-library one this same command also runs), the Python API,
`appcompat.check_appcompat()`'s pre-scope `compare_snapshots()` call
(before its own `scope_diff_to_app()` step applies app-usage narrowing),
`appcompat.check_plugin_host_contract()`'s identical pre-scope comparison
(the plugin-host counterpart to `check_appcompat()`), each dependency
pair's `_run_abi_diff()` inside `deps compare`, and `compat`'s ABICC
adapter's own comparison *before* its intentional
strict/source-only/new-symbol-warning transformations
(`compat/_helpers.py`'s `_apply_strict()` and siblings) apply must produce
identical: canonical finding IDs (`finding_identity.py`); effective
verdicts; configuration digest; contract decisions; assurance decisions;
compatibility exit contribution. Naming appcompat, `deps compare`,
release's matrix comparison, and ABICC's pre-transformation comparison here
matters independently of Phase 1's own migration list — that phase, and
Phase 5's ABICC migration, only guarantee their *extraction* moves onto the
shared pipeline; without this equivalence test also covering their
*comparison* step, their finding IDs, contract decisions, or verdict
processing could still silently diverge even after extraction converges.
ABICC's own strict/source-only/new-symbol transformations are intentional,
documented ABICC-compatibility behavior (not divergence to eliminate) —
test them as before/after pairs against the shared pre-transformation
result, the same way `scan`'s crosscheck promotion is tested below, rather
than requiring the *post*-transformation result to match `compare`'s.
Scoped deliberately to `scan`'s baseline comparison rather than its overall
result: `scan --against --crosscheck KEY=error` intentionally lets
`scan_engine._crosscheck_severity_exit` promote an otherwise-clean run to
`API_BREAK` (recorded as `promoted_crosscheck`) — a real, scan-only axis
Phase 3's `crosscheck_promotion` contribution deliberately preserves, not
a divergence to eliminate. Requiring `scan`'s *overall* effective verdict
to match `compare`'s would either fail on this correct behavior or invite
removing the promotion; test scan-specific contributions (crosscheck,
budget overflow) separately from this equivalence check.

**Renderer equivalence.** Every renderer, given the same `ReportEnvelope`,
must expose the same effective verdict, finding IDs, blocking findings,
exit code, and exit reasons.

A token/AST-based clone detector may still be worth adding as a secondary,
advisory CI signal, but it should be understood as catching a different
(much smaller) risk than the equivalence tests above.

## Relationship to in-flight work

This plan does not compete with the existing initiative plans — it names
where they converge and what remains once each is fully landed:

- **[CLI cleanup, phase two](cli-cleanup-phase-two.md)** already names three
  of the same prerequisites this plan generalizes (one typed `dump`
  resolution path — its "PR C"; one effective pack/gate configuration —
  its "PR B"; one canonical exit decision — its "PR G1", already merged).
  Phases 1–3 here are the full generalization of those three PRs across
  every operation, not just `compare`/`scan`/release.
- **[G33](g33-typed-api-and-mcp-convergence.md)** built the schema registry
  and `CompareRequest`/`CompareResult` completeness this plan's Phase 1–2
  extend to `dump` and the artifact-resolution surface generally; its own
  "Phase 6" note (a standing sequencing constraint on ADR-049's rollout)
  applies unchanged to this plan's Phase 2.
- **[G32](g32-comparability-contract-and-multi-tu-manifest.md)**'s
  comparability contract (`ExtractionContract`, `scope_fingerprint`) is
  exactly the fingerprint machinery Phase 1's acceptance tests reuse — this
  plan does not propose a second comparability mechanism.
- **[Public contract default](public-contract-default.md)** (ADR-049) is
  the compatibility-configuration resolver Phase 2 makes the sole runtime
  contract; this plan does not change ADR-049's own D7/D8 precedence rules,
  only how uniformly the *result* of applying them reaches every operation.

## Out of scope

- Rewriting binary/debug/header parsers, or merging CastXML and Clang
  backends into one implementation (see "What should explicitly not be
  unified" above).
- A general-purpose clone-detection tool; noted as an optional secondary
  signal, not a replacement for the equivalence tests this plan specifies.
- Any change to ABICC's or `compat`'s external, user-facing contract
  (flags, exit codes, report shape) — Phase 5 changes only *how* that
  contract is implemented internally.
- Any change to the ChangeKind registry, detector registration pattern, or
  identity-resolution tiers (`finding_identity.py`) — these are cited
  throughout as examples of the target pattern already working correctly.
