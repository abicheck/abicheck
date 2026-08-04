# ADR-055: Typed Request/Result Completeness and a Schema-Version Registry

**Date:** 2026-07-27 (D1–D3); Gap 3/D4 added 2026-07-27 after a second,
more detailed external review of the same subject was checked line-by-line
against `mcp_server.py` (see "Amendment" note below Gap 2)
**Status:** Accepted — implemented (D1–D4). Each decision below carries an
"As implemented" note recording where the implementation departed from it;
"Implementation notes" at the end covers what is still open and how this
line itself went stale.
Written up after an external API-layer review of `abicheck`
(CLI/Python/MCP/Actions/schemas) turned out to be largely stale against
current `main` — G22/ADR-037 already landed the CLI consolidation and the
`checker.compare`-vs-Tier-2 chokepoint the review flagged as missing — but
verifying that review's claims against the actual code (`service.py`,
`api_types.py`) surfaced two real, narrower, already-self-documented gaps.
**Correction:** this ADR's first version also asserted, as a Non-goal, that
`abi_compare` "already routes through `compare_snapshots`/the same Tier-2
layer as the CLI" and therefore needed no further MCP-surface work.
Re-checking that specific claim against `mcp_server.py` line-by-line
(prompted by a second, independent review raising the same point with more
detail) found it true only for the final snapshot-diffing step, not for the
tool as a whole — see Gap 3 below, now folded in as D4 rather than left a
Non-goal. All four are now closed; the decisions below are kept in their
original proposing voice, with each one's "As implemented" note recording
where reality diverged.
**Verified:** main@2e43d53 on 2026-08-04
**Decision maker:** (pending)

---

## Context

ADR-037 (G22, **Accepted — implemented**) gave `abicheck` a real Tier-2
chokepoint: `abicheck/api_types.py` ships `InputSpec`/`CompareRequest`
(+`validate()`)/`OutputSpec`, and `service.run_compare_request` is the single
classification path every front-end (CLI `compare`, the folded
`compare-release`/`deep-compare` aliases, `appcompat`, and the MCP server's
`abi_compare`) routes through — enforced by the `cli-contract` AI-readiness
gate (D10.1). That work is real and already shipped; an external review that
proposed re-doing it (without having re-cloned `main` to check) was stale, not
a genuine gap.

Reading `service.py`/`api_types.py` end to end while verifying that review
did turn up two things ADR-037 explicitly left alone, each already flagged in
the code's own comments rather than newly discovered here:

### Gap 1 — `CompareRequest` cannot express everything `dump`/`compare --depth` supports

