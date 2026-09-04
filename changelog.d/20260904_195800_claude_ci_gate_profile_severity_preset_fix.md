### Fixed

- **`compare --profile ci-gate` no longer silently overrides a project's
  own configured severity preset.** CLI cleanup phase two PR G2's
  `ci-gate` profile injects `severity_preset: "default"` (a stand-in for
  the deleted `exit_code_scheme: "severity"` selector, needed only to make
  `severity_active` true when nothing else configures severity) — but
  once injected into the raw Click kwargs, this value was indistinguishable
  from a real `--severity-preset default` flag, so it outranked a project's
  own `.abicheck.yml` `severity.preset: info-only` under the documented
  "profile > project config" precedence. Pre-PR-G2 the profile never
  touched `severity_preset` at all (only the now-deleted algorithm
  selector), so a project's own preset governed untouched; an ABI break
  that intentionally exited `0` under `info-only` silently started exiting
  nonzero under `--profile ci-gate`. Fixed by dropping the profile's
  injected value whenever the project already configures its own severity
  policy (a preset or any per-category level) —
  `cli_compare_options._resolve_profile_severity_preset`, called from
  `cli_compare_helpers._resolve_compare_config` with a new
  `severity_preset_from_profile` flag computed from the run-profile
  injection receipt. Found by Codex review on PR #1062.
- **`docs/_meta/one-semantic-pipeline-status.yaml`'s Phase 7B account named
  a function CLI cleanup phase two PR G2 deleted** (`policy.
  release_gate_options.resolve_gate_pack_exit_code_scheme`) as still the
  shared implementation both gate-pack callers use — the ledger's own rule
  requires updating it in the same PR a described concept changes. Updated
  to describe the surviving severity-only fold (`apply_release_gate_pack`)
  and the exit-code-scheme half of that gap being mooted, not closed, by
  the flag's deletion. Found by Codex review on PR #1062.
