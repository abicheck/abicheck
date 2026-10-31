### Added

- **Snapshots keep data a newer abicheck wrote, and block only what they
  cannot read** (ADR-062 D14/D15) — loading and re-saving a snapshot now
  preserves an unknown section, a section header's `extensions` map, and
  unknown fields (including an `extensions` map) inside function, variable,
  type and enum records, instead of refusing the document or dropping them;
  they count toward the evidence and publication digests. A section whose
  header names a `must_understand` extension this build does not know, that
  is written at a newer `section_schema_version`, or whose extraction
  `failed` is kept verbatim but not decoded: `compare` marks exactly the
  detectors that read it as not evaluated ("not comparable: …", naming the
  section and the extension), keeps running the others, and reports
  `analysis_assurance: partial` — exit codes are unchanged. Every written
  section is listed in a new `section_availability` table
  (`present`/`failed`/`not_collected`), and the package manifest accepts a
  `must_understand` list. Writing such a snapshot into a ProjectSnapshot
  package or a bundle (JSON, archive or package) is refused with an error
  rather than storing the blocked section as present and empty; save it as
  a single-file snapshot. The per-section wire field inventory is now
  generated from the encoders and diffed in CI.
