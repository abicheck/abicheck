<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A namespace-scope `constexpr` value change is reported once in source replay compare** — the header tier reports it as `CONSTANT_CHANGED` and the L4 source replay also reported it as `CONSTEXPR_VALUE_CHANGED`, so `compare --sources --depth source` listed one break twice. The header-tier finding is kept (it is artifact-anchored and carries the declaration's facts); the replay copy is matched to it by qualified name, one for one, and recorded as `deduplicated` in the disposition ledger. Verdict, exit code and `detected_total` are unchanged; the raw total still counts both observations.
