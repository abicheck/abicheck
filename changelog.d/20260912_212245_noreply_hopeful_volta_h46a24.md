### Changed

- **The typed API and the CLI now agree on what omitting an option means.**
  `InputSpec.include_dependencies`, `service.run_dump`'s keyword, and the
  synthetic signature built for introspection all defaulted to `True`
  (include toolchain/system-header declarations) while
  `dump --include-system-declarations` defaults to `False`. Measured on a
  one-header C++ library, the same inputs produced **10 functions /
  `dependency_scope="filtered"`** through the CLI and **5,597 / `"full"`**
  through `DumpRequest` — and because a `filtered` side is not comparable
  with a `full` one, a baseline dumped with the CLI could not be compared
  against a candidate dumped through the typed API at all: `scope_mismatch`,
  no verdict, while the error advised passing the flag "on neither", which
  the caller already had. All three now default to `False`. **A typed-API
  caller that omitted the field and wanted the unfiltered surface must now
  pass `include_dependencies=True` explicitly.**
- **`render_output`'s `show_recommendation` now defaults to `True`**, the
  behavior every real consumer already got. The CLI passed `True` explicitly
  to compensate for a `False` default, which left a direct Tier-2 caller
  silently receiving no recommendation section — and made `render_output`'s
  own docstring ("unconditionally included in every human-facing format")
  false for the audience it was written for. Suppression stays available as
  an explicit `show_recommendation=False`. CLI output is unchanged,
  verified byte-for-byte across all seven `--format` values.

### Removed

- **The `stat` keyword, from `render_output`, `to_json` and `to_markdown`.**
  It was a dispatch flag rather than a rendering option — "ignore the format
  I asked for and render a different document" — and on `render_output` it sat
  *above* format validation, so `render_output("nonsense", stat=True)`
  returned a summary instead of raising `ValidationError`. Each outcome it
  reached already has a direct spelling, and the two summary documents are now
  re-exported from `abicheck.service` so the replacement is available at the
  same import site: `fmt="oneline"` (or `to_stat`) for the one-line summary,
  `to_stat_json` for the summary-only JSON, and plain `fmt="junit"` for JUnit
  (which `stat` never short-circuited anyway).
