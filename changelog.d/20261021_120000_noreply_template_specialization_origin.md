### Fixed

- **A class-template specialization the new header stops instantiating is no
  longer reported as removed when a macro produces its class-head, or when it
  was instantiated from a partial specialization the new header still
  declares.** The origin of each instantiation is now read from the structure
  of clang's AST during the header-graph pass and recorded on the graph's
  `template_instantiation` node. Every declared partial specialization keeps
  a `template_decl` node, so a comparison can tell that the pattern is still
  declared. A deleted or replaced partial specialization, a deleted explicit
  specialization and a removed template still report `type_removed`. What the
  graph records changed, so `EXTRACTOR_GENERATION` moves to 6 and
  `SOURCE_GENERATION` to 4, with `template_class_origin.py` added to the
  source tier's scope.
- **A deleted explicit specialization written on the same line as its primary
  template's class-head is reported as removed again.** castxml's
  declaring-line reading took the first class-head on the line and read the
  explicit specialization as an implicit instantiation. That hid the
  `type_removed` and its members' removals. A line spelling both shapes is now
  undetermined.
- **castxml spells an undeduced `auto` return type `auto`, not `AutoType`.**
  Before this, `unspecified_return_now_named` could not fire on a castxml
  snapshot.
