### Fixed

- `compare --build-info old=…` / `--sources new=…` again print a Flow-2
  `abicheck_inputs/` pack's non-fatal validation findings (incomplete fact
  families, empty source surfaces) to stderr. Moving the loader into the
  engine replaced its direct `click.echo` with an `on_warning` callback, and
  the compare-side resolver did not thread the CLI's sink through — so a
  successful comparison could conceal degraded evidence.
