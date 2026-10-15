### Changed

- **`abicheck.service` no longer re-exports private helpers** — `_attach_header_graph`, `_resolve_symvers`, `_typeinfo_functions`, `_try_header_scoped_dump`, `_has_matched_public_surface` and `_render_deps_section_md` now live only in their owner modules (`service_header_graph_attach`, `workflows.input_resolution`, `workflows.dump.header_scoped`, `frontends.render`). Documented public names (`run_dump`, `run_compare`, `resolve_input`, `render_output`, …) import from `abicheck.service` exactly as before.
