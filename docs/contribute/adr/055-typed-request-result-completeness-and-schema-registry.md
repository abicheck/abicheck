# ADR-055: Typed Request/Result Completeness and a Schema-Version Registry

**Date:** 2026-07-27
**Status:** Proposed — not implemented. Written up after an external
API-layer review of `abicheck` (CLI/Python/MCP/Actions/schemas) turned out to
be largely stale against current `main` — G22/ADR-037 already landed the CLI
consolidation and MCP↔Tier-2 chokepoint the review flagged as missing — but
verifying that review's claims against the actual code (`service.py`,
`api_types.py`) surfaced two real, narrower, already-self-documented gaps.
This ADR proposes closing them; it does not implement anything itself.
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
integrator) can query once for "what versions does this abicheck build
support, and are they compatible."

## Non-goals

- **Not** re-litigating anything ADR-037/G22 already shipped (CLI decorator
  families, the `--depth` ladder, command folding, `.abicheck.yml`
  rebalance) — that's done, per the ADR-037 status line.
- **Not** touching the MCP server's tool surface. `abi_compare` already
  routes through `compare_snapshots`/the same Tier-2 layer as the CLI; a
  wider MCP tool surface (e.g. exposing the new `CompareRequest` fields as
  MCP params) is a natural follow-on once D1 below lands, but is out of
  scope for this decision.
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
schemas.current("snapshot")   # -> 17
schemas.current("compare")    # -> "2.22"
schemas.current("scan")       # -> "1.3"
```

backed by the *existing* constants (`serialization.SCHEMA_VERSION`,
`checker_types`/wherever `compare`'s `report_schema_version` lives,
`service_scan`'s `scan_schema_version`, `build_output.py`'s
`BUILD_OUTPUT_MANIFEST_NAME`-adjacent version, `run_plan.py`'s
`RUN_PLAN_SCHEMA`) — this is a **read-only lookup facade**, not a new
versioning scheme, and does not change any of those constants' current
values or bump policy. `docs/use/python-api.md`'s dead `schema_version 8`
claim is exactly the failure mode this closes: a docs generator (or a new
`gen_python_api_reference.py`-style script) can pull every current version
number from one place instead of a human hand-copying one, which is how it
went stale in the first place.

## Files & surfaces (sketch, for whoever picks this up)

| Module | Change |
|---|---|
| `abicheck/api_types.py` | New `InputSpec`/`CompareRequest` fields (D1) |
| `abicheck/service.py` | `run_compare_request` reads the new fields instead of falling back to `resolve_input` kwargs; new `CompareResult` (D2) |
| `abicheck/schemas.py` *(new or extended)* | `current(name)` registry (D3) |
| `docs/reference/python-api-reference.md` | Regenerate after D1/D2 (generated file) |
| `tests/test_api_types.py` | New field defaults/round-trip tests |
| `tests/test_service_unit.py` | Parity test: a `CompareRequest` built with the new fields set produces the same `DiffResult` as the equivalent `resolve_input`/kwargs call today |

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

Same phased-PR discipline ADR-037/G22 used: D3 (schema registry) is
independently shippable and lowest-risk — land it first. D1 is the larger
slice (needs `DumpManifest`/`CompileContext` per-side wiring in
`run_compare_request`) and should land behind its own parity test
(`test_service_unit.py`, above) before D2's wrapper type is worth adding.