`api_types.py`'s own module docstring says as much: *"This module is Phase 1
of the G22 plan... Later phases extend `CompareRequest` with the depth (D5),
policy/severity (D4), and frontend (D8) fields the ADR sketches."* ADR-037's
own phases 3–6 did add `--depth`/`--max`/`--ast-frontend` to the **CLI**, but
`run_compare_request` (`service.py`) resolves depth/sources/build-info
*outside* `CompareRequest` — the request struct itself still has no `depth`,
`sources`, `build_info`, `dump_manifest`, or per-side `CompileContext`/
`frontend_context` (ADR-050's host/device AST-context selector) fields.
`service.py` says this directly, twice, in comments inside
`run_compare_request` itself:

> `# CompareRequest has no explicit --gcc-options equivalent today, so this
> is the only lever available here.`
>
> `# CompareRequest has no lower-level "parse only, don't classify" mode...`

A caller who wants `compare`'s full depth/sources/build-info/multi-TU-manifest
feature set from Python has to fall back to loose keyword arguments on lower-
level functions (`resolve_input`, `dump`'s own signature) — the exact kind of
per-front-end drift risk ADR-037 D1/D2 introduced `CompareRequest` to close
for the fields it *does* cover.

### Gap 2 — no single place owns "what's the current schema_version"

`docs/use/python-api.md` said snapshots carry `schema_version` `8`; the real,
current value (confirmed against `serialization.SCHEMA_VERSION` and
`docs/reference/snapshot-format.md`, the number's actual fact-owner page) is
17. This was a real, harmless-but-stale doc bug (fixed in the PR that
prompted this ADR — see `docs/use/python-api.md`'s link to
`reference/snapshot-format.md` instead of a hand-copied number). It is a
symptom, not the disease: `abicheck.schemas` already has *some* structure for
`compare`/`aggregate` report versions (packaged JSON Schema loaders,
`gen_python_api_reference.py`-style generation elsewhere), but snapshot/scan/
build-output/run-plan version numbers each live in their own module-level
constant with no shared lookup surface a docs generator (or an external
integrator) can query once for "what version does this abicheck build
currently emit for artifact X" — current-version discovery only; this
proposes no compatibility metadata or cross-version lookup (see D3 below).

### Gap 3 — `abi_compare` uses only half of the Tier-2 chokepoint

This ADR's first version claimed, as a Non-goal, that `abi_compare` "already
routes through `compare_snapshots`/the same Tier-2 layer as the CLI." That is
true, but incomplete in a way that changes the conclusion: reading
`mcp_server.py`'s `abi_compare` end to end (lines ~602–900 at the time of
writing) shows it calls `service.compare_snapshots` — the "classify two
already-resolved snapshots" half of Tier-2 — for the middle diffing step
only. Everything upstream and downstream of that one call is a second,
hand-rolled implementation, not the `run_compare_request(CompareRequest)`
chokepoint the CLI's `compare` command uses:

- input resolution: its own local `_resolve_input` helper, not
  `service.resolve_input`/a `CompareRequest`;
- policy/suppression loading: its own direct `PolicyFile.load`/
  `SuppressionList.load` calls, duplicating what `run_compare_request`
  already does internally from `CompareRequest` fields;
- severity/exit-code computation: its own `compute_exit_code`/legacy-verdict
  branch, separate from any equivalent CLI code path;
- `used_by`/`required_symbols` app-scoping (ADR-043): its own scoping logic,
  not shared with `appcompat.py`'s equivalent.

So `CompareRequest`/`run_compare_request` is not yet a real universal
chokepoint — `abi_compare` is a second, parallel compare engine that happens
to borrow one internal function from the first. This is exactly the
duplication a fuller external review (checked against this same code,
independently of the review that originally prompted D1–D3) identified as
its top-priority finding. See D4 below.

## Non-goals

- **Not** re-litigating anything ADR-037/G22 already shipped (CLI decorator
  families, the `--depth` ladder, command folding, `.abicheck.yml`
  rebalance) — that's done, per the ADR-037 status line.
- **Not** proposing to make ADR-049's `CompatibilityEvaluationConfig` (a
  separate, already-accepted, phased effort) authoritative anywhere. If D1's
  extended `CompareRequest` and ADR-049's evaluation config end up
  overlapping in a future field, that's a sequencing question for whoever
  implements D1, not a decision this ADR needs to make now.
- **Not** deprecating `run_compare`'s existing kwargs-shim calling
  convention, or breaking any current caller. Every change below is
  additive: a new optional field with a default, or a new wrapper type that
  existing tuple-returning functions keep returning unchanged.

## Decision

### D1. Extend `CompareRequest`/`InputSpec` to cover depth/sources/build evidence

Add, as additive fields with defaults (matching `api_types.py`'s own stated
convention — `field(default_factory=...)` where a frozen dataclass needs a
fresh empty default per instance):

```python
@dataclass(frozen=True)
class InputSpec:
    path: Path
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    version: str = ""
    pdb: Path | None = None
    debug_roots: tuple[Path, ...] = ()
    # New (D1):
    sources: Path | None = None
    build_info: Path | None = None
    dump_manifest: Path | None = None
    compile: CompileContext | None = None          # per-side override
    public_header_dirs: tuple[Path, ...] = ()

@dataclass(frozen=True)
class CompareRequest:
    old: InputSpec
    new: InputSpec
    # ... existing fields unchanged ...
    depth: str | None = None                        # AnalysisDepth spelling
    frontend_context: str = "host"                  # ADR-050 host|device
```

`run_compare_request` already resolves every one of these concepts
internally (`resolve_input`'s `public_headers`/`public_header_dirs`
parameters, the dump-manifest-aware snapshot path, `CompileContext`) — this
decision is about giving the *typed request* a way to carry them in, not
about building new resolution logic. The two `service.py` comments quoted
above become the acceptance test: once this lands, neither should still be
true.

**Sequencing note.** `dump_manifest`/multi-TU manifests are themselves
ADR-050 (G32) machinery, still being built out at the time of writing — D1
should track that ADR's `DumpManifest` type rather than inventing a second
shape.

**Two-resolution-path finding (see G33's Phase 2 status note).** The actual
CLI `compare` command does not resolve its inputs through
`service.run_compare_request` at all — it calls a separate, richer function,
`cli_resolve._resolve_compare_snapshots`, which layers per-side
`CompileContext`/`dump_manifest`/debug-root overrides and dependency-graph
following on top of what `run_compare_request` does today. So "extend
`CompareRequest` to cover everything CLI `compare` supports" is not simply a
matter of adding fields to an already-shared resolution path — D1's
implementer must first decide whether to migrate the CLI onto
`run_compare_request` (making it the genuine single resolution path) or to
extend `run_compare_request` to match `_resolve_compare_snapshots`'s
capabilities in parallel, keeping two implementations in sync by hand. This
ADR does not resolve that choice; it is scoped work for whoever picks up D1.

**As implemented — and the choice above, now decided as (b).** D1 landed in
PR #651: `InputSpec` gained `sources`/`build_info`/`dump_manifest`/`compile`/
`public_header_dirs` and `CompareRequest` gained `depth`/`frontend_context`,
with the resolution wiring split out into
`abicheck/service_compare_evidence.py`. Its stated acceptance test holds —
neither `service.py` comment this ADR quoted still exists.

The open choice is now answered: **option (b)** — `run_compare_request` was
extended in parallel, and the CLI `compare` command still resolves through
`cli_resolve._resolve_compare_snapshots`. This is recorded as a decision, not
as unfinished work, because the *capability* gap D1 set out to close is
closed (a Python or MCP caller no longer has to drop to loose kwargs to reach
`compare`'s depth/sources/build-info/manifest feature set), while option (a)
would rewrite the CLI's single most heavily-tested resolution path to gain
nothing a user can observe. What remains genuinely two-implementation is
narrower than the framing above suggests, and is listed explicitly so nobody
has to re-derive it: project-config `source.method` inference, the set-input
evidence-flag rejection guard, and the per-side AST-frontend override live
only in the CLI path. Migrating the CLI onto `run_compare_request` is a
separate, independently-reviewable change — it needs its own before/after
parity evidence over the CLI's full flag matrix, which is exactly the kind of
work that goes wrong when bundled into a different decision's PR.

### D2. Typed `Result` wrappers for the existing typed-request verbs

`run_compare`/`run_compare_request` return a bare
`tuple[DiffResult, AbiSnapshot, AbiSnapshot]` today. Introduce a
`CompareResult` dataclass that wraps exactly that tuple's three fields plus
nothing else initially (a pure, zero-behavior-change rename of the return
shape):

```python
@dataclass(frozen=True)
class CompareResult:
    diff: DiffResult
    old_snapshot: AbiSnapshot
    new_snapshot: AbiSnapshot
```

**Decided: a new parallel function, `run_compare_request_v2(request) ->
CompareResult`.** The tuple-returning `run_compare_request` stays exactly as
it is — no existing caller's code changes. `run_compare_request_v2` is not
Click-decorated and carries no CLI-facing surface of its own; it exists
purely as a typed Python/service entry point for whoever picks up D1, so the
naming isn't user-facing API surface a caller types day to day the way
`run_scan`/`run_compare_request` are — it's an internal migration seam. The
point of the wrapper is purely to give a *future* field (resolved depth, an
`EvaluationReceipt` once ADR-049 wires one up, a coverage summary) somewhere
to land without a second tuple-shape break down the line — the same reasoning
ADR-035 already applied to `ScanRequest`/`ScanResult`, generalized to
`compare`. Once D4 (and any other typed-result migration) is complete and
`run_compare_request`'s tuple-returning callers are gone, a follow-up ADR can
decide whether to rename `run_compare_request_v2` to something permanent —
that rename is explicitly out of scope here.

**As implemented — two deliberate departures.** `CompareResult` lives in
`api_types.py` next to `CompareRequest`, not in `service.py` as the file
sketch below says: that module is already "typed request/response structs",
imports nothing from the service layer at runtime, and keeping the pair
together lets a caller annotate a result without importing `service`'s much
heavier graph. And it carries a **fourth** field, `suppression`, rather than
the "exactly the tuple's three fields plus nothing else" this decision
proposed. That field is not speculative growth — it is what D4 turned out to
need: `run_compare_request` resolves the suppression list internally, but
`appcompat.scope_diff_to_app(..., suppression=...)` needs the resolved object
*after* classification, and `DiffResult` carries the resolved policy file but
not the suppression list. Without it, the MCP server would have had to keep a
`SuppressionList.load` call purely to re-derive a value the service had
already loaded — the exact duplication D4 exists to remove, reintroduced by
the shape chosen to enable removing it. `run_compare_request` was not
duplicated to add the wrapper: it is now a one-line tuple view
(`CompareResult.as_tuple()`) over the same implementation, since two copies
of the resolution logic is the failure this whole ADR is about.

### D3. A minimal schema-version registry

Add a small `abicheck.schemas` (or extend the existing one, if `compare`/
`aggregate` schema loading already lives there) function-level registry:

```python
from abicheck import schemas
schemas.current("snapshot")   # -> current SCHEMA_VERSION (int)
schemas.current("compare")    # -> current REPORT_SCHEMA_VERSION (str)
schemas.current("scan")       # -> current SCAN_SCHEMA_VERSION (str)
```

(the exact values drift as each artifact's own constant bumps — deliberately
not hand-copied here, since a literal number in this ADR would go stale the
same way `docs/use/python-api.md`'s did; see each module's own docstring for
the current value and its bump history)

backed by the *existing* constants (`serialization.SCHEMA_VERSION`,
`checker_types`/wherever `compare`'s `report_schema_version` lives,
`service_scan`'s `scan_schema_version`, `build_output.py`'s
`BUILD_OUTPUT_MANIFEST_NAME`-adjacent version, `run_plan.py`'s
`RUN_PLAN_SCHEMA`) — this is a **read-only lookup facade**, not a new
versioning scheme, and does not change any of those constants' current
values or bump policy. `docs/use/python-api.md`'s dead `schema_version 8`
claim is exactly the failure mode this makes preventable: a docs generator
(or a new `gen_python_api_reference.py`-style script) can pull every current
version number from one place instead of a human hand-copying one, which is
how it went stale in the first place — closing the *doc-generator* half of
that gap (wiring an actual generator to call `schemas.current()` instead of
hand-copying) is separate follow-up work, not implied by the registry
existing.

**As implemented, including that follow-up.** `schemas.current(name)` covers
all six artifact names, backed by each artifact's own constant through a
function-local import (a module-level one is a real cycle: `run-plan` needs
`buildsource.run_plan.RUN_PLAN_SCHEMA`, whose module already imports
`abicheck.schemas` transitively).

The consumer half is closed in two layers, because a review round
(chatgpt-codex-connector on PR #665) correctly pointed out that only one of
them actually satisfies this repo's own rule:

1. **Delete the copy where the number is incidental.** `docs/AGENTS.md`'s
   rule is "don't hand-copy a version that has a fact owner — link to it."
   Three sites were quoting a version whose *value* taught the reader
   nothing: `docs/reference/check-target.md`'s "the starting shape is the
   compare-report shape (`report_schema_version: "…"`)",
   `docs/use/output-formats.md`'s `# e.g. "1.0"` import comment, and — the
   one that proves the point — that same page's claim that a snapshot's
   `schema_version` "is currently `8`". That is *the original D3 bug*, alive
   on a second page: G33 Phase 0 fixed the identical sentence in
   `docs/use/python-api.md` and nobody noticed the duplicate. All three now
   link to the owning page instead of restating it.
2. **Pin what must stay literal.** A JSON snippet showing real output can't
   carry a link, and a fabricated version in it would misinform a reader
   matching it against their own file. Those sites keep a real value, gated
   by `scripts/check_ai_readiness.py`'s `doc-count-sync` check, which now
   reads its expected values from `schemas.current()`. This is the
   already-established pattern for `docs/reference/snapshot-format.md` (the
   snapshot version's own fact owner), not a new exception invented here.

The distinction between the two is *who owns the fact*, not how much effort
each takes: layer 1 is for pages that were holding a second copy, layer 2 is
for the owner page and for verbatim output samples. Generating these pages
was considered and rejected — a generator would have to own a whole page of
hand-written prose to own one number.

Both mechanisms earned their place immediately. The check caught two live
stale values on its first run (`report_schema_version` `"1.0"` and `"2.13"`
against a real `2.26`), and the review that followed caught two *more* it
structurally could not: a copy nobody had anchored (`snapshot-format.md`'s
snapshot-vs-report comparison table, now anchored) and the `schema_version 8`
sentence above. Pinning only catches the sites you thought to pin — which is
the honest limitation of layer 2, and the reason layer 1 exists.

### D4. Route `abi_compare` through `run_compare_request`

Once D1 lands (so `CompareRequest`/`InputSpec` can express everything
`abi_compare`'s current parameters need), rewrite `mcp_server.py`'s
`abi_compare` to build one `CompareRequest` and call `run_compare_request`
for the resolve+classify step, instead of its own `_resolve_input` +
`compare_snapshots` pair:

```python
request = CompareRequest(old=..., new=..., ...)
diff_result, old_snap, new_snap = run_compare_request(request)
```

Concretely, in terms of the current tool's parameters:

- `old_input`/`new_input`/`old_headers`/`new_headers`/`headers`/
  `include_dirs`/`language` map onto `InputSpec.path`/`headers`/`includes`,
  same as the CLI's own `resolve_input` call;
- `policy`/`policy_file`/`suppression_file` map onto `CompareRequest` fields
  once D1 (or a small preceding slice) adds them — `CompareRequest` has no
  suppression/policy-file field today, so this is new surface for
  `CompareRequest` itself, not something D1 already covers; scope it before
  starting D4, don't assume it falls out of D1 for free.
  **Correction:** this bullet was wrong when written. `CompareRequest` has
  carried `policy`, `policy_file_path`, and `suppress` since PR #611 (G30/
  ADR-047), well before this ADR was drafted, so D4 needed no preceding
  slice for them. Recorded rather than deleted because "check the struct
  instead of trusting a neighbouring ADR's summary of it" is the same lesson
  that produced this document's Gap 3 correction.

What D4 deliberately does **not** try to fold into `CompareRequest`/
`CompareResult`:

- `used_by`/`required_symbols` app-scoping (ADR-043) and `diagnostic_comparison`
  (ADR-050 D2) are cross-cutting concerns applied *after* a `CompareResult`
  exists, not part of resolving/classifying the comparison itself — they stay
  a thin wrapper layer in `mcp_server.py` that consumes a `CompareResult`,
  the same way `appcompat.py` would if it were rewritten today. Folding them
  into the request/result types themselves would re-introduce the "everything
  is one growing struct" problem D1/D2 exist to avoid.
- `severity_*`/exit-code computation stays MCP-specific glue over
  `CompareResult.diff.changes` — exit-code *schemes* (legacy vs.
  severity-aware) are a CLI/MCP presentation concern, not part of the typed
  result itself.

**Acceptance test:** `mcp_server.py`'s `abi_compare` has no local
`_resolve_input` call and no direct `PolicyFile.load`/`SuppressionList.load`
calls of its own — every one of those goes through `run_compare_request`.
A parity test (comparing `abi_compare`'s output for a fixed input against
the CLI `compare` command's output for the equivalent flags) is the
regression guard against silent behavior drift during the rewrite.

**As implemented.** `abi_compare` builds one `CompareRequest` and calls
`run_compare_request_v2`; the module no longer imports `compare_snapshots` at
all. Both acceptance tests exist in `tests/test_mcp_server_unit.py`
(`TestAbiCompareCliParity`): the source-level gate is asserted directly
(comments stripped first, since the function now *documents* what it stopped
calling), and the parity tests compare `abi_compare`'s rendered report and
exit code against the CLI's over a flag matrix — defaults, policy profile,
policy file, suppression file, `--show-only`, `--report-mode`, and
severity-aware gating. Three findings from doing it:

1. **One MCP guard had to become request surface, not stay wrapper glue.**
   `mcp_server._resolve_input` pinned `follow_linker_scripts=False`, because
   the tool size-checks the *caller-supplied* path and a GNU ld script's
   `INPUT()` target would never pass through that check. Routing through the
   service would have silently dropped that, so `InputSpec` gained a
   `follow_linker_scripts` field (default `True`, matching `resolve_input`'s
   own default, so no pre-existing caller changes). The *path-containment*
   and *file-size* guards stayed MCP-local, applied before the request is
   built: those constrain untrusted input, which is genuinely the front end's
   job, unlike resolve/load/classify.
2. **The report is byte-identical to the CLI's**, modulo two keys the CLI's
   renderer adds and this tool has never emitted (`old_evidence_depth`/
   `new_evidence_depth`). Verified as a before/after diff of the tool's own
   output across the flag matrix, not only against the CLI, so the rewrite is
   shown not to have moved anything.
3. **One intentional behaviour change:** an unsupported `language` (e.g.
   `"rust"`) is now a structured validation error rather than a value passed
   quietly down the resolver. That is `CompareRequest.validate()` doing what
   the CLI's `--lang` choice already did — the front-end parity ADR-037 D9
   asks for — so it is recorded here as intended, not tolerated as fallout.

The parity tests deliberately do **not** cover `used_by`/`required_symbols`,
consistent with the next paragraph: that scoping is applied after a result
exists and has no CLI-comparable rendering path here. The sibling
`TestAbiCompare` scoping tests continue to cover it.

## Files & surfaces (as landed)

| Module | Change |
|---|---|
| `abicheck/api_types.py` | `InputSpec.sources`/`build_info`/`dump_manifest`/`compile`/`public_header_dirs` + `CompareRequest.depth`/`frontend_context` (D1); `InputSpec.follow_linker_scripts` (D4); `CompareResult` (D2 — here, not `service.py`, see its "As implemented" note) |
| `abicheck/service_compare_evidence.py` | D1's resolution wiring, split out of `service.py` |
| `abicheck/service.py` | `run_compare_request_v2` owns the implementation and returns `CompareResult`; `run_compare_request` is its tuple view (D2); per-side `follow_linker_scripts` forwarding (D4) |
| `abicheck/schemas/__init__.py` | `current(name)` registry (D3) |
| `scripts/check_ai_readiness.py` | `doc-count-sync` reads snapshot/compare versions from `schemas.current()` and pins the pages quoting them (D3's consumer half) |
| `abicheck/mcp_server.py` | `abi_compare` builds a `CompareRequest` and calls `run_compare_request_v2`; no local resolve, no policy/suppression load, no `compare_snapshots` import (D4) |
| `docs/reference/python-api-reference.md`, `docs/reference/mcp-tools-reference.md` | Regenerated (generated files) |
| `tests/test_api_types.py` | Field defaults for D1/D4; `TestCompareResult` (D2) |
| `tests/test_service_unit.py` | `TestCompareRequestAdr055Evidence` (D1); `TestRunCompareRequestV2` (D2, incl. per-side `follow_linker_scripts`) |
| `tests/test_mcp_server_unit.py` | `TestAbiCompareCliParity`: the source-level D4 gate plus CLI-parity over a flag matrix |
| `tests/test_mcp_server_coverage.py` | `TestAbiCompareTool` repointed to the service-layer names `abi_compare` now reaches |

## Alternatives considered

- **Do nothing; keep depth/sources/build-info as loose kwargs on
  lower-level functions.** Rejected for the same reason ADR-037 rejected it
  for the fields it already covers: a caller assembling a request by hand
  can drift from the CLI's own resolution order, and a new keyword has
  nowhere principled to land except another `run_compare` signature growth.
- **Fold this into ADR-049 instead of a new ADR.** Rejected: ADR-049 is
  about a different axis (contract relevance / compatibility *policy*
  configuration), already accepted and mid-rollout on its own schedule. This
  decision is about request/response *shape* completeness, orthogonal to
  what policy is applied once a comparison runs. Keeping them separate avoids
  the "two parallel policy engines" risk ADR-049's own rollout section
  already warns about for its own scope.
- **Skip D3 (schema registry) as too small to need an ADR.** Considered, but
  keeping it in the same document as D1/D2 records the concrete bug (`docs/
  use/python-api.md`'s stale `8`) that motivated it, so a future reader does
  not have to rediscover why a "just add a lookup function" change needed a
  decision record at all — mostly it's here for traceability, not because
  D3 alone is architecturally significant.
- **D2's `CompareResult` entry point: a `return_result: bool = False` flag on
  the existing `run_compare_request`.** Rejected: Python can't cleanly type a
  return shape that depends on a boolean *value* without `@overload(Literal[
  True]/Literal[False])` boilerplate at every call site, and a boolean flag
  that switches a function's return shape is a mild API smell in its own
  right — same class of thing this repo's own review process tends to flag
  elsewhere.
- **D2's `CompareResult` entry point: a bare `compare()` function.** Rejected
  in favor of `run_compare_request_v2` after checking this ADR's own claimed
  precedent (ADR-035's `ScanRequest`/`ScanResult`) against the actual code:
  the real function there is `run_scan(req: ScanRequest) -> ScanResult`, not
  a bare `scan()` — it followed the existing `run_<verb>` naming family from
  day one. A bare `compare()` would be a naming departure from that
  precedent, not a match to it.

## Rollout

Same phased-PR discipline ADR-037/G22 used: D3 (schema registry) was
independently shippable and lowest-risk — it has landed
(`abicheck/schemas/__init__.py`'s `current()`). D1 is the larger
slice (needs `DumpManifest`/`CompileContext` per-side wiring in
`run_compare_request`, plus the policy/suppression fields D4 needs) and
should land behind its own parity test (`test_service_unit.py`, above)
before D2's wrapper type is worth adding. D4 (the `abi_compare` rewrite) is
deliberately last and gated on D1: rewriting the MCP tool before
`CompareRequest` can express its full parameter set would either lose
capability or force D4 to invent its own ad hoc `CompareRequest` extension
outside this ADR's decision. See
[G33](../plans/g33-typed-api-and-mcp-convergence.md) for the implementation
plan and current per-phase status.

That order held: D3 first, then D1 (PR #651), then D2 and D4 together — D4's
need for the resolved suppression list is what gave `CompareResult` its
fourth field, so splitting them would have meant landing a wrapper with no
consumer and then changing its shape one PR later.

## Implementation notes

**How this ADR's own status went stale, since it is the second time.** D1
landed inside PR #651 ("close dump/compare dependency-scoping asymmetry"),
whose title says nothing about ADR-055 — so neither this document's status
line nor G33's Phase 2 note was touched, and both kept saying "D1 not
started" through several later PRs. The same shape as the Gap 3 correction
above: a claim about the code that stayed true in the document long after it
stopped being true in the code. The cheap mitigation is the one already used
elsewhere in this repo — decisions carry an executable gate, not just a
prose status. D1's and D4's both now do (the `service.py` comment-absence
check by way of `TestCompareRequestAdr055Evidence`, and D4's source-level
gate in `TestAbiCompareCliParity`).

**Still open after this ADR, deliberately.** Two items, each needing its own
scoped change rather than an extension of this one:

- The CLI's separate `_resolve_compare_snapshots` path (option (b) above) —
  see D1's "As implemented" note for the exact three capabilities that
  remain CLI-only.
- G33's Phase 5 (`abi_dump`/`abi_scan` reaching the same depth/sources/
  build-info/manifest parity `abi_compare` now has). That plan gates it on
  Phase 4, which this closes, so it is unblocked — but it is a change to two
  other tools' parameter surfaces, not part of this decision.
