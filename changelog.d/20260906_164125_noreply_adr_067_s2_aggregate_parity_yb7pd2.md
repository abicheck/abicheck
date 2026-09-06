### Added

- **ADR-067 workstream C, slice S2 (completed)**: closes the one gap the
  bundle/aggregate parity slice left open. `appcompat.scope_diff_to_required_symbols`
  (the `--required-symbol(s)` plugin-host mirror of `scope_diff_to_app`) now
  synthesizes the same suppressible, ledger-recorded `CONSUMER_REQUIRED_SYMBOL_REMOVED`
  overlay for a missing required entrypoint that `--used-by` has carried since
  ADR-044 P2 -- previously such a symbol reached only the CLI's own bespoke
  `missing_entrypoints`/`scoped_missing_labels` string list, invisible to the
  disposition ledger's raw-versus-effective totals and unsuppressible by an
  exact rule. `--required-symbol(s)` also gains a `suppression` parameter end
  to end (`_apply_required_symbol_scoping` -> `scope_diff_to_required_symbols`
  -> `check_plugin_host_contract`), mirroring `--used-by`'s existing one.

### Changed

- The evaluate/record/withheld-rule-diagnostic sequence both consumer-scoping
  paths need is now one shared primitive,
  `policy.disposition_close.record_and_maybe_suppress_overlay` --
  `scope_diff_to_app`'s own loop was rewritten to call it too, so the two
  overlay mechanisms share one recording implementation rather than each
  hand-rolling it.
