### Changed

- **A binary-only run no longer reports a source-level break it cannot back (ADR-084)** —
  a `compare` with no headers, or only debug info, used to set an `API_BREAK`
  verdict (exit `2`) on registry membership alone. Kinds that the binary can
  produce, such as `mandatory_template_param_added`, `inline_function_removed`,
  `type_removed` and `experimental_removed_without_replacement`, now keep that
  verdict only when a header or source producer observed the entity as contract.
  Otherwise the finding is demoted to `COMPATIBLE_WITH_RISK` (exit `0` under the
  default exit map), it reads `contextual_risk` rather than `source_contract`,
  and the ADR-067 disposition audit records the rule `binary_gated_api_break` with
  its reason, so it stays visible. Header-backed runs are unchanged. The
  `std_reexport_removed` and `tag_type_renamed` findings no longer assert public
  reachability without observed evidence. Exit codes change for header-less runs.
