<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Assurance-overlay `compile.frontend` merge now case-normalizes the
  checkout side's `"auto"` sentinel** — `action_config_overlay.py`'s
  `_merge_compile_block` compared the checkout document's raw
  `compile.frontend` string against the literal `"auto"`, so a case variant
  a real `.abicheck.yml` legitimately accepts (`AUTO`, `Auto`, ...) —
  `build_config.BuildConfig.from_dict` lowercases the value before
  validating it, matching the CLI's own
  `click.Choice(AST_FRONTENDS, case_sensitive=False)` — was wrongly read as
  a concrete checkout choice and silently discarded a sources-root
  `clang`/`castxml` selection instead of letting it win.
