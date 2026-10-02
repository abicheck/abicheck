### Fixed

- **Markdown reports no longer drop findings of a flooded kind.** A
  non-gating section with more than 25 findings of one kind rolls them up
  into one counted line, but the rolled-up findings were removed from the
  section while the rollup line itself was never rendered, so they vanished
  from Markdown output with no trace. The rollup line is rendered again,
  and every finding is either listed or counted.
- **`compat` can read back a dump it wrote with `-dump-path X.dump`.** The
  `-old`/`-new` loader sent every `.dump` file to the ABICC Perl importer,
  but `compat dump` writes an abicheck JSON snapshot under any name. The
  format is now chosen from the file's content.
- **`-std=` spellings are read one way everywhere.** The probe harness took
  the first `-std=` flag and rejected GNU and draft spellings
  (`gnu++2a`, `c++1z`), and picking the newest standard across compile
  entries ranked `c++98` above `c++20`. One owner
  (`abicheck.model.language_standard`) now reads the last flag, as the
  compiler does, and orders editions by publication year.

### Removed

- **Code that no command, workflow or documented Python API reached.**
  Found by running the CLI's real scenarios (dump/compare in every output
  format, both header frontends, release directories, `compat`, `deps`,
  `project history`) under coverage and cross-checking every unexecuted
  definition for production references. Removed, with the tests that only
  exercised them: the stderr GitHub-annotation renderer and
  `annotations_step_summary` (the JSON `annotations` field is the live
  path), the appcompat Markdown/JSON/HTML renderers (`appcompat_html.py`),
  the remaining step functions of the deleted `merge`, `collect` and
  `scan --artifact-set` commands, inputs-pack compaction
  (`compact_inputs_pack`), `derive_l2_compile_context`, the NumPy metadata
  self-check, the unused `compile_context_options` decorator, the
  `_CastxmlParser` methods that only delegated to the
  `extract.headers.castxml` modules, and about forty smaller unreferenced
  helpers and wrappers. The `collect` pack builder some tests still use as a
  fixture producer now lives in `tests/_build_source_collect.py`.

### Changed

- The internal-namespace default list and the legacy verdict-to-exit-code
  mapping each have one owner (`model.symbol_ownership`,
  `policy.severity.legacy_exit_code`); the `--used-by` scoped exit code and
  the public-surface closure no longer keep their own copies.

### Fixed (continued)

- **Integer type spellings compare alike across backends.** DWARF spells
  `unsigned long` as `long unsigned int`; `canonicalize_type_name` now folds
  integer specifier order, so finding identity and type comparison no longer
  depend on which extractor produced the spelling.
- **Export accounting recognises every internal namespace.** It used a
  narrower list (no `__detail`/`_impl`) than the rest of the tool; it now reads
  the shared vocabulary.
- **An evidence pack edited after it was written is rejected** by
  `--build-info`/`--sources` instead of being attached under its old content
  hash.
