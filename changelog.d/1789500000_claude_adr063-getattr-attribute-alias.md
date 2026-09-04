### Fixed

- **The `semantic-ir-cutover` architecture gate now recognizes `getattr`
  reached through a name assigned from a `builtins`-module attribute.**
  `import builtins as b; g = b.getattr; g(snap, "typedefs")` bypassed the
  gate: the call itself is a bare `Name` call (no attribute for the
  module-alias check to see), and the assignment producing `g` was an
  `ast.Attribute` value the alias resolver didn't recognize as a `getattr`
  source. `_getattr_aliases` now also treats `<name> = <builtins-module-
  alias>.getattr` as a `getattr` alias assignment (CodeRabbit review on
  PR #1041).
