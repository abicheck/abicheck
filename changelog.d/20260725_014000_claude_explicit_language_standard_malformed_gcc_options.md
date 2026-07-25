<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`explicit_language_standard()` (`abicheck/_compiler_options.py`), ADR-050's
  `language_standard` profile-fingerprint helper, crashed on a malformed
  `--gcc-options` value** (e.g. an unbalanced quote) instead of degrading
  gracefully — found while adding the dedicated coverage a code review
  called for. Unlike `dumper_contract.py`'s own `shlex.split` call (already
  guarded), this one had no `try`/`except ValueError`, so a malformed
  `--gcc-options` string now unconditionally aborted every header-based
  dump via `_attach_extraction_contract`, a new failure mode a pre-ADR-050
  dump never had. Fixed the same way as its sibling call site: catch
  `ValueError` and fall back to no tokens from the unparseable string
  (`gcc_option_tokens` alone still contributes normally).

