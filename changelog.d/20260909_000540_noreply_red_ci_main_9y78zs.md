### Fixed

- **A C-to-C++ self-healed clang header dump no longer risks reporting a
  stale compile-language mode on a cache hit.** A pure-`#include` umbrella
  header that auto-detects as C, then self-heals to C++ after a missing
  C++ standard header, used to cache its parsed AST under the key/path
  computed from the pre-retry (C-mode) inputs — the only inputs available
  before clang has even run once. A later, identical dump would then
  cache-hit that entry and report the pre-retry `resolved_force_cpp=False`
  alongside genuinely self-healed C++ content, a residual this module's
  own docstring already documented as a known (if narrow) gap. It became
  consequential once a downstream consumer (the clang backend's `is_cxx`
  gate on the asm-label extern-C exclusion) started trusting that bit for
  correctness. `dumper._clang_header_dump` now writes the cache entry
  under a key/path recomputed from the mode that actually produced the
  result whenever self-heal changed it, rather than the stale pre-retry
  key — a later identical call simply misses the now-unused pre-retry key
  and re-runs the self-heal fresh (losing that one narrow case's caching
  benefit) instead of ever replaying a stale bit.
