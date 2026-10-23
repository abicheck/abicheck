### Performance

- **Each side's public surface is resolved once per comparison** — `compare`
  used to re-run the public-surface closure walk for the same snapshot in the
  Tier-2 entity-id step, `FilterNonPublicSurface`, contract evaluation and
  surface metrics (10 resolutions on an MKL `libmkl_intel_lp64` 2025.1 →
  2026.2 compare with `mkl.h`, now 4: one per side of the main comparison and
  one per side of the separate L0 hard-removal comparison). The comparison
  now creates one `ComparisonPublicSurfaces` and passes it down explicitly
  through `compare()` and the disposition pipeline context, so its lifetime
  is the comparison's, not a global memo's. Reports are unchanged.
