### Performance

- **The clang header backend now builds each raw AST's template-parameter
  indexes once per request instead of once per parser.**
  `_ClangAstParser.__init__` rebuilt `_index_template_param_kinds`,
  `_index_template_param_defaults` and `_index_template_param_names` on every
  construction -- four whole-AST walks, since the defaults builder runs the
  names builder internally -- and one request routinely constructs several
  parsers over the *same* tree: the export-bound and neutral parses of each
  side, plus one per member of a directory/package release fan-out sharing a
  header context. All three read only the AST (never a binary's exports, the
  public-header selection, the target triple or the language mode), so they
  are now built once per raw AST inside the existing request-local AST
  acquisition scope and shared as a read-only `TemplateParamIndexes` bundle.
  On a template-heavy six-DSO shared-header comparison this took the builders
  from 14 runs over 2 distinct ASTs to 2, and the index phase from ~0.41s to
  ~0.02s. Findings, verdicts, snapshots and reports are unchanged; outside an
  acquisition scope the build stays strictly local, so a direct
  `dumper.dump()` caller retains no additional state.
