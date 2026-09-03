### Fixed

- **The opaque-type by-value scan's leaf spelling is now extracted with a
  depth-aware scope split.** For a qualified record with a qualified
  template argument (`api::Wrapper<dep::Tag>`), a naive `rsplit("::", 1)`
  cut inside the template argument and extracted `"Tag>"` instead of the
  real leaf `"Wrapper<dep::Tag>"`. A genuine by-value exposure rendered
  that way went undetected, leaving the type wrongly `opaque` and letting
  the stable identity tier suppress a real layout change. Now uses
  `diff_helpers.depth_aware_bare_name`, the same `<`/`>`-nesting-aware
  split already used elsewhere in the codebase for this exact class of
  qualified-name splitting (Codex review on PR #1041).
