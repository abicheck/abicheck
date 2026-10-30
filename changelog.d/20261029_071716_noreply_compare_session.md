### Changed

- **One session runs a single-pair `compare`** — `abicheck compare OLD NEW`
  for two single operands now runs through
  `workflows.compare.session.run_pair_session`: one typed
  `PairSessionRequest` in, one `PairSessionOutcome` (classified result,
  resolved policy, evaluation configuration, gate severity) out, with the
  collect fold, compile context, operand rules, resolution, policy inputs
  and classification in one module instead of a dozen CLI helpers. The CLI
  keeps option parsing, the `--dry-run`-shared preflight and rendering. CLI
  output, exit codes and report schemas are unchanged.
- **Retired CLI helpers, with their owners** — the compile-config fold
  (`merge_compile_config`, `merge_compile_std_fields`, `apply_cli_defines`,
  `compile_config_argv_tokens`) moved from `cli_options` to
  `workflows/compile_config.py`; dump's config resolution
  (`resolve_dump_project_config` and its siblings, `DumpDebugConfig`) to
  `workflows/dump/project_config.py`; compare's inline evidence collection to
  `workflows/compare/inline_evidence.py`; the POST-manifest overlay and
  acknowledgment inputs to `workflows/compare/project_inputs.py`; ownership
  requests to `workflows/ownership_request.py`; the changed-path/`--abi3`
  enrichment (`parse_abi3_floor` included) to `workflows/compare/enrichment.py`;
  the PDB/DWP and debug-package predicates to `workflows/evidence_transport.py`.
  `cli_resolve._resolve_compare_pair`/`_resolve_compare_snapshots`,
  `frontends/cli/{acknowledgment_config,contract_overlays,ownership_config,
  ignored_flag_warnings}.py` and the CLI copies of the evaluation-config and
  policy-override folds are deleted, not re-exported.
- **One suppression/policy loader** — `workflows.compare_policy.load_policy_inputs`
  loads both documents for the CLI and the typed API, stating each refusal as
  a `RunInputError` (usage, bad parameter or operational); the CLI translates
  it once (`frontends/cli/run_input_errors.py`) and the typed API keeps its
  own `ValidationError` wording.
