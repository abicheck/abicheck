### Security

- **CLI cleanup phase two, PR 3A follow-up (Codex review, real reproduction)**:
  `service_input_resolution._seeded_includes_and_compile_context` now enforces
  its own `allow_build_query` gate before forwarding `build_config`/
  `build_query`/`build_compile_db` into the shared L2 seed/L3→L2 fold
  primitive, rather than relying on `collect_inline_pack`'s identically-named
  parameter, which is a documented, deprecated no-op there. Without this
  local gate, any caller of the new (currently unused by any shipped code
  path) Tier-2 pass-through added earlier this session could have caused an
  operator-supplied `build.query` command to execute merely by supplying a
  `build_config` path, with no separate consent step — violating this
  primitive's own "never execute a build system as a side effect" contract
  for library callers. No currently-shipped caller passed these parameters
  yet, so this closes a latent gap before it became reachable, not an active
  vulnerability.
