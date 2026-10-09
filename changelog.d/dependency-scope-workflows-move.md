### Changed

- **Dump-time dependency scoping moved to the `workflows/dump/` package.** `abicheck/dumper_scoping.py` is deleted. Its code moved, unchanged, into two modules: `abicheck.workflows.dump.dependency_scope` (`resolve_dependency_scope`, `scope_snapshot_excluding_dependencies`) and `abicheck.workflows.dump.dependency_retention`, the direct-reference retention helpers. `type_reachability.py` is now classified `compare`, which retires its `dispositions.yaml` entry. Dump output is unchanged.
