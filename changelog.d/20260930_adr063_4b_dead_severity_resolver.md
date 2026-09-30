### Removed

- Removed `cli_helpers_compare._resolve_severity`, a second severity resolver with no production caller. `compare` resolves severity once, through `resolve_compare_config` (ADR-063 sub-phase 4B).
