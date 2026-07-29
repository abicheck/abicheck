### Added

- **`profiles.<id>.compile.frontend`/`consumer_compile.frontend` (G34
  Phase B)** — either compiler-profile overlay may set `frontend:` to one
  of the same four values the global `--ast-frontend` flag accepts
  (`auto`/`castxml`/`clang`/`hybrid`), overriding the global default for
  that profile's cell only. Reaches `abicheck project plan`'s generated
  `run-plan.json` as its own `compile_ast_frontend`/
  `consumer_compile_ast_frontend` fields, resolved independently of each
  other. Schema/projection only in this change — no run-plan consumer yet
  threads this field into a real `dump`/`compare` invocation; see
  `docs/contribute/plans/g34-producer-consumer-compiler-profile-separation.md`
  for the remaining wiring.

### Fixed

- **`_directly_referenced_dependency_names` no longer conflates a colliding
  alias's own reach with a candidate's ambiguity, and now handles
  elaborated (`struct`/`class`/`union`/`enum`) type references
  consistently.** Several related fixes to the dump-time dependency-scoping
  retention logic (`abicheck/dumper_scoping.py`): a typedef alias colliding
  with another alias that resolves to nothing retainable (a primitive, or
  an alias whose only reached key is itself ambiguous) is no longer
  invisible to the ambiguity check; two colliding aliases that only
  *partially* agree on their targets now retain their common owner instead
  of dropping the whole spelling; a kept type's/enum's collision guard and
  a dependency candidate's own spelling now both claim every legal
  elaborated-type-specifier keyword (`class`/`struct` are interchangeable
  for a non-union C++ type); and a typedef alias reference nested inside an
  already-matched elaborated reference (`struct Foo` when `Foo` is also a
  typedef name) no longer incorrectly resolves the nested alias too.
