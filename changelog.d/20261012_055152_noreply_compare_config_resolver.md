### Changed

- **One release configuration resolution** — a directory/package release now
  resolves its configuration through
  `abicheck.workflows.compare_configuration.resolve_release_pack_application`,
  the scalar compare's own `resolve_compare_configuration`, on both routes:
  - the CLI supplies the flags the user typed
    (`cli_compare_receipt.resolve_release_pack_application`);
  - `workflows.release_compare.execute_release_compare` resolves it from
    `ReleaseCompareOptions` when the caller passes no `pack_application`.

  A release run through the typed workflow now records the same gate on each
  member's `contract_context` (and the same `effective_config_digest`) as the
  typed scalar compare of that pair. CLI output and exit codes are unchanged.
