### Security

- **`compile.compiler` from an auto-discovered `.abicheck.yml` no longer
  selects the compiler executable.** It names an executable to invoke for
  header extraction, the same "arbitrary command from a config the operator
  didn't choose" shape ADR-032 D5 already gates for `build.query` — a
  config found by directory search (e.g. a fork PR's own branch content
  auto-discovered by a CI job) is ignored, with a clear diagnostic, unless
  the config was explicitly bound via `--config`.
- **`compile.options` may no longer smuggle a compiler-plugin-loading flag**
  (`-Xclang -load`, `-fplugin=`, `-Xclang -add-plugin`, ...), whether as a
  single token or reassembled from otherwise individually whitespace-free
  YAML list items — rejected unconditionally with a clear error, regardless
  of config trust tier, since no header-ABI-extraction use case needs one.

### Fixed

- `debug.pdb_path` is now rejected (alongside the other `debug.*` keys) for
  a stored-BundleFacts `compare` — neither the stored/stored nor
  stored/live comparison path ever consumed it, so silently accepting it
  looked like it did something.
- Plain `dump --help` now shows `--debug-root` again — it was missing from
  the curated help panel even though it remains a real, supported flag.
- The stored-OLD_FACTS + live-NEW_INPUT `compare` dispatch path now applies
  `compile.ast_frontend_fallback`/`compile.allow_unsupported_castxml`
  before the header-AST extraction it triggers, matching the main
  `compare`/`dump` dispatch paths — both toggles were previously silently
  ignored on this path.
- `action/run.sh`'s and `action/validate-inputs.sh`'s compile-context
  guards no longer misfire on the Action's own default `lang: c++` value —
  only an actual override now counts as "lang was explicitly requested".
- `action/run.sh`'s synthesized `compile.options` now treats a multi-line
  `gcc-options` Action input the same way `scan`'s own equivalent flag path
  does: one line is one already-complete token, never re-split on
  whitespace regardless of line breaks.

### Documentation

- Clarified `gcc-options`' Action input description: a multi-line entry's
  synthesized `compile.options` items must each be whitespace-free (a
  spaced line is rejected, not silently accepted), and a single-line value
  is shell-quoting-aware split.
- The `dump`/`compare` cross-compilation examples in
  `docs/use/dump-compare-flags.md` now pass `--config .abicheck.yml` so the
  shown `compile:` block is actually loaded.
