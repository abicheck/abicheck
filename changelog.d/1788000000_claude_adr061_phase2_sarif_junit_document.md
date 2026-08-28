### Changed

- **ADR-061 Phase 2 (canonical report document)**: SARIF and JUnit output now
  crosses the immutable `ReportDocument` boundary before serialization, the
  same way the native JSON report modes and the one-line `--stat` summary
  already did. SARIF is itself JSON, so `sarif.to_sarif_str` projects through
  the shared `report.render_json`; JUnit needed a new pure projection,
  `report/render_xml.py`, because a `ReportDocument` deliberately stores JSON
  values only (so a renderer is never handed a live object graph it could
  mutate) and an `ElementTree` is not one — `element_to_mapping` /
  `element_from_mapping` are its lossless encoding, and indentation plus the
  XML declaration moved to the projection as formatting rather than report
  facts. Output is byte-for-byte identical for every format and mode; the one
  observable difference is that `ET.indent` now mutates the rebuilt tree
  rather than the caller's suite. `compare`'s NOT_COMPARABLE (ADR-050 D2)
  `--format json`/`--format sarif` output crosses the same boundary too: the
  JSON refusal report moved to a new `report/not_comparable.py`, and the SARIF
  one renders the existing `sarif.to_sarif_not_comparable` mapping through
  `report.render_json.render_mapping_as_json`. No public function was removed
  or renamed.
