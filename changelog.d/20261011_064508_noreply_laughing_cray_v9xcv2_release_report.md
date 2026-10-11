### Changed

- The directory/package release report (and the stored `BundleFacts`
  comparison) is now built once by `abicheck.report.release_document` and
  rendered by `abicheck.report.render_release_report`. The CLI formatters
  `cli_compare_release_helpers._format_release_{summary,json,markdown,junit}`
  and `_release_findings_for_render` are removed, not re-exported. Use
  `abicheck.frontends.cli.release_report.render_release_summary` or the
  `report` builder instead. `release_disposition_audit_block` and
  `evidence_contract_error_entries` moved to `report.release_document`, and
  `_release_md_library_findings` moved to
  `report.render_release_markdown.release_md_library_findings`. The
  `compare_bundle_facts_{scope,sections}` modules are now
  `report.release_stored_{scope,sections}`. Report output is byte-identical.
