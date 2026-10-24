<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A removed or retargeted typedef is reported once under `--depth source`** — the header tier and the L4 source replay both reported the same typedef removal or retarget (`typedef_removed` and `public_typedef_removed`, `typedef_base_changed` and `public_typedef_target_changed`), so one change counted twice. The header-tier finding is kept and the replay copy is recorded as `deduplicated` in the disposition ledger, so the raw total still counts both observations. Verdict and exit code are unchanged.
