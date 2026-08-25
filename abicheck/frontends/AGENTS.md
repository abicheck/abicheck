# AGENTS.md — `abicheck/frontends/`

## Purpose

This package owns presentation-only translation between a user-facing
surface (the Click CLI, the ABICC-compatible CLI, the typed Python API) and
the workflow/report layers that do the real work. ADR-061 Phase 4 names it
the destination for command input translation (`frontends/cli/commands`)
and reusable Click-only option declaration (`frontends/cli/options`), with
the eventual goal of reducing root `cli.py`/`service.py` to thin
registration/delegation facades under 150 lines each.

## Permitted imports

Frontend code may import `abicheck.model`, `abicheck.workflows`, and
`abicheck.report`. It must not import a compare/policy/extract module
directly, and it must not contain an extraction routine, a comparison
detector, or a compatibility-policy algorithm — those belong to their own
responsibility package, reached only through a workflow's typed result.

## Canonical entry points

Implementation status: this package holds exactly one module so far —
`cli.cli.options.secondary_output` (moved from the flat
`abicheck/cli_secondary_output.py`), the shared `--write FORMAT=PATH` Click
option factory and its coherence validator. It qualified for an
immediate, same-session move because it has zero first-party imports, the
same property that made `artifact_plan.py` a safe Phase 3 vertical slice: a
physical relocation with no first-party imports cannot change any
import-cycle or dependency-direction fact about the rest of the codebase.

Everything else Phase 4 names — the bulk of `cli_options.py` and its
mutually-interdependent sibling option modules (`cli_params.py`,
`cli_profiles.py`, `cli_options_contract.py`, `cli_contract_options.py`,
`cli_help.py`), command input translation for `dump`/`compare`/`scan`, and
reducing `cli.py`/`service.py` themselves — is **not** attempted yet. Two
reasons, both structural rather than a scope choice:

1. **The option-declaration cluster is not leaf-shaped.** Unlike
   `secondary_output.py`, `cli_options.py` (near its own 2000-line hard cap)
   and its siblings import each other and are imported by essentially every
   `cli_*.py` command module — moving that whole ~3,800-line cluster in one
   pass is a high-blast-radius change to Click decorator stacking order
   across the entire CLI surface, not a small, independently-verifiable
   vertical slice. It needs its own dedicated pass, splitting the cluster's
   internal dependency graph first.
2. **`cli.py`/`service.py` cannot shrink until `workflows/` actually owns
   the operations they currently implement inline.** `service.py`'s
   `resolve_input`/`_run_dump_uncached`/`compare_snapshots` (hundreds of
   lines each) *are* the current dump/compare implementation, not adapters
   over an already-existing workflow object Phase 4 could point them at
   instead — moving that logic into `workflows/` is Phase 3's job. Phase 3
   itself has only relocated one dependency-free contract type
   (`ResolvedArtifactPlan`) so far; the real per-artifact resolve/execute
   pipeline does not exist yet (see `workflows/AGENTS.md`'s own status
   note and ADR-061's Phase 3 section). Thinning `cli.py`/`service.py`
   before that pipeline exists would mean either leaving the real logic in
   place under a thin wrapper (achieving nothing) or duplicating it into a
   new home with no shared implementation to delegate to (a second copy
   that can drift, exactly what this migration exists to prevent).

Documented in ADR-061's Phase 4 status note.

## Tests

Frontend unit tests migrate alongside their implementation. The
`secondary_output` leaf's own tests remain wherever the CLI commands that
apply it are tested (`tests/test_cli_scan.py`, `tests/test_cli_contract.py`
and siblings) until a dedicated `tests/unit/frontends/` tree is warranted.

## Prohibited responsibilities

Do not declare a compare/scan/dump algorithm, a suppression or severity
rule, or a report-schema field here. A frontend module translates already-
validated user input into a workflow request and translates a workflow
result into a process response; it does not compute either side's content.

## Change checklist

Before adding to this package, confirm the module being moved either has no
first-party imports (safe to relocate immediately, like `secondary_output`)
or that its real dependents already live in `workflows`/`report` (safe to
delegate to). Moving a module whose current implementation *is* the real
algorithm, with nothing in `workflows`/`report` yet to delegate to, is not
this package's job — that is the responsibility-package migration itself,
not a frontend translation.

## Public compatibility

`cli.py`, `compat/cli.py`, and `service.py` remain the documented public
entry points during migration. A supported CLI flag or Python API function
may delegate to a `frontends` module, but this package must never import
back through those root facades.
