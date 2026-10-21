### Fixed

- **Binary-only findings no longer claim proven public reachability** —
  `unspecified_return_now_named`, `experimental_removed_without_replacement`,
  `experimental_graduated` and `mandatory_template_param_added` tagged their
  subject `public_reachable`/`PROVEN_REACHABLE` from the export population.
  They now derive the tags from observed header or source evidence (ADR-084);
  without it reachability reads `unknown`. Verdicts are unchanged.
