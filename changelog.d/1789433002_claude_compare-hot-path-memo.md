### Performance

- The legacy-fallback answers of the surface-fact accessors
  (`declared_in_headers`, `in_public_contract`, `binary_exported`) are
  shared frozen constants instead of a new `Fact` per call, and four pure
  string helpers on the template/CPO path (`mask_operator_symbols`,
  `template_angle_depth`, `_strip_param_signature`, `_cpo_function_stem`)
  are memoized. Together about 30% off `compare()` across the synthetic
  workload suite (14.0 s to 9.9 s, best of three); the surface-fact share
  applies to pre-v46 and hand-built declarations, which carry no stored
  facts.
- Ordered de-duplication of call-graph leak proof paths and of graph-edge
  occurrence ids no longer scans a list per item.
