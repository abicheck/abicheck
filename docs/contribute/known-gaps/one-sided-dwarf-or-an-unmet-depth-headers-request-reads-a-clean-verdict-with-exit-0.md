---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# One-sided DWARF or an unmet --depth headers request reads a clean verdict with exit 0

Opened 2026-11-08. The index entry is in [known-gaps.md](../known-gaps.md).

**Status (2026-11-09): CLOSED** by ADR-090 (PR #1963): both runs now read `UNCONFIRMED` with exit 1 on every surface; the strict xfails in `tests/test_invariant_oracles_evidence_shapes.py` are its regression tests. History: found by the evidence-shape oracles (`tests/test_invariant_oracles_evidence_shapes.py`). The library pair is the stripped-pair entry's: `add` gains a parameter, built with `gcc -shared -fPIC -g`, no `-H` headers.

| Run | `verdict` | `analysis_assurance.status` | `version_bump` | exit |
|---|---|---|---|---|
| old with DWARF, new stripped | `COMPATIBLE` | `partial` | `patch` | 0 |
| both stripped, `--depth headers` | `NO_CHANGE` | `failed` | `none` | 0 |

Neither run reports `func_params_changed`. Each renderer states the clean result: terminal `Gate: ACCEPTED`, Markdown and review `` `NO_CHANGE` ``/`` `COMPATIBLE` ``, review `**Gate:** PASS`, HTML `Compatibility: …`. The first row is the one-sided case ADR-090 records in its matrix; the second fails its depth request (`failed`) and still passes the gate.

The controls in the same module hold. DWARF without headers reads `BREAKING`/exit 4 with `complete` assurance. A DWARF no-change pair reads `NO_CHANGE`/exit 0 with `complete` assurance. A header extractor that fails (an unparseable header) or is unavailable (castxml not on `PATH`) stops the run with an error rather than comparing an empty surface.

The fix is [ADR-090](../adr/090-unresolved-surface-is-not-a-clean-verdict.md) (accepted): verdict `UNCONFIRMED`, `version_bump: undetermined`, exit 1. The module's strict xfails state that contract as separate checks (JSON verdict, version bump, exit code, rendered wording), so each flips as the ADR's stages land. Owner: the same as the stripped-pair entry.
`false-clean` — a compare with type-level evidence on one side only, or with its requested depth unmet, reads clean with exit 0.
