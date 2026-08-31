### Added

- **ADR-063 Phase 3 (D5), slice 6: `policy.public_surface.PublicSurfaceQuery`.**
  The new, forward-facing relevance-query API surface every Phase 3
  consumer threads through: `resolve()` (bare `frozenset[EntityId]`
  membership), `resolve_public_domain()` (the structured
  `resolvable`/`has_typed_roots`/`has_provenance`/`ambiguous_type_names`/
  `exact_type_identities`/origin-index result), and
  `resolve_export_domain()` (the `contract=exports` domain's
  `ExportSurface`). This slice's own actual relevance computation still
  delegates to `surface.py`'s/`export_surface.py`'s existing, proven
  closure-walk algorithms rather than a literal graph traversal — stated
  explicitly as scoped-out follow-up work in the module's own docstring,
  not silently claimed complete (see
  `docs/contribute/plans/one-semantic-pipeline.md`'s Phase 3 section for
  the full accounting).
