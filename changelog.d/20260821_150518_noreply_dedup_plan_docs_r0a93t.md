### Fixed

- **`resolved_config_from_dict` no longer coerces a non-boolean
  `gate.require_complete_analysis` value into `True`, and reading a
  persisted `evaluation_context` block gained a real gate to grow on.**
  The round-trip fix for `GateConfig.require_complete_analysis`/`scope`
  (Phase 2 item 1, dedup-and-convergence plan) used a bare `bool(...)`
  coercion, which accepts any truthy JSON value — including the string
  `"false"` — silently bypassing `GateConfig.__post_init__`'s own strict
  type check; it now rejects a non-boolean value outright. Separately,
  `EVALUATION_CONTEXT_SCHEMA_VERSION` is bumped from 1 to 2 to reflect the
  two new `gate` keys, so an older reader now fails closed via the
  existing version-ceiling check on a context it cannot fully preserve,
  instead of silently ignoring them (Codex review, PR #817). A later fix
  in this same PR additionally makes *this* build's own reader reject a
  `schema_version >= 2` payload that omits either key outright, rather
  than defaulting — see the round-trip and schema-version-gating
  changelog entries below for that behavior's own final shape.
