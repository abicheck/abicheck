<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **One removed export is one breaking finding.** An exported function or
  variable that left the export table while its public declaration survived
  was reported twice: as `func_visibility_changed` by the header-aware
  detector and again as `func_removed_elf_only` by the symbols-only L0
  fold. The duplicate ELF-only finding is now dropped in
  `deduplicate_cross_detector`, and the drop is recorded as `deduplicated`
  in the disposition audit. The `soname_bump_recommended` advisory now counts
  distinct breaking symbols instead of findings, so the same root cause no
  longer reads as "2 binary-incompatible change(s)".
