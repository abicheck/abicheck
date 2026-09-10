### Fixed

- **Every `--show-only` severity filter and active-`reclassify`-rule
  disclosure now reuses a `ReportEnvelope`'s own frozen resolution date,
  and `AbiSnapshot` elements (`Function`, `Variable`, `RecordType`,
  `EnumType`) are decoupled from the caller's own objects, not just their
  containing lists** — `apply_show_only`'s severity dimension and
  `active_reclassify_rules` (SARIF's `policyReclassify`, HTML's
  `compute_confidence`, Markdown's `compute_policy_section`) previously
  defaulted to a fresh `date.today()` at render/filter time instead of the
  envelope's own `resolved_today`, so a dated `PolicyFile.reclassify` rule
  active when the envelope was built but read as expired later could
  silently drop from a `--show-only` filter or from a format's disclosed
  active-rule set — disagreeing with the same finding's frozen verdict
  elsewhere in the same envelope. Every one of these call sites now
  threads `envelope.resolved_today` through explicitly. Separately,
  `_snapshot_abi_snapshot` copied only the `functions`/`variables`/
  `types`/`enums` *containers*, leaving each element shared with the
  caller — mutating `old.functions[0].mangled` after envelope construction
  could still change a later JUnit render's testcase names, since
  `_collect_all_symbols` reads that field straight off `envelope.old`.
  Each list element now gets its own shallow `copy.copy`, closing this at
  a cost still orders of magnitude below full recursion.
