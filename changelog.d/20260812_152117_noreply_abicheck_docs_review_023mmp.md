### Documentation

- **Fixed several correctness issues surfaced by a documentation review.**
  The `--header` help text (and generated `docs/reference/cli-reference.md`)
  no longer claims a headerless native binary "falls back to symbols-only
  mode" — without headers abicheck actually uses whatever debug info is
  available first (DWARF on ELF, PDB on PE), only degrading to symbols-only
  when neither headers nor debug info are present. `README.md`,
  `docs/start/getting-started.md`, and `docs/learn/architecture.md` no
  longer claim DWARF debug-info cross-check on macOS — abicheck has no
  Mach-O DWARF/debug-map reader, so a headerless Mach-O scan is always L0
  only (already correctly documented on `docs/reference/platforms.md`).
  `docs/use/cli-usage.md` and `docs/use/tool-modes.md` got the same
  DWARF/PDB-before-symbols-only correction. `docs/reference/
  header-backend-capabilities.md` and `docs/use/github-action.md` now state
  that `--ast-frontend auto` never silently falls back from castxml to
  clang without the explicit `--allow-ast-frontend-fallback` opt-in (or
  `ABICHECK_ALLOW_AST_FALLBACK=1`), matching `action.yml`/`cli_options.py`.
  `docs/reference/config-file.md` no longer says the `targets:`/`bundles:`/
  `profiles:` run-plan generator is "planned but not built" — `abicheck
  project plan` implements it — and now explains why an explicit `--config`
  fails loudly on a malformed file while an auto-discovered one only warns
  and falls back, instead of reading as two disconnected rules.
