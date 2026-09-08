<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Removed

- **`compare`'s four hidden debug-resolution flags are gone.** `--dwarf-only`/
  `--no-dwarf-only`, `--debuginfod`/`--no-debuginfod`, `--debuginfod-url`, and
  `--debug-format` were already hidden and already fully config-backed; per
  ADR-068 D5, a hidden-but-accepted option is still public surface, so they
  are removed outright rather than left hidden. `.abicheck.yml`'s
  `debug.dwarf_only`, `debug.debuginfod`, `debug.debuginfod_url`, and
  `debug.format` keys are now the only way to set them on `compare`. The old
  spellings exit `64` (`No such option`). `dump` is unaffected — it declares
  its own separate, visible copies of these flags and keeps them.
