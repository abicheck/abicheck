### Fixed

- **`resolve_dump_depth()` raised a bare `ValueError` on a differently-cased
  `depth` instead of resolving it, and its sibling `resolve_dump_collect_
  context()` compared `depth == "binary"` exactly.** Both are reachable
  outside the real `dump` CLI (Click's own `DepthParam.convert()` normalizes
  case before either function ever sees the value there, but a direct typed-
  API/test caller bypasses that), and both diverged from their own
  deliberately-duplicated leaf mirror, `service_compare_evidence._resolve_
  depth_collect_mode`, which already lowercases. `resolve_dump_depth()` now
  lowercases before the `EvidenceDepth` lookup, and `resolve_dump_collect_
  context()`'s header-suppression check and `dump_cmd`'s own source-only-
  plus-binary-depth rejection are both case-insensitive now too (CodeRabbit
  review on #814).
