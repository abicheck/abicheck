<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Release members compare through their typed request, not a keyword round-trip** — a directory/package compare built each member's comparison as loose keyword arguments and then re-translated them into the typed request the single-pair path builds. `compare_member` now builds that request once, directly, from the member's release request, and every release field is mapped explicitly (an unmapped field is refused rather than dropped). The public `service.run_compare` keyword API is unchanged. Behaviour is unchanged: the member reports match the keyword route's reports on the F2 corpus.
