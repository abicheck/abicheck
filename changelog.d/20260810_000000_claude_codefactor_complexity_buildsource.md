### Changed

- **The `buildsource/` AST and pack-handling internals were restructured to
  cut cyclomatic complexity** — fourteen functions flagged by CodeFactor's
  `Complex Method` check were split into named, individually-testable
  helpers. The heaviest were `type_graph`'s two AST passes (`_walk_types`,
  `_index_declared_entities`), which now share one `_AstIndexes` bundle
  instead of threading four index dicts through every recursive call, and
  `toolchain_probe._check_one_overlay`, whose compiler family/version/target
  validations became three separate error producers. No behaviour, output,
  or public signature changes.
