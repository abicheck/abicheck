### Changed

- **Post-processing reads its kind sets from their owners by public name** — the leak-triggering, identity-vtable, never-filter, and type-level kind sets used by the post-processing steps are now public constants in `surface` and `internal_leak` (`TYPE_LEVEL_KIND_NAMES`, `NEVER_FILTER_KIND_NAMES`, `MEMBER_LEVEL_TYPE_KIND_NAMES`, `LEAK_TRIGGERING_KINDS`, `IDENTITY_VTABLE_KINDS`) instead of underscore names that other modules reached into. The sets are unchanged, so every finding, verdict and exit code is unchanged.
