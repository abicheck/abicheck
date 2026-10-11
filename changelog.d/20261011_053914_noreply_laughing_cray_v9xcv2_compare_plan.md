### Changed

- **One compare configuration resolution for the CLI and the typed API** —
  `--pack` and `.abicheck.yml` `policy.overrides` resolution for a
  single-pair compare moved out of the CLI into
  `abicheck.workflows.compare_configuration`, which raises typed
  `CompareConfigurationError`s (`ValidationError` subclasses) that the CLI
  maps to exit 64. New API fields: `CompareRequest.pack_paths` applies
  ADR-049 D8 packs exactly as `compare --pack` does, including a gate pack
  that moves a `gate.severity.*` level (and so `CompareResult.exit_decision`
  and `severity_config`), and `CompareRequest.stated_settings` names which
  defaulted settings (today `policy`) the caller stated; `None`, the default,
  keeps reading every field as stated. The CLI-only
  `cli_compare_receipt.resolve_and_apply` was removed; its owner is
  `workflows.compare_configuration.resolve_compare_configuration`.
