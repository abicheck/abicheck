<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Split output rendering out of `service.py`** — `abicheck/service.py` had
  grown past the AI-readiness hard line-count cap (2011 > 2000 lines),
  failing `test_no_hard_file_size_violations` and
  `test_main_returns_zero_on_clean_tree`. `render_output` and its helpers
  now live in a new leaf module, `abicheck/service_render.py`, re-exported
  from `service` for API compatibility; no behavior change.
