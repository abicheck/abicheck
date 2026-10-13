<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Inline transitions from stale header-backend facts are declined, not reported** — `FUNC_BECAME_INLINE` and `FUNC_LOST_INLINE` no longer fire when either side is a header-derived snapshot written before the implicit-inline fix (SCHEMA_VERSION below 53). Comparing a pre-fix clang or castxml baseline against a current candidate used to produce a spurious wave of these RISK findings. The declined comparison is recorded in the assurance status as `inline_facts_reliable`. Re-dumping the baseline restores the comparison.
