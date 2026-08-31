### Changed

- **`abicheck.service_render` is now classified `frontends`** (ADR-061
  Phase 4). It imports `reporter.py`/`sarif.py` (`report`-classified), which
  `frontends` may import but `workflows` (where `abicheck.service` itself
  lives) may not — the two had been left an open design question in this
  ADR's own text. `abicheck.service`'s `render_output`/`_render_json_output`/
  `_render_deps_section_md` now reach `service_render.py`'s implementation
  through a new `abicheck.workflows.render` bridge instead of importing it
  directly, closing the resulting `workflows -> frontends` edge (and the
  dependency cycle it created) without moving `service.py` itself.

### Notes

- No behavior, schema, or public-signature change: every name keeps its
  documented import path (`from abicheck.service import render_output`,
  etc.) and its real, checked type signature (verified with `reveal_type()`
  — a name-only re-export would have silently widened all three to `Any`
  for external callers).
