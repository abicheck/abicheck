<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Advanced DWARF extraction records what it did** -- `AdvancedDwarfMetadata.extraction` (`not_run`, `completed`, `failed`) is set by the extraction itself, and `advanced_facts_collected` reads it instead of inferring "extraction ran" from the payload. A completed parse that found nothing is collected; a run that never happened, or that failed to read the file, is not, and the `advanced_dwarf` detector reports the state in its not-evaluated reason rather than reading as a binary with no debug info. A snapshot decoded from a stored document carries no status and keeps the old inference, so no reading changes for existing snapshots; the status is not persisted, so the stored document is byte-identical.
