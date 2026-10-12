---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A typed release compare does not record the gate on its members (2026-10-12)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-12 at `f25eddcdd`): OPEN.** Typed route only; the CLI is unaffected.

`workflows.release_compare.execute_release_compare` is the release compare
owner, reachable without Click. A release member records its resolved
evaluation config on its result (`member_compare.record_release_resolved_config`)
from the release's `PackApplication`. Only the CLI builds that application
(`cli_compare_receipt.resolve_release_pack_application`, which resolves the
whole evaluation config: gate, project overrides, packs). So a typed caller
that passes no `pack_application` gets members whose
`contract_context.evaluation_context.resolved_config.gate` is the default
legacy gate, even when the plan's `GateOptions` carries a severity preset.
The typed *scalar* API records the gate itself, from the request
(`workflows.compare_gate_receipt.install_resolved_gate_receipt`).

Verdicts, findings, dispositions and exit contributions are unaffected: the
plan's gate drives every member's `_compatibility_contribution` and the
release severity fold. Only the recorded receipt (and so
`effective_config_digest`) differs.

Pinned by `tests/test_release_compare_workflow.py`: its `severity_strict`
cells are `xfail(strict=True)`, so the fix turns them red until the xfail is
removed.

**Fix direction.** Resolve the release's evaluation config once, in
`workflows/`, from the request and options, so every route (CLI, typed)
hands members the same config. That is the compare-configuration resolver
consolidation (architecture review candidate 3): moving
`resolve_cli_config`/`resolve_release_pack_application` out of
`cli_compare_receipt.py`. Don't patch the typed route with a second resolver.
