---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Release report: live and stored operands render different documents

Index: [Known gaps](../known-gaps.md)

**Status (updated 2026-10-24): PARTIAL.** Both operands now go through one
builder, `report.release_document.build_release_report`, but each still
yields its own document shape. They agree on every semantic field both carry
(pinned by `tests/unit/report/test_release_document.py`); the shapes differ.

A directory/package `compare` (live operand) and `compare
OLD.bundle-facts.json NEW` (stored operand) describe the same kind of
release comparison, but:

- **JSON.** The live document carries `release_schema_version`, `libraries`
  as a list of flattened per-member entries, `changed_libraries`,
  `unmatched_old`/`unmatched_new`, `warnings`, `exit`, `change_inventory`,
  `severity`, `effective_config_digest`/`effective_config_fields`,
  `disposition_audit`, the matrix fields and `show_only_filter`. The stored
  document carries `mode: "bundle_facts"`, `old_bundle_facts`,
  `new_is_stored`, `per_library_verdict`, `libraries` as an object keyed by
  member holding each member's full scalar `compare` JSON, and
  `analysis_errors` (always present). The live one names the same field
  `bundle_analysis_errors` and emits it only when non-empty. Only the stored
  `bundle_findings` carry `demangled_symbol`. The stored document has no
  `release_schema_version`, `exit` block, receipt digest or
  `disposition_audit`.
- **Markdown.** The two headings differ ("ABI Release Comparison" and
  "Bundle-facts comparison"), and so do the sections. The stored Markdown is
  always demangled; the live one follows the demangle setting.
- **Formats.** The stored operand renders JSON, Markdown, JUnit and `oneline`.
  JUnit and `oneline` go through the live release renderer, fed the stored
  comparison's own per-library entries
  (`frontends/cli/commands/compare_bundle_facts_release.py`); pinned against the
  live operand by `tests/test_cli_compare_stored_release_parity.py`. It still
  has no HTML or `--view show=` projection: HTML is rendered from the JSON
  document, which needs an effective-config receipt the stored path does not
  resolve, and `--view` is rejected for a stored OLD_INPUT.
- **JUnit detail.** The live JUnit emits a passing testcase for every unchanged
  symbol, because the live operand has the old-side inventory. The stored
  operand has none, so its JUnit omits those cases and its test counts differ.
  Verdict, exit code, members and findings agree.

Converging them changes a published JSON shape on one side, so it needs a
release-schema decision. It is not a byte-preserving refactor. When they
converge, `test_stored_and_live_documents_still_differ_in_shape` fails;
close this entry then.

