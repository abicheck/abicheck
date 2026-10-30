<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--set PATH=VALUE` on `dump` and `project capture-variants`, and `config_overrides` on `DumpRequest` and `run_dump`** (ADR-091 D4, Phase 2) — the same one-key-per-run override `compare --set` takes, validated by the `.abicheck.yml` schema and applied with the same precedence (explicit flag over `--set` over the file over the default). `dump` applies `compile.lang`, `build.compile_db_filter`, the `debug.*` keys its resolver reads, and the two CLI-only `compile.*` toggles; `capture-variants` applies `bundle_variants`. Every other key exits 64 naming the command, rather than being dropped. A typed caller gets the same keys through `DumpRequest.config_overrides`. `project plan`, `project validate`, `project history`, `deps tree`, `deps compare` and `aggregate` take no `--set`, since none reads a key `--set` can set.
