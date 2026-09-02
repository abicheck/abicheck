# AGENTS.md — `abicheck/policy/`

## Purpose

This package owns deciding relevance, suppression, classification,
severity, and gate (exit-code) effect for an already-identified change. It
answers "does this change matter, and how much" — never "are these two
declarations the same entity" (that is `compare/`) and never "how is it
reported" (that is `report/`).

Most of that behavior still lives in flat root modules that
`architecture/modules.yaml` lists as this layer's `legacy_paths`. Those stay
where they are until a behavior-preserving vertical slice moves them;
`architecture/debt.yaml` holds an oversized one (`analysis_assurance.py`) at
`no_growth` with an explicit rationale — its own debt entry states it
"cannot move safely without a behavior-preserving vertical slice", and
`scripts/check_architecture.py`'s `debt-exemption` gate mechanically
enforces that a debt-tracked file's path cannot change within the same PR
that would also need to renew its baseline, so it stays flat until a PR
whose only job is that move does so deliberately.

Three further legacy-path modules are classified nowhere by design, not by
oversight: `checker_policy.py`, `contract_gating.py`, and `reclassify.py`
are each documented, in their own module docstrings, as leaf modules that
both `compare` (`checker_types.DiffResult`, `checker.py`) and `policy`
(`severity.py`, this package) depend on — the "pull the shared logic out to
a leaf both sides can depend on" pattern ADR-061 names for exactly this
class of cross-layer dependency. Giving one of them a single layer would be
wrong, not merely premature, so `modules.yaml`'s `public_root_surfaces`
list — the ADR's own named escape hatch for behavior with no single clean
owner (see `docs/contribute/adr/061-responsibility-package-architecture.md`
D3) — carries them instead of a `legacy_paths` entry.

## Permitted imports

Per ADR-061, `policy/` may depend only on `model` and `compare`, plus the
public root surfaces. It may not import extraction, workflow, report, or
frontend modules — a policy module that touches a binary, a build system,
or a CLI flag directly is in the wrong layer.
`scripts/check_architecture.py` enforces this.

## Modules

- `severity.py` — severity/gate configuration and the change-gate exit-code
  computation (`compute_exit_code`, `compute_gate_decision`,
  `SeverityConfig`, `SeverityLevel`, ...). Moved here from
  `abicheck/severity.py` (ADR-061 physical migration); the flat path is now
  a thin, lazily-resolving back-compat shim — see its own module docstring.
- `exit_decision.py` — `ExitDecision`/`ExitReason`, the orthogonal-axis
  exit-code fold shared by `compare`/`scan`. Moved from
  `abicheck/exit_decision.py`; same shim treatment.
- `contract_coverage_exit.py` — ADR-049 Phase 7's contract-coverage exit
  contribution. Moved from `abicheck/contract_coverage_exit.py`; same shim
  treatment.
- `gate_decision.py` — ADR-061 Phase 2's `gate_decision_for_result`: the one
  call site that turns a `DiffResult` + optional `SeverityConfig` into a
  `GateDecision`, so `reporter.py`/`sarif.py`/`html_report.py` each call one
  shared function instead of independently re-assembling
  `compute_gate_decision`'s arguments from the result. New module, not a
  moved one — no flat shim exists or is needed.
