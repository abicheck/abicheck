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
- **Action config overlays now discover a `--sources` root's own
  `.abicheck.yml` and validate configs before merging them** —
  `embed_build_source()` reads `build:`/`sources:`/`compile:`/`source:`/
  `debug:` via a lookup anchored at `--sources` itself, separate from the
  checkout-root walk the Action's config-overlay merge already performed;
  once any compile-context input triggered that merge, the `--sources`
  root's own such blocks (e.g. a distinct `sources.graph: full`) were
  silently dropped. The merge now also discovers and folds in that config.
  A base document that is syntactically valid YAML but structurally
  invalid per `BuildConfig`'s own schema (e.g. `release: []`) is now
  validated (and rejected loudly) before any merge, instead of having its
  invalid key silently replaced by the Action's own overlay. Two more
  overlay-cleanup gaps (a merge-subprocess failure, and a directory
  matching a configured `build.compile_db` name) are also fixed.
- **`compare`'s stored-BundleFacts-OLD-INPUT driver now rejects
  `release.dso_only`/`release.include_private_dso` for a single-file
  NEW_INPUT** — when NEW_INPUT resolves to neither a directory nor a
  recognized package archive, the driver compares it as a single library
  file with no ELF-type check at all (unlike the directory-walk case,
  where the existing shared-library discovery already excludes
  executables regardless of this setting), so these settings previously
  had no effect and no diagnostic. Now rejected with a `click.UsageError`.
- **Action `dso-only`/`include-private-dso`/`fail-on-removed-library` can
  now explicitly override a discovered `.abicheck.yml`'s own `true`
  setting** — these three inputs previously declared a `default: 'false'`
  in `action.yml`, making an omitted input and an explicit `dso-only:
  false` indistinguishable; a workflow that explicitly set one of them to
  `false` to override a discovered/explicit project config's own `true`
  had that override silently ignored (the synthesizing function's
  early-return guard treated "all false" as "nothing to do" and left the
  discovered config untouched). The three inputs now declare no default,
  so an omitted one resolves to an empty string distinguishable from an
  explicit `"true"`/`"false"`, and only an explicitly-given value is
  written into the synthesized overlay.
