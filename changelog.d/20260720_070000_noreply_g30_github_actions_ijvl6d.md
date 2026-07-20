<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`build-output validate`'s empty-header-root check now requires an
  actual file**, not just a directory entry — a declared header root
  containing only empty subdirectories (e.g. codegen scaffolding that never
  emitted headers) previously passed the S10 hard-fail guard because
  `Path.rglob("*")` yields directories too; PR #611 code review caught the
  gap.
- **`check_id` is now validated against its documented
  `target@profile#baseline_channel@requested_depth` shape at the point it's
  set**, matching `requested_depth`/`effective_depth`'s existing eager
  validation (`checker_types.validate_check_id`), instead of only being
  caught by the opt-in JSON Schema.
