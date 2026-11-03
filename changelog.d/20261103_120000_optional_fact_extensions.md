### Added

- **A new optional snapshot fact no longer makes every new dump unreadable
  for older abicheck builds** (ADR-062 D19). An optional fact is now declared
  as an extension fact (`abicheck/storage/extension_fact_catalog.py`) and
  stored in its section header's `extensions` map, which an older reader
  keeps and ignores, instead of raising the global snapshot
  `schema_version`. A fact whose omission would make an older reader's
  result wrong is marked must-understand: the older reader then marks only
  the analyses that read that section as not comparable, or refuses the
  document when every analysis is affected. A `schema_version` bump now
  needs a recorded envelope or wire reason, checked by the new
  `schema-bump-reason` ai-readiness check. A snapshot write also refuses
  header extensions recorded for a section it does not write, instead of
  dropping them silently.
