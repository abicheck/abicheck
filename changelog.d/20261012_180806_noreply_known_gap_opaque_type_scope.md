<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Opaque-type suppression no longer matches across scopes by bare name when the stable pairing is incomplete** — a finding about a visible type is no longer suppressed by an opaque declaration that shares its bare `RecordType.name` (or a stable `EntityId` that a visible declaration also carries), in the opaque-type and opaque-struct paths. Such findings are now kept, and drops are still recorded under `downgrade_opaque_type_changes`. Some findings that were silently hidden before are now reported, in complete and incomplete comparisons alike.
