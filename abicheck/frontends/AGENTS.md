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

Implementation status: the first tenant was
`cli/options/secondary_output.py` (moved from the flat
`abicheck/cli_secondary_output.py`), the shared `--write FORMAT=PATH` Click
option factory and its coherence validator. It qualified for an
immediate, same-session move because it has zero first-party imports, the
same property that made `artifact_plan.py` a safe Phase 3 vertical slice: a
physical relocation with no first-party imports cannot change any
import-cycle or dependency-direction fact about the rest of the codebase.

Four more modules joined it on the same criterion — `options/profiles.py`,
`options/contract.py`, `options/inventory.py` and `help.py`. The blocker note
this section used to carry, that the option cluster's modules "import each
other", did not survive an AST scan: the cluster is a **star**, with
`cli_options.py` as the hub and its five siblings holding zero intra-cluster
imports.

ADR-061 Phase 4 then moved the real weight here. `cli.py` went from **1959
lines to 128** and is now a registration facade: the Click root group, its
`--version`/SIGTERM wiring, the tail-of-module registration imports, and one
lazy compatibility shim. Everything else lives in this package:

| Module | What it owns |
|---|---|
| `cli/commands/dump.py` | `dump`'s ~30 Click parameters → one `DumpRequest`, resolved once and consumed by both the dry run and the real run |
| `cli/commands/compare.py` | The single-pair compare, the release fan-out, and inline build-source embedding |
| `cli/runtime.py` | Verbosity, output writing, provenance stamping, and the process-exit decision |

**`abicheck.cli` re-exports nothing. Import from the owner above, and patch
the owner.** It used to: a `cli/moved.py` table mapped ~80 private helpers to
the module that now owns each, a lazy `__getattr__` on `cli.py` resolved them
at access time, and a custom module class rejected assignment to any of them.
That last part was not decoration — a `monkeypatch.setattr` against a
lazily-resolved alias froze it, so every later caller read a stale function
and every later patch of the real owner was silently ignored, which had
already surfaced as an order-dependent CI failure two test files away from
its cause. All three were removed once every call site imported from the
owner; `getattr(cli, "_safe_write_output")` now raises the ordinary
`AttributeError`. The root module's own budget (`__all__ == ["main"]`, under
150 lines) is pinned by `tests/test_cli_root_surface.py`.

The move required classifying the whole `cli_*` family as `frontends`, which
surfaced ~47 real direction violations (the CLI reaching past the engine into
`policy`, `compare` and `extract`). Those are closed, not suppressed: each now
routes through a `workflows` re-export surface (`gate`, `extraction`,
`findings`, `scan_config`) — see `workflows/AGENTS.md`. Four inversions in the
other direction closed too, taking `ENGINE_CLI_BOUNDARY_ALLOWLIST` from 15
entries to 4 over this phase and the last.

`cli_params.py` has since physically moved to `cli/options/params.py`
(ADR-061 Phase 4) — pure relocation, since it was already classified
`frontends` via `legacy_paths` beforehand, so no reclassification or import
surprises were involved. The rule that move confirmed still stands for the
next candidate: **this is a migrated package, so `unclassified-import`
applies to every module physically inside it.** Check a candidate's full
first-party import set before moving it, not just whether it looks
leaf-shaped — a module that reaches a `policy`-classified package (like
`abicheck.policies`, the shipped policy documents) for policy-owned discovery
data still routes through the matching `workflows` facade
(`workflows.policy_file.builtin_policy_names`) rather than importing it
directly, the same boundary this section's `cli/commands` table entries
already respect. `abicheck.policies` briefly carried a `model` classification
during this same migration, which the architecture checker accepted but which
was itself the wrong layer for policy-owned data (Codex review) — it belongs
in `policy`'s `legacy_paths` alongside `policy_file.py`, not `model`'s.

**Still open:** `service.py` (1763 lines) has not been thinned, and the reason
is now specific rather than open-ended. Moving it means classifying the 28 flat
modules it imports, which surfaces 67 direction violations whose load-bearing
cause is that `*_metadata.py` conflate a model dataclass with its parser —
`AbiSnapshot` has typed fields of `PeMetadata`/`DwarfMetadata`/…, so making
those modules `extract` creates a forbidden `model -> extract`. Splitting them
is Phase 5's "parsers and catalogs" scope. See ADR-061's Phase 4 status note
for the full measurement.

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

## Product invariant (local consequence)

A front end **parses and passes**: equivalent *resolved* requests through
the CLI, the typed Python API, and the GitHub Action must produce the same
decision (ADR-055/049's cross-front-end gate). Raw-input resolution still
differs today — the CLI and Action discover `.abicheck.yml` and apply
`--pack`, a bare typed-API call does not — and closing that
gap is direction, not a claim. A new option
that would only exist in one front end, or that changes analysis under the
name of a presentation switch, is a request-model gap to fix at the owner,
not a Click callback. Root `AGENTS.md` "Product decisions and change
routing" states the rule.
