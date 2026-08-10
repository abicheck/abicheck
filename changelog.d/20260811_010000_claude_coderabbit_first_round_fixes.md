### Fixed

- **`callback_graph.py` never indexed a `FieldDecl` inside an unnamed
  struct/union**, so a callback slot declared inside one (`union { handler_t
  cb; };`) could never resolve. Field indexing no longer requires the
  owning record to have a name.
- **`callback_graph.py`'s `CALLBACK_MAY_INVOKE` join silently dropped a real
  dispatch path** when the same caller reached the same registered function
  through two different slots: without a role discriminator, both edges
  shared an identical relation key and the second was dedup-merged into the
  first, losing its own `slot` fact. The joined slot id is now also stamped
  as the edge's `role`.
- **`macro_graph.py`'s `ClangMacroGraphExtractor` could raise an uncaught
  `TypeError`** on a malformed AST location (`"line": null` or similar),
  aborting extraction for the whole build instead of degrading to a
  diagnostic like every other malformed-input case it already handles.
- **`inline_graph_fold.py`'s callback-coverage propagation could raise
  `KeyError`** on a hand-built or deserialized graph whose `narrowed_passes`
  and `narrowed_scope` dicts disagree — now degrades to an empty scope
  instead.
