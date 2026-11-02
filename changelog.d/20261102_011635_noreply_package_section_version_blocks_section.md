### Fixed

- **A newer section version in a ProjectSnapshot package blocks only that
  section** — a directory package whose section was written at a newer
  `section_schema_version` than this build understands is no longer refused
  whole: as in a single-file snapshot, only the detectors reading that
  section are not comparable, with the same cause text, and the section is
  kept verbatim on re-save. A newer `provenance` section still refuses
  (ADR-062 D14).
