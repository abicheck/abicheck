### Changed

- ADR-063 Phase 3: `abicheck.surface.compute_public_surface` is removed; use
  `abicheck.policy.public_surface_query.PublicSurfaceQuery.resolve_public_domain`
  (or `policy.public_surface_closure.resolve_public_surface`). Every internal
  caller now goes through `PublicSurfaceQuery`. `surface_graph.compute_surface_metrics`
  and `diff_surface_metrics.diff_surface_metrics` no longer resolve the public
  surface themselves: they take the resolved closure as `public_type_names` /
  `old_public_type_names` / `new_public_type_names` (from
  `PublicSurfaceQuery.public_type_names`), and without it count every type,
  as they already did for an unresolvable surface.
