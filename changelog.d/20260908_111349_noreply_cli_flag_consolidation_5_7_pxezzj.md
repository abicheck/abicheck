### Fixed

- **Phase 7d flag-demotion follow-up**: fixed several stale references to
  `compare`'s now-removed `--dso-only`/`--fail-on-removed-library`/
  `--on-incomplete-scope` flags left over from their demotion to
  `.abicheck.yml`'s `release.dso_only`/`gate.fail_on_removed_library`/
  `scope.on_incomplete` — the root `README.md`'s exit-code table, the
  single-file "set-only-flag ignored" warning (named the removed
  `--dso-only` instead of the surviving config key), and five `description`
  fields across the published `compare`/`aggregate` report JSON Schemas
  (and their `docs/reference/schemas/v1/` mirrors). `scripts/gen_cli_reference.py`
  also collapsed a multi-paragraph `help=` string's internal blank line
  incorrectly, terminating `docs/reference/cli-reference.md`'s options
  table partway through — every option help string is now whitespace-
  collapsed before being placed in its Markdown table cell.
