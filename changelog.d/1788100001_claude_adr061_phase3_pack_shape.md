### Changed

- **ADR-061 Phase 3 (converge artifact workflows)**: the two pack-shape
  predicates now have owners. `is_pack_dir` moved out of the oversized
  `buildsource/inline.py` into a new dependency-free
  `buildsource/pack_shape.py` (re-exported from `inline` unchanged, so every
  existing import keeps working), and the `None`/`is_dir` guard around
  `is_inputs_pack` moved beside it in `buildsource/inputs_pack.py` as
  `is_inputs_pack_dir`, together with a named `is_any_pack_dir` for the
  "either shape" idiom seven sites were re-spelling. That guard had been
  copied privately into three modules because the original lived in the CLI
  layer and engine-side code could not import upward; all three are now
  delegating aliases.
