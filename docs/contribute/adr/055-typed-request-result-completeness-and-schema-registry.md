# ADR-055: Typed Request/Result Completeness and a Schema-Version Registry

**Date:** 2026-07-27 (D1–D3); Gap 3/D4 added 2026-07-27 after a second,
more detailed external review of the same subject was checked line-by-line
against `mcp_server.py` (see "Amendment" note below Gap 2)
**Status:** Proposed — D3 (schema-version registry) implemented
(`abicheck/schemas/__init__.py`'s `current()`, see
[G33](../plans/g33-typed-api-and-mcp-convergence.md) Phase 1); D1/D2/D4 not
started. Written up after an external API-layer review of `abicheck`
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
Non-goal. This ADR proposes closing all four; it does not implement anything
itself beyond D3.
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

`run_compare_request` gains a `return_result: bool = False`-style opt-in (or,
more in keeping with ADR-037's own precedent of a parallel typed entry point
rather than a flag, a new `run_compare_request_v2`/`compare()` function
returning `CompareResult` while the tuple-returning original stays exactly as
it is) — either way, no existing caller's code changes. The point of the
wrapper is purely to give a *future* field (resolved depth, an
`EvaluationReceipt` once ADR-049 wires one up, a coverage summary) somewhere
to land without a second tuple-shape break down the line — the same reasoning
ADR-035 already applied to `ScanRequest`/`ScanResult`, generalized to
`compare`.

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

## Files & surfaces (sketch, for whoever picks this up)

| Module | Change |
|---|---|
| `abicheck/api_types.py` | New `InputSpec`/`CompareRequest` fields (D1); policy/suppression fields for D4 |
| `abicheck/service.py` | `run_compare_request` reads the new fields instead of falling back to `resolve_input` kwargs; new `CompareResult` (D2) |
| `abicheck/schemas/__init__.py` | `current(name)` registry (D3) — **implemented** |
| `abicheck/mcp_server.py` | `abi_compare` rewritten to build a `CompareRequest` and call `run_compare_request` (D4) |
| `docs/reference/python-api-reference.md` | Regenerate after D1/D2 (generated file) |
| `docs/reference/mcp-tools-reference.md` | Regenerate after D4 (generated file) |
| `tests/test_api_types.py` | New field defaults/round-trip tests |
| `tests/test_service_unit.py` | Parity test: a `CompareRequest` built with the new fields set produces the same `DiffResult` as the equivalent `resolve_input`/kwargs call today |
| `tests/test_mcp_server_unit.py` | Parity test: `abi_compare`'s output for a fixed input matches the CLI `compare` command's output for the equivalent flags, before and after the D4 rewrite |

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
