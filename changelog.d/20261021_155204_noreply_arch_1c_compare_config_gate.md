<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **`compare --use-cases` attribution has one typed owner per layer.** The attribution computation moved to `policy/use_case_attribution.py` (manifest model and entrypoint resolution in `policy/use_cases.py`), its JSON, text and Markdown projections to `report/use_case_impact_render.py`, and the manifest read and refusal to `workflows/compare/use_case_attribution.py`. The `impact/use_case_impact.py` module is removed. The required-symbol policy fold moved to `workflows/compare/configuration.py`. No CLI flag, message, exit code or report field changes.
