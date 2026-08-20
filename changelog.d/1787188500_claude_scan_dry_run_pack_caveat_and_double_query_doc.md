### Fixed

- **CLI cleanup phase two, PR B follow-up (Codex review, fresh evidence)**:
  `scan --against --dry-run --pack <gate-pack>`'s preview no longer appends
  a self-contradicting "a selected --pack may adjust it" caveat to the
  exit-code-scheme label. By the point `scan_cmd` renders that label,
  `_resolve_scan_evaluation_config` has already folded the selected pack
  into `resolved_cfg` — the caveat exists for `compare --dry-run`'s own
  call site, where the pack genuinely has not been resolved yet at render
  time, and does not apply here.

### Docs

- Documented, as a known and accepted limitation rather than fixed, that
  `service_input_resolution._resolve_side_snapshot_impl` can invoke an
  authorized `build_query` twice — once via the L2 include/compile-context
  seed, once via the L3-L5 build-source embed step — since each currently
  runs its own independent `collect_inline_pack()` call. A real fix needs
  sharing one collection result across the two differently-scoped layer
  collections (L3-only seed vs. L3+L4+L5 embed), which is separate,
  follow-on PR 3A work.
