### Fixed

- **The GitHub Action names the `unused_rules: block` gate** — a run that exits `1` because a configured suppression/reclassify rule matched nothing now publishes `verdict=RULES_UNUSED` with its own warning and job-summary line, on both the two-sided (`exit.unused_rules_contribution`) and audit-only (`exit_axes.unused_rules`) `compare` shapes. It previously fell through to `ADDITIONS_UNACKNOWLEDGED` on a two-sided run; that verdict is now chosen only when the additions axis gated.
