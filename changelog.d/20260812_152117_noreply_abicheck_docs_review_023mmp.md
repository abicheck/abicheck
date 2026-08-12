### Documentation

- **Fixed several correctness issues surfaced by a documentation review.**
  The `--header` help text (and generated `docs/reference/cli-reference.md`)
  no longer claims a headerless native binary "falls back to symbols-only
  mode" — without headers abicheck actually uses whatever debug info is
  available first (DWARF on ELF, PDB on PE), only degrading further to L0
  binary-metadata analysis (exported symbols plus platform-specific facts —
  SONAME/dependencies/rpaths on ELF, machine type/imports/delay-load/
  hardening on PE, install name/dependencies/rpaths on Mach-O — never a
  bare symbol list) when neither headers nor debug info are present.
  `README.md`,
  `docs/start/getting-started.md`, and `docs/learn/architecture.md` no
  longer claim DWARF debug-info cross-check on macOS — abicheck has no
  Mach-O DWARF/debug-map reader, so a headerless Mach-O input's own
  binary/debug-info evidence is always L0 only (`--sources`/`--build-info`
  can still attach L3–L5 evidence independently of platform; already
  correctly documented on `docs/reference/platforms.md`).
  `docs/use/cli-usage.md` and `docs/use/tool-modes.md` got the same
  DWARF/PDB-before-symbols-only correction. `docs/reference/
  header-backend-capabilities.md` and `docs/use/github-action.md` now state
  that `--ast-frontend auto` never silently falls back from castxml to
  clang without the explicit `--allow-ast-frontend-fallback` opt-in (or
  `ABICHECK_ALLOW_AST_FALLBACK=1`), matching `action.yml`/`cli_options.py`.
  `docs/reference/config-file.md` no longer says the `targets:`/`bundles:`/
  `profiles:` run-plan generator is "planned but not built" — `abicheck
  project plan` implements it — and now distinguishes `compare`'s own
  project-config load (severity/scope/policy: always a hard error on a
  malformed file, explicit `--config` or auto-discovered alike) from the
  separate `compile:`-block loader shared by `compare`/`dump`/`scan`'s L2
  compile context, where only an auto-discovered file's parse failure
  degrades to a warning, instead of presenting the second, narrower rule
  as if it applied everywhere.
- **The published docs now carry a site-wide "unreleased" banner.**
  GitHub Pages deploys the documentation site on every push to `main`
  with no separate build for a tagged release, so it can describe
  `main`-only features (Agent Skills, `--contract` domains) with no
  indication the reader isn't looking at the latest published release —
  a documentation review's P0.1 finding. `docs/hooks.py` reads
  `pyproject.toml`'s own `version` at build time and stamps it into a new
  mkdocs-material announcement bar (`docs/overrides/main.html`) shown on
  every page, so the banner can't itself go stale between manual updates.
