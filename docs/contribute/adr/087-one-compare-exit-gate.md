# ADR-087: One Compare Exit Gate — the Typed API Takes the CLI's Severity and Assurance Inputs

**Date:** 2026-10-25
**Status:** Accepted (2026-10-25) — implemented on `refactor/unify-exit-gate`. The native CLI's exit codes do not change.

Governed by: [ADR-064](064-canonical-gate-algorithm-and-exit-decision.md) (one gate algorithm and one `ExitDecision`), [ADR-068](068-one-comparison-product-and-scan-retirement.md) (`compare` is the one comparison product; the typed API is a front end of the same pipeline), [ADR-054](054-cli-project-integration-surface-consolidation.md) (no opt-out flag or environment variable pre-1.0). Documented contract: [`docs/reference/exit-codes.md`](../../reference/exit-codes.md).

## Context

A single-pair `compare` reaches its exit code through two derivations of the same `EffectiveGate`:

- **Native CLI** (`workflows.gate.effective_gate_for_resolved_compare_config`): reads the resolved `.abicheck.yml` config. It honors the `severity:` block (`preset`, `abi_breaking`, `potential_breaking`, `quality_issues`, `addition`) and `assurance.require_complete`.
- **Typed API** (`workflows.compare_finalize.finalize_classified_pair`): read only the `CompareRequest` fields. It passed `None` for the four per-category overrides, so only `severity_preset` reached the gate, and it never passed `require_complete_analysis`. A typed caller therefore could not express the `.abicheck.yml` rules that `exit-codes.md` documents.

`exit-codes.md` documents those `.abicheck.yml` rules as changing `compare`'s exit code, and the CLI implements them. The typed API is the incomplete one. The two gates differ in their inputs, not in their rules.

## Decision

1. **The documented contract is the one gate.** `effective_gate_for_compare(severity, *, require_complete_analysis, result)` in `workflows/gate.py` is the single derivation. Both front ends reach the exit code through it. `effective_gate_for_resolved_compare_config` is now a projection from the resolved config onto it.
2. **The typed API gains the inputs.** `CompareRequest` adds `severity_abi_breaking`, `severity_potential_breaking`, `severity_quality_issues`, `severity_addition` (the `.abicheck.yml` `severity:` overrides, precedence over `severity_preset`, as the CLI reads them) and `require_complete_analysis` (`assurance.require_complete`). Every new field defaults to today's behavior. `finalize_classified_pair` passes them through.
3. **The CLI does not change.** Its exit codes, its config keys and its output are untouched, so no migration warning and no `Breaking` changelog entry are needed. The change is additive for the typed API.

## Input class

Affected: a **typed** `compare` call that sets any of the five new fields, and a **CLI** run whose `.abicheck.yml` sets `severity.*` or `assurance.require_complete` (unchanged, shown for comparison). A typed caller that sets none of them is unaffected.

## Before / after

Exit code for a `BREAKING` pair (the `abi_breaking` category is the one that gates):

| Typed `CompareRequest` | Typed before | Typed after | CLI (`.abicheck.yml`, unchanged) |
|---|---|---|---|
| no severity fields | 4 (legacy) | 4 | 4 (legacy) |
| `severity_preset="default"` | 4 | 4 | 4 |
| `severity_preset="info-only"` | 0 | 0 | 0 |
| `severity_abi_breaking="info"`, no preset | not expressible (4 for the same pair) | 0 | 0 |
| `severity_preset="strict"`, `severity_abi_breaking="info"` | not expressible (4 for the same pair) | 0 | 0 |
| `require_complete_analysis=True`, analysis not `complete` | not expressible (0 for the same pair) | 1 | 1 |

"Typed before" shows what the same input produced before the fields existed. Each "after" row matches the CLI and the documented table. The `require_complete` row changes only when the analysis is incomplete; a complete analysis still exits by the verdict.

## Consequences

- Existing CLI users see no exit-code change. Existing typed callers see no change unless they set a new field.
- The CLI and the typed API now share one derivation, so the drift the two-gate layout allowed cannot recur silently.
- The `run_compare` keyword shim does not forward the new fields yet. Its callers use `CompareRequest` directly.
- The `require_complete` exit (`1`) is covered at the gate level only. An end-to-end test needs a committed incomplete-analysis fixture, which this change does not add.

## Verification

`tests/test_compare_exit_gate_unified.py`: the CLI and the typed API must each equal the documented exit for every preset × override combination (the oracle is the documented table); the CLI's gate projection and the typed derivation must be the same `EffectiveGate`; and `require_complete_analysis` must reach the gate. Against the pre-change `compare_finalize.py`, the three override-`info` cases fail.
