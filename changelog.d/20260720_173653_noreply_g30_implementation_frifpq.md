### Added

- **`.abicheck.yml` gains a `targets:`/`bundles:`/`profiles:`/`baseline:`
  block** (G30 P1.5, ADR-047 §3) declaring a project's CI-integration
  topology — library/app-consumer/plugin-contract targets, release bundles,
  which build profiles are ABI contracts, baseline channels, and a
  per-target `checks:` list assigning `{channel, depth, required,
  gate_mode, profiles}` tuples. New `abicheck project-targets validate
  [CONFIG]` command checks cross-references and kind-specific rules
  (`abicheck/buildsource/project_targets.py`). `dump`/`compare`/`scan`
  don't read this block yet — it's the config surface G30 P1.4's (not
  built yet) run-plan generator will consume.

