### Changed

- **Report rendering moved to `abicheck.frontends.render`.** The rendering code moved there from the deleted `abicheck/service_render.py`, unchanged. This includes `render_output`, `ONELINE_FORMAT` and `resolve_demangle_for_format`. The documented entry point `abicheck.service.render_output` keeps working, and rendered output is unchanged.
