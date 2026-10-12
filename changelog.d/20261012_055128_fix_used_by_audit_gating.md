### Fixed

- **`compare --used-by`/`--required-symbol` no longer prints "0 gating" beside
  a failing exit** — the disposition audit (one-line note, Markdown, JSON
  `disposition_audit.effective_total`) narrowed its gating set to the
  findings the consumer uses, while the exit code is always the full-library
  decision (workstream D-S1). The audit now counts exactly what that
  decision scores: library findings keep their gate disposition whether or
  not a consumer uses them, and consumer-only findings (e.g.
  `consumer_required_symbol_removed` overlays), which never affect the exit,
  are recorded as gate-excluded `non_gating`. The standalone
  `check_appcompat`/`check_plugin_host_contract` APIs, whose verdict is the
  scoped one, keep the narrowed count.
