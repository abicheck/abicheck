<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- The composite Action's compile-context config guard now also rejects a
  project config supplied via the documented `extra-args: --config PATH`
  passthrough, not just an explicit `build-config` input or an
  auto-discovered `.abicheck.yml` — `extra-args` is appended after the
  synthesized `--config` overlay, so Click's last-flag-wins previously let
  it silently discard the overlay's compiler/sysroot settings.
- `report/render_release_markdown.py`'s Markdown renderer for a
  directory/package release's bundle and matrix findings sections no
  longer filters findings itself — it now consumes an already-computed,
  immutable `--view show=` projection from its caller
  (`cli_compare_release_helpers.py`), matching `report/AGENTS.md`'s
  renderer contract.

### Documentation

- Migrated every catalog example (`catalog/cases/*/README.md`, mirrored
  into `docs/reference/examples/`) whose shown `abicheck compare`/`dump`
  command still used a flag Phase 7 retired from those two commands
  (`--ast-frontend`, `--compiler`, `--compiler-prefix`, `--compiler-option`,
  `--lang`) to the `.abicheck.yml` `compile:` block + `--config` form —
  copy-pasting the old spelling exited 64.
