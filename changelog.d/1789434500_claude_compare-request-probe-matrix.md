### Added

- **`CompareRequest.old_probe_matrix` and `CompareRequest.new_probe_matrix`.** The typed Python API now accepts the build-configuration matrices the CLI takes as `--build-info old=<matrix>`/`new=<matrix>`; their diff (`cxx_standard_floor_raised`, `api_depends_on_consumer_env`, `behavioural_default_changed`) joins the findings exactly as on the CLI. Both routes load them through `abicheck.workflows.pair_evidence.load_probe_matrix_changes`; giving only one side is a `ValidationError` (a usage error on the CLI, same message).
