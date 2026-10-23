<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Clang L4 projection carries only the public surface** — `PublicSurfaceClangParser` (`abicheck/buildsource/source_extractors/clang_projection.py`) narrows the clang AST parser's declaration lists by header provenance before any parse, and `project_clang_surface()` projects them into source entities. System-header declarations such as `fclose` no longer enter the projection (Phase 4 of the L4/L2 convergence plan). The L4 emitter and its output are unchanged.
