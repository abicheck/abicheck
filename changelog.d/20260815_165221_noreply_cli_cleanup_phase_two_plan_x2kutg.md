<!-- Codex review follow-up on CLI cleanup phase two, PR 1: Python API compatibility. -->

### Fixed

- **`abicheck.service.render_output()` no longer breaks on the pre-existing
  `stat=`/`show_recommendation=` keyword arguments.** Only the CLI's
  `--stat`/`--recommend` flags were announced as removed; `render_output`
  is exported Tier-2 Python API, and a caller still spelling
  `render_output(..., stat=True)` got a bare `TypeError`. Both keywords are
  now accepted as compatibility shims: for a non-JSON, non-JUnit `fmt`,
  `stat=True` is equivalent to `fmt=service_render.ONELINE_FORMAT` (`json`
  and `junit` each keep their own pre-existing shape instead, fixed in later
  rounds — see below), and `show_recommendation` is accepted but has no
  effect (the recommendation it used to gate is now unconditional). Prefer
  `fmt=ONELINE_FORMAT` directly in new code.
