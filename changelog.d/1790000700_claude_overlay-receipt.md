### Changed

- `compare`'s persisted contract receipt (`contract_context.evaluation_context`)
  now records where a POST-manifest overlay came from. When
  `.abicheck.yml`'s `contract.overlays.post_manifest` is applied (a single-pair
  `compare` with an explicit `--config`), `contract.overlays` is stated at
  `project_config` tier, naming the config file and its digest, next to the
  ledger's observation that the overlay applied. A discovered config, the
  directory/package fan-out and the `--no-baseline` audit apply no overlay,
  and their receipts still say `built_in_default`.
