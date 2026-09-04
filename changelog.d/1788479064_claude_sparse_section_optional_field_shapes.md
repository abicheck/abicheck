<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`storage`'s sparse legacy-section DTOs now validate every optional
  field's wire shape too**, closing the same corruption-to-missing-evidence
  gap the prior fragment closed for required fields — `_freeze_extra`
  previously validated only which keys `extra` may carry, not each key's
  own value shape (e.g. `BuildSection.from_document({"build_source": []})`
  previously succeeded and round-tripped a malformed record unchanged).
  Every field across all six sparse sections now has a declared shape,
  checked before freezing.
