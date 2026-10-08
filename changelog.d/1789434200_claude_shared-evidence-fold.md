### Changed

- **Internal: one evidence fold for every compare route.** The native `compare` CLI and the typed pipeline (and so every directory/package member) now diff embedded build-info/source facts and fold the `--abi3` audit through the same `abicheck.workflows.pair_evidence.fold_pair_evidence` before classifying. `abicheck.frontends.cli.compare_enrichment.fold_abi3_into_extra_changes` is removed; its owner is `fold_pair_evidence`. Output, exit codes and report schemas are unchanged.
