<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **ADR-049 Phase 5: `compare --contract-evaluation` now reports one
  canonically-resolved configuration.** The persisted `contract_context`
  block's `evaluation_context.resolved_config` used to be what
  `checker.compare` could reconstruct from its own arguments, with the CLI
  patching in the two fields it happened to know about afterwards; every
  value a caller stated was therefore recorded at the `api_request` layer,
  since a core verb sees values and not the inputs that chose them. The
  `compare` command now resolves its own inputs — typed flags, the project's
  `.abicheck.yml`, a selected `--profile`, the already-loaded
  `--policy-file`/`--suppress` documents — through Phase 1's canonical
  resolver and installs *that* object, so each field's `field_provenance`
  entry names the real ADR-049 D7 layer that selected it, with the path and
  digest of the file a replay would have to re-read. Advisory only, exactly
  as before: no verdict, finding, exit code, or report outside the
  `contract_context` block changes.

### Fixed

- **A `--profile`-selected exit-code scheme was recorded as a value nobody
  chose.** `--profile ci-gate` injects `exit_code_scheme: severity` into the
  command's own options wherever the user left the flag alone, which made it
  indistinguishable from a built-in default by the time the receipt was
  resolved — so a run that really scored under the severity-aware scheme had
  its `gate.exit_code_scheme` resolved to `legacy` for the receipt, a wrong
  value rather than merely an unnamed source. `apply_compare_profile` now
  records what it injected on the Click context, and the resolver contributes
  it at D7's own `run_profile` layer (below an explicit flag, above the
  project config).
- **A `--required-symbol` run's receipt named the wrong base policy.** That
  contract switches an untouched `--policy` to `plugin_abi` (ADR-043), a value
  that is neither typed nor configured nor the built-in default — so the
  resolved configuration reported `strict_abi` for a run that used
  `plugin_abi`. The receipt now records the value and names
  `--required-symbol` as what selected it; an explicit `--policy-file` still
  outranks it, as it does live.
- **`--public-symbols-list` was not identifiable in the resolved
  configuration.** `surface.explicit_scope` keeps the value recovered from the
  run's evidence ledger (what actually applied), but taking that entry's
  provenance too dropped the option, path, and digest of the file that
  selected the scope. The receipt now keeps the resolver's entry and appends
  the observed hop, so both the selection and its application are recorded.
