<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **L5 type-graph role parity** (G29 Phase 5 item 5): the optional source
  graph now records three type dependencies it previously dropped on the
  floor, so a public declaration constrained by an internal type is visible
  to reachability, suppression gating, and the
  `public_to_internal_dependency` cross-check. An enum's **fixed underlying
  type** (`enum class Color : detail::Handle`) emitted no edge at all,
  because a clang `EnumDecl` carries no `type` key and the shared typedef
  code path read an empty spelling; a **non-type template parameter**'s own
  type (`template <detail::Handle H> struct Slot`, including one nested
  inside a template-template parameter, `template <template <detail::Handle
  H> class C> struct Outer`) and a template parameter's **default type
  argument** (`template <class T = detail::Impl> struct Box`) had no
  producer either. Each lands on the same graph node the templated entity's
  own field/signature edges already use — a class or alias template's
  `record_type` node, a function or variable template's `source_decl` one —
  so a template's constraint is reachable from the entity it constrains
  rather than sitting on an orphan node.
  `ROLE_COVERAGE_MATRIX` (ADR-046 D3) claims the three new roles alongside
  the existing ones, and
  [Source Graph Schema](https://abicheck.github.io/abicheck/reference/source-graph-schema/)
  now documents the full role set. Five further roles the plan item listed —
  typedef target, alias-template target, variable type, member-pointer type,
  and function-pointer signature — turned out to already be covered by an
  existing role and are now pinned by tests rather than left as an
  unstated assumption. Concept/constraint dependency remains unimplemented:
  clang's JSON AST does not name the concept at the use site, and a concept
  is a declaration that is not a type, so it needs a new graph node kind and
  its own design pass.
