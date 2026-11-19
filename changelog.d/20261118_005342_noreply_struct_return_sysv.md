<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **By-value return convention is read from the header layout** — a record that a declared function returns by value now emits `STRUCT_RETURN_CONVENTION_CHANGED` when its x86_64 SysV return class changes (registers up to 16 bytes, hidden pointer above that), at stripped binary plus headers. Which records are returned by value comes from the header backend's type graph (`Function.return_by_value_fact`, snapshot schema v70, castxml), not from the spelling of the return type; an undetermined return type or member gives no finding rather than a guess. Snapshots written before v70 carry no such fact and yield no finding for it.
