### Fixed

- **A hidden friend function/function-template's identity is now resolved
  in its enclosing namespace, not the befriending class.** The clang
  header-AST walk built a hidden friend's `EntityId` from its lexical
  scope, which still named the befriending class even though a hidden
  friend is a member of the nearest enclosing namespace
  ([namespace.memdef]). Two hidden friend templates with an identical
  signature declared in different classes — genuinely the same entity,
  confirmed by clang rejecting the pair as a redefinition — previously got
  two different `EntityId`s. `hidden_friend_owner` still records which
  class befriended each declaration.
