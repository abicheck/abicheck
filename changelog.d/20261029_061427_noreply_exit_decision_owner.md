<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **One owner for every `compare` exit decision** — `abicheck.policy.exit_decision_precedence` now decides the exit code for the single-pair path, the directory/package release, a `--used-by`/`--required-symbol` consumer's own scoped code (`resolve_scoped_exit_code`, formerly `workflows.consumer_scope_gate._scoped_exit_code`) and `check-target`'s gate-mode composition (`resolve_gate_mode_exit_code`, formerly `buildsource.check_report.final_exit_code`). Frontends and renderers only read that decision. `abicheck.frontends.cli.release_exit` is removed: its `_exit_compare_release` now sits in `abicheck.frontends.cli.runtime` beside the pair exit, and the decision it used to assemble is `policy.release_exit_decision.resolve_release_exit_decision_with_assurance`. Exit codes, report schemas and CLI output are unchanged.
