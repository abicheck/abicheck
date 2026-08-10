<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`Param.is_va_list` now has a producer (G31 Phase C continued).** No
  backend had ever populated this fact — `param_became_va_list`/
  `param_lost_va_list` were unreachable on real input. The direct-clang
  header-AST backend now extracts it (`dumper_clang_qualifiers.
  _clang_param_is_va_list`, x86-64 System V spelling only — the one ABI
  verified here; an unrecognized target's real `va_list` still reads
  `False`, a conservative false negative rather than a guessed spelling).

  Two gates come with it, mirroring `Param.is_restrict`'s own (G31 Phase
  C): the detector is now **header-tier, clang/hybrid-producer-only** —
  unlike `is_restrict`, this fact is not symmetric across producers, since
  castxml has never populated it and always reports `False`, so pairing it
  with a clang/hybrid side would read every genuine `va_list` parameter as
  added/removed purely from which backend parsed which side. Snapshot
  schema **v23** adds `clang_va_list_facts_reliable`, which declines the
  comparison against a persisted pre-v23 clang/hybrid baseline whose
  blanket `False` is real-but-wrong data. The whole-snapshot disk cache
  version is bumped to `11` so a warm cache re-extracts rather than
  replaying a snapshot that predates the fix.

### Documentation

- **[Header-Backend Capabilities](https://abicheck.readthedocs.io/en/latest/reference/header-backend-capabilities/)
  updated (G31 Phase C continued)** for `Param.is_va_list`'s new clang-only
  producer — regenerated from `scripts/backend_capabilities.py`.
