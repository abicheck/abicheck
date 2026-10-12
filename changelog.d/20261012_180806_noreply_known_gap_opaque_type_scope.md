<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Opaque-type suppression no longer matches across scopes by bare name when the stable pairing is incomplete** — when the two snapshots' `EntityId` pairing is not proven complete, a finding about a visible type is no longer suppressed because an opaque declaration in another namespace shares its bare `RecordType.name`. Such findings are now kept, and the drop is still recorded under `downgrade_opaque_type_changes`. Some findings that were silently hidden before are now reported; this affects only incomplete comparisons.