- `public_surface.py` / `public_surface_closure.py` — ADR-063 Phase 3 D5's
  public-surface relevance query, split across two sibling leaf modules
  purely to keep each under the 800-line new-file cap (mechanical
  extraction, not a redesign): `public_surface.py` owns the `PublicSurface`
  result dataclass and the declaration/type indexing (`_index_surface_types`
  and its own origin/ambiguity bookkeeping); `public_surface_closure.py`
  owns the actual closure-walk algorithm (`_seed_public_roots`/
  `_walk_type_closure`/`_walk_exact_type_closure` and siblings) and the real
  entry point, `resolve_public_surface()` — reads *what a declaration/type/
  typedef references* from `compare/surface_graph.py`'s
  `referenced_identifiers_by_node()`, a pure function of the snapshot's own
  current declarations computed fresh on every call, not the independent
  regex re-parse `surface.py`'s pre-migration implementation used to be, and
  (after two further Codex review rounds — see this module's own docstring
  and `docs/contribute/known-gaps.md`'s ADR-063 Phase 3 entry) deliberately
  **not** `AbiSnapshot.surface_graph`'s own persisted `GraphNode.attrs`
  either: a stale or adversarial persisted fact could otherwise silently
  outrank a fresh, correct recomputation through the graph's own
  cross-producer evidence-merge precedence. Both are leaf modules with respect to
  `surface.py`/`export_surface.py`: neither imports either of those, so both
  (and `export_surface.py`'s own export-domain closure, which reuses
  `_walk_type_closure` verbatim) can depend on this package without a cycle.
  `surface.py` re-exports `PublicSurface` for its existing callers.
  `resolve_public_surface`/`PublicSurfaceQuery` historically lived directly
  in `public_surface.py` before this split; a lazy `__getattr__` shim at
  the bottom of that module (the pattern below) resolves them via
  `importlib.import_module` from their new homes, so `from abicheck.policy.
  public_surface import resolve_public_surface, PublicSurfaceQuery` keeps
  working (Codex review, PR #979) without a static import re-introducing
  the cycle the split exists to avoid.
- `public_surface_query.py` — `PublicSurfaceQuery`, the orchestrator on top
  of the two modules above: the only place in this package that depends on
  *both* `public_surface_closure.py` (the public-domain query) and
  `export_surface.py` (the `contract=exports` domain's own root-seeding) at
  once. Kept separate from `public_surface_closure.py` specifically because
  that dependency pair would otherwise close a real import cycle
  (`public_surface_closure.py -> export_surface.py -> public_surface.py` and
  siblings) — see this module's own docstring for the full reasoning.

- `selectors.py` / `selectors_namespace_glob.py` — ADR-063 D10
  (implementation plan Phase 9): the shared selector-matching primitive
  (`SelectorSet`) behind `suppression.py`'s `Suppression` and
  `reclassify.py`'s `ReclassifyRule` — one grammar (`symbol`/
  `symbol_pattern`/`type_pattern`/`member_name`/`namespace`/
  `entity_namespace`/`cause_namespace`/`source_location`/`change_kind`/
  `binding`/`finding_id`/`expires`) instead of two independently-maintained
  copies. **Genuinely dependency-free leaves** — zero import of
  `checker_types.py`/`suppression.py`/`reclassify.py`/`policy_file.py`/
  `finding_identity.py`, checked directly by
  `scripts/check_architecture.py` (narrower than the general
  `policy -> compare` layer edge, which would otherwise permit
  `finding_identity.py`) — which is what lets `reclassify.py` import
  `selectors.py` **statically**: before this phase it built a `Suppression`
  instance purely for its selector grammar, resolved via a runtime
  `importlib.import_module` call to dodge the cycle a static import would
  have closed (`policy_file -> reclassify -> suppression -> checker_types
  -> policy_file`); now neither module needs to import the other.
  `selectors_namespace_glob.py` holds the fnmatch/regex namespace-glob
  compilation machinery, split out purely to keep `selectors.py` itself
  under the 800-line cap below (mechanical extraction, same pattern as
  `public_surface.py`/`public_surface_closure.py` above).
- `depth_projection.py` — ADR-063 Phase 8 follow-up: the `--depth`
  *ceiling* half `docs/contribute/known-gaps.md`'s "floor for live
  extraction, not a ceiling for a pre-built snapshot" entry named but did
  not attempt. `project_snapshot_to_depth()` is a pure gating decision
  ("which facts may a comparison at this depth see"), mirrored from the
  one prior validated reference implementation of the same idea —
  `scripts/check_tier_accuracy.py`'s `project()` — generalized from that
  script's synthetic corpus onto a real `AbiSnapshot` and its own public
  `binary`/`headers`/`build`/`source` ladder (`evidence_depth.DEPTH_RANK`,
  a `model`-layer leaf, not `compare`). `service_compare_pipeline.
  classify_compare_pair` applies it as a view over what gets classified,
  right after `workflows.artifact.execute.enforce_requested_depth`
  confirms the floor — the two functions are deliberately kept separate
  (floor vs. ceiling), not merged into one.

## Conventions

- Every module starts with `from __future__ import annotations`.
- The 800-line production cap applies (`scripts/check_architecture.py`).
- A back-compat shim left at a module's old flat path re-exports the
  moved module's full public surface explicitly (not `import *`), so
  `abicheck.<name>.<attr>` attribute access keeps working identically to
  before the move, not only `from abicheck.<name> import <attr>`.
