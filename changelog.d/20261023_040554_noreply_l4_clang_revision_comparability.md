<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Clang L4 payload-revision mismatch no longer fabricates breaks** — when a stored source surface and a fresh one carry the same `fact_set` contract but different clang `producer_version` stamps, `compare` now compares only the pinned `typedef` fields (`REVISION_COMPARABLE_FIELDS`), withholds the other source-replay checks, and reports the lowered assurance through `SOURCE_FACT_COVERAGE_INCOMPLETE` with the compared-fact count. Unpinned identity fields (blank `mangled_name` in the clang projection) no longer read as removed or added entities (ADR-063 amendment A1-A4).
