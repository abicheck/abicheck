<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **The analysis-assurance block is now a package under `policy/`** — the
  1,586-line flat `abicheck/analysis_assurance.py` (its last
  `architecture/debt.yaml` no-growth entry), with its two flat leaves
  `analysis_assurance_layout.py` and `analysis_assurance_comparability.py`,
  moved to `abicheck/policy/analysis_assurance/`. `compute_analysis_assurance`
  and the `AnalysisAssurance` result are its public interface; each axis is a
  private submodule (`_context`, `_fact_set`, `_graph_completeness`,
  `_manifest_layers`, `_target_accounting`, `_translation_units`,
  `_export_accounting`, `_compute`, `_outputs`, `_result`, `_layout`,
  `_comparability`). The move is behavior-preserving: every moved function and
  class is AST-identical to its previous form. Internal importers now use
  `abicheck.policy.analysis_assurance`, and there is no re-export at the old
  `abicheck.analysis_assurance` path, so code importing that path directly
  must switch to the new one.
