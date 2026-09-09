<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`action/run.sh`'s unreachable `mode: scan`→`compare` translation branch
  forwarded compile-context flags `compare` no longer accepts.** Main's
  Phase 7 CLI cleanup removed `--lang`/`--ast-frontend`/`--compiler`/
  `--compiler-prefix`/`--compiler-option`/`--sysroot`/`--nostdinc` from
  `compare`'s CLI entirely (config-only now, via `.abicheck.yml`'s
  `compile:` block) after this branch was written. The branch is never
  taken (`_SCAN_NEEDS_LEGACY_CLI` is unconditionally `true`), but
  `tests/test_action_run_contract.py::test_action_flags_are_real_cli_options`
  statically validates every flag `action/run.sh` could ever pass to each
  subcommand, dead code included, and correctly caught the drift. Switched
  the branch to the same `add_compile_context_flags` config-overlay helper
  the real `compare`/`dump` branches already use, instead of forwarding the
  removed flags directly.
