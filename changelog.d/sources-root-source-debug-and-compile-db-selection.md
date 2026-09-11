### Fixed

- The GitHub Action's sources-root config promotion
  (`abicheck.action_config_overlay.apply_sources_root_config_blocks`, used by
  both `action/run.sh`'s compile-context overlay and
  `actions/check-target/action.yml`'s assurance-overlay step) no longer
  promotes a `--sources` tree's own `source:`(singular)/`debug:` blocks into
  the synthesized `--config` overlay for `mode: compare`'s single-sided shape
  (a stored-snapshot old operand). The real `compare` pipeline never
  re-resolves either block from a per-side `--sources` tree — both are
  frozen once, from the checkout project config, before the tree is even
  considered (`frontends/cli/commands/compare.py`'s
  `_embed_inline_source_side`). Promoting them from the sources-root
  document anyway let enabling `analysis.assurance: complete` silently
  change collection depth or debug-info extraction — and therefore the
  reported findings — purely as a side effect of synthesizing the overlay.
  `compile:` is unaffected (it keeps its genuine two-stage merge for
  `compare`), and `dump`/`scan`'s own single-document selection of
  `source:`/`debug:` off the `--sources` tree is unaffected too — only
  `compare`'s single-sided shape excludes them now.

- `abicheck.action_config_overlay.discovered_compile_db_resolves` (the
  assurance-overlay/compile-context overlay's `build.compile_db` containment
  check) now validates the *same* match `buildsource/inline.py`'s real
  collector will actually select — the first `sorted(...)` glob match that is
  a file — instead of accepting the overlay the moment *any* contained match
  exists anywhere in the (previously unsorted) glob results. A glob such as
  `../*/compile_commands.json` can match both a database inside
  `--sources` and one in a lexically earlier sibling directory outside it;
  the real collector always resolves to the sorted-first match, so a
  validator that accepted on any in-root match could pass a glob whose real,
  selected database is the one outside the root — reading external compiler
  flags into the run purely because the assurance overlay's own validation
  looked at a different match than the one that gets used.
