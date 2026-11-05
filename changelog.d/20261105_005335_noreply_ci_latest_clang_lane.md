### Fixed

- **The clang header backend keeps typedef'd anonymous structs and enums on
  clang 22+** — `typedef struct {…} T;` and `typedef enum {…} T;` dropped out
  of every clang-backend snapshot made with clang 22 or newer, because clang
  22 removed the `ElaboratedType` node whose `ownedTagDecl` the backend read
  and marks the tag type `isTagOwned` instead. Both shapes are now read, so
  the surface no longer depends on which clang ran.
- **The opt-in dependency-declaration pruner no longer drops class members
  nested in a pruned function or variable** — with
  `ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS=1`, a dependency function or variable
  whose subtree held a class (a lambda's closure type, a local class) was
  collapsed with that class's methods inside it. Such a subtree is now kept
  whole, as the pruner's "never prune a method" rule promises.
