### Fixed

- **A directory/package release run's own aggregate JSON receipt still
  missed a project-backed `.abicheck.yml` `policy.overrides` value.**
  `_release_summary_effective_config_block` (the primary release JSON and
  `--output-dir`'s `summary.json` alike) folded only `PackApplication.
  policy_overrides` (deliberately just the pack's own contribution) and
  never consulted `pack_application.resolved_config.policy.overrides` --
  the one canonical, D7-precedence-correct merge of explicit file, pack,
  and project config. A project override that genuinely scored every
  library in the release (each library's own exit code reflects it) left
  the release-level summary's `effective_config_fields["policy.overrides"]`
  empty. This is the fourth occurrence of the same receipt-provenance bug
  class this project has now closed (native CLI, per-library release
  receipt, typed API, and now the release-level aggregate receipt) --
  fixed by reading `policy.overrides` from `resolved_config` directly
  rather than re-deriving an incomplete merge, since `resolve_cli_config`
  (the one place a `PackApplication` is constructed for either the
  per-library or the release-summary caller) already resolves it correctly
  whether or not a `--pack` was selected.
- **A typed `CompareRequest.project_policy_overrides`/`pack_policy_overrides`
  entry targeting `Verdict.NO_CHANGE` was silently dropped from the run's
  persisted receipt while still being applied to score the comparison.**
  Neither field has a document-parsed severity vocabulary behind it the
  way a real `.abicheck.yml`/`--pack`/`--policy` document does (that
  vocabulary has no spelling for `NO_CHANGE` at all), so this was
  reachable only through a typed caller constructing an already-parsed
  pair directly. A comparison could quietly demote a finding to compatible
  while its own receipt showed no override at all -- replaying that
  receipt later would restore the default breaking verdict. Fixed by
  rejecting `Verdict.NO_CHANGE` as an override target for both fields at
  classification time, with a clear usage error, rather than silently
  scoring it and hiding it from the receipt.
