### Fixed

- **`compare`'s report summary no longer mislabels a public-surface shrink as
  a compatible addition.** `summary.compatible_additions` stayed the
  historical "all COMPATIBLE findings" total, but since surface metrics
  became unconditional (ADR-027 Phase 5) a `public_surface_shrank` finding
  (a net *decrease* in public declaration count) had nothing to distinguish
  it from a real API addition when a consumer read that field alone. A new
  `summary.quality_issues` field (JSON `report_schema_version` 3.13; a
  parallel "Quality issues" row in the Markdown review digest) names the
  non-addition subset, mirroring the `quality_issues` field the release
  fan-out (`compare` over a directory/package) already emits per library.
- **The byte-identical-inputs coverage warning
  (`note_if_same_binary_compared`) now fires on a snapshot-input comparison
  too.** It previously keyed only on `LibraryMetadata.sha256`, which
  `collect_metadata` deliberately never populates for a JSON/text snapshot
  path — so a `--old-snapshot`/`--new-snapshot` compare (or any typed
  `CompareRequest` over two snapshot files) could never get the warning
  even when both sides were content-identical. It now falls back to a
  canonical snapshot-content digest when live-binary metadata is
  unavailable on either side, worded as "old and new abi snapshots are
  byte-identical" (not "binaries") to keep the weaker claim honest.
- **A finding downgraded to `evidence_status: unattributed` no longer
  carries the same unconditional impact text as an `artifact_proven`
  finding of the same kind.** `impact_for()` now accepts the finding's
  evidence status and appends an evidence caveat (e.g. `func_removed`'s
  "dynamic linker will refuse to load or crash at call site" no longer
  reads as certain for a finding synthesized from header-only evidence
  with no matching symbol-table entry). Wired into the JSON report's
  `impact` field; the Markdown/HTML/SARIF renderers are not yet updated
  and keep the unconditional text for now.
