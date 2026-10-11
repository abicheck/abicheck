---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A stored package's generation drift is reported only for `ProjectSnapshot` packages, and generations are bumped by hand

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-11 at `46c127111`): PARTIAL.** Unchanged since 2026-10-10. `EXTRACTOR_GENERATION`/`RESOLVER_GENERATION` now exist (`storage/versioning.py`), every package writer stamps them, `project_snapshot_store` passes them to `check_reader_compatibility`, and drift appears in `coverage_warnings` without changing verdict or exit code; an unrecorded generation is unknown. Importers stamp the generations of the build that extracted the facts: this build's only for facts it just dumped (`bundle variants` capture from binaries), a caller-supplied pair when known, else unstated (`StorageVersions.written_by_this_build`'s `generations`). Still open: the `BundleFacts` reader passes them but has no report surface; nothing enforces a bump when extraction or resolution semantics change.


From Stage E: `storage.versioning.check_reader_compatibility` reports
`semantics_differ` only when the caller passes its own
`reader_extractor_generation`/`reader_resolver_generation`, and neither caller
(`project_snapshot_store`, `storage/bundle_facts_package`) does. No build-side
generation constant exists to pass, and nothing reads
`ReaderCompatibility.semantics_differ`. So ADR-062 D2's non-fail-closed half
is unbuilt: a package produced under an older resolver is read as if its
derived results were today's, with no notice. The two fail-closed axes
(package format, comparison contract) are wired and unaffected.

Proposed: define the reader's current extractor and resolver generations next
to `PACKAGE_FORMAT_VERSION`, bump them when extraction or resolution semantics
change, pass them from both readers, and surface `semantics_differ` in the
report that loaded the package. Owner: ADR-062.
