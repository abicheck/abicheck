### Removed

- **Private names no longer re-exported from `abicheck.service`** — the facade
  carried six underscore-prefixed names (`_attach_header_graph`,
  `_resolve_symvers`, `_typeinfo_functions`, `_render_deps_section_md`,
  `_try_header_scoped_dump`, `_has_matched_public_surface`) that were
  re-exports, not part of the supported API. Import them from their owner
  module instead (`abicheck.service_header_graph_attach`,
  `abicheck.workflows.input_resolution`, `abicheck.frontends.render`,
  `abicheck.workflows.dump.header_scoped`). Every documented name still imports
  from `abicheck.service` unchanged, and no behavior, exit code or schema
  changed.
