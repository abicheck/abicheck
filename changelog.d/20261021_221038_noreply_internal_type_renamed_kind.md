<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`public_api_internal_type_renamed`: a renamed internal dependency target is one finding** — when a public entry still reaches the same internal declaration or type under a new name, and the source graph reconciles the old and new nodes as one entity (ADR-048), the change is reported once as `PUBLIC_API_INTERNAL_TYPE_RENAMED` (`COMPATIBLE_WITH_RISK`) instead of as a `public_api_internal_dependency_added` finding plus a separate `declaration_renamed` for the same pair. A move with no rename keeps the `public_api_internal_dependency_added` finding and says in its description that the target moved between declaring files. An ambiguous or unresolved candidate, and an unrelated add, keep today's findings, so a rename is never fabricated from weak evidence.
