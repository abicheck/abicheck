<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`PolicyFile.validate_overrides()` was dead code — a risky `--policy-file`
  override never warned anyone.** The method already flagged, e.g.,
  downgrading `func_removed` to `ignore` or `type_vtable_changed` to `risk`,
  but nothing ever called it, so its warnings never reached a user. Wired in
  at both places a policy file is actually loaded: `cli_params.
  _load_suppression_and_policy()` (`compare`, `scan --against`, `appcompat`,
  and `compare-release`'s early validation/matrix paths) now echoes each
  warning to stderr, and `service.load_suppression_and_policy()` — the
  Tier-2 chokepoint `compare-release`'s real per-library fan-out and any
  direct Python API caller actually loads its policy through — now logs
  them too, so a risky override no longer stays silent on that path either.
  Also fixed a false positive in `validate_overrides()` itself: it flagged
  every `_CRITICAL_BREAKING_KINDS` override without checking whether the
  configured base policy already classified that kind below `BREAKING` —
  e.g. `soname_changed` is already `risk` under `strict_abi`, so a policy
  file that explicitly restates `soname_changed: risk` no longer reads as a
  downgrade. Only an override strictly weaker than the base policy's own
  verdict for that kind is now flagged. Finally, `compare-release` reloads
  the same `--policy-file` several times over one run — its early strict-
  suppression validation, its probe-matrix path, and its sequential
  per-library fan-out (plus again for JUnit's re-run) — which used to mean
  the identical warning logged once per load; `policy_file.
  dedup_validate_overrides_warnings()`, a scope now wrapped around the
  whole `compare-release` run and shared by both loaders
  (`cli_params._load_suppression_and_policy` and `service.
  load_suppression_and_policy`), collapses that down to one warning per
  release run (the `--jobs N>1` process-pool path is unaffected, and every
  other caller — a plain `compare`, `scan --against`, or a direct Python
  API call — is unaffected too, since it never enters that scope).
