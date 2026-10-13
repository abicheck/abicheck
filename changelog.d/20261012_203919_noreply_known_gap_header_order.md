<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->
### Fixed

- **A reorder of an identical public-header set is comparable when both dumps prove it** -- `dump` now records, in the extraction contract's new `header_canonical` field (schema v59), the canonical (sorted) header order its declarations were proven equal to by a witness that parses both orders and compares the extracted declarations. `compare` accepts a pure reorder only when both sides carry the same proof; a dump whose declarations cannot be proven, or any side without the field, keeps refusing with `profile_fingerprint mismatch; differing fields: header_sequence`. Adding or removing headers is unchanged. Sorting the header set is not applied to the extraction itself.
