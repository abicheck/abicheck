<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Removed

- **Release and pack-resolution internals moved out of the CLI modules, with their old import paths removed** — `validate_pack_manifests` and `resolve_release_pack_application` are now owned by `abicheck.workflows.pack_resolution`, and `cli_configuration_inputs`, `typed_parameter_names` and `COMPARE_CONFIG_PARAMS` by `abicheck.workflows.compare_input_adapters`; `abicheck.cli_compare_receipt` keeps only the Click-context translation. `abicheck.cli_compare_release_helpers` no longer re-exports the gate, exit, release-header or Debian-symbols helpers, and its unused `_extract_if_package` is gone: import each from its owner (`abicheck.policy.release_gate_options`, `abicheck.frontends.cli.release_exit`, `abicheck.workflows.release_inputs`, or `abicheck.workflows.bundle_facts_stored_compare` for the extraction seam). Output, exit codes and messages are unchanged.
