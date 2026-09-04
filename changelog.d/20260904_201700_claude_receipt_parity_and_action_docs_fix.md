### Fixed

- **The `--contract` receipt could disagree with the gate that actually
  scored the run under `--profile ci-gate`.** The live gate computation
  (fixed earlier this session in
  `cli_compare_options._resolve_profile_severity_preset`) already drops
  `ci-gate`'s injected `severity_preset: "default"` placeholder once the
  project states its own severity policy — but
  `compatibility_evaluation_frontend.py`'s separate D7 receipt resolver
  still supplied that placeholder as a `run_profile` candidate
  unconditionally, so a persisted `contract_context.evaluation_context`
  could record `gate.preset.id == "default"` (attributed to `ci-gate`) for
  a run that actually scored the project's own `severity.preset:
  info-only`. Fixed by applying the identical "project already configures
  its own severity policy" guard to the candidate-building code, so the
  receipt and the live gate agree again. Found by Codex review on PR #1062.
- **`action.yml`'s exit-code/verdict docs still described `--artifact-set`
  as permanently floored at exit `1`** for a member's evidence-contract
  abort — stale after this session's earlier fix gave it the same
  dedicated exit `7` the single-binary path uses. Updated to describe the
  current behavior (still correctly noting a sibling library's real
  API_BREAK/BREAKING outranks it). Found by Codex review on PR #1062.
- **`EVALUATION_CONTEXT_SCHEMA_VERSION` bumped `2` → `3`**: deleting
  `--exit-code-scheme`/`exit_code_scheme` removes
  `field_provenance["gate.exit_code_scheme"]` from every persisted
  `evaluation_context` from here on, and a v2 reader could not tell
  "removed because the field no longer exists" apart from "removed
  because this run stated nothing for it." Found by Codex review on
  PR #1062.
