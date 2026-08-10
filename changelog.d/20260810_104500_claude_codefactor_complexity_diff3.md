### Changed

- **Split two more CodeFactor "Complex Method" findings in the aggregate text
  report and the ADR-049 contract evaluator.**
  `aggregate.AggregateResult.render_text` now delegates each block that can be
  absent or take several shapes to its own renderer
  (`_render_contract_coverage_lines`, `_render_profile_matrix_lines`,
  `_render_profile_entry_line`, `_render_coverage_and_gate_lines`), leaving the
  entry point as the section order it describes.
  `contract_evaluation.evaluate_change_contract_relevance` splits its chain of
  early-return authorities into `_mode_dispatch_decision`,
  `_pipeline_authority_decision` and `_surface_classification_decision` — one per
  kind of authority, applied in the same order as before. Both
  behaviour-preserving.
