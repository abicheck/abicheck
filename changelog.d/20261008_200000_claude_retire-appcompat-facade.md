### Removed

- **`abicheck.appcompat` retired (Lane C stage 4)** — the delegation-only
  facade left by the Lane C split is gone. Import the owners instead:
  `abicheck.workflows.consumer_scope` (`scope_diff_to_app`,
  `scope_diff_to_required_symbols`, `check_against`,
  `parse_app_requirements`, `AppCompatResult`, `PluginHostContractResult`),
  `abicheck.workflows.consumer_scope_standalone` (`check_appcompat`,
  `check_plugin_host_contract`) and `abicheck.model.consumer_requirements`
  (`AppRequirements`, `ConsumerImportFacts`, `LibraryExportFacts`).
  `docs/use/python-api.md`, `docs/use/appcompat.md` and
  `docs/use/plugin-systems.md` show the new paths.
