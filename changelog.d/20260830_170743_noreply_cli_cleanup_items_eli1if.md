### Fixed

- **`scan --dry-run` and `scan --artifact-set --dry-run` now validate
  `--abi3` before previewing success.** Both previously reported exit 0 for
  a candidate that is not a recognisable CPython extension module, even
  though the real run rejects it with `EVIDENCE_CONTRACT_ERROR` (exit 1).
  Both previews now run a cheap, binary-only extension probe
  (`python_ext.detect_python_extension_from_binary`, no DWARF/header/build
  parse) and block with the same message the real run raises.
- **`scan --dry-run`'s TU-count estimate no longer reports a confident-looking
  `0` for a pinned depth backed only by a query-declaring `--config`.** When
  no `--sources`/`--build-info` is given but `build.query` is declared, the
  real run's trusted query supplies L3 evidence at run time — the estimate
  now flags the count as genuinely unknown instead of silently understating
  a `--budget` pick that relies on it. `L4_source_abi`/`L5_source_graph`
  derive their own counts from L3's, and previously stayed silently
  confident (`"0 TU(s), ~0.00s"`) even once L3's own row was fixed to say
  "unknown" — both single-binary and `--artifact-set` dry-run previews now
  flag them too.
