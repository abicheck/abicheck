<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`type_graph.py` missed the `enum_underlying` dependency for a
  C-style `typedef enum : U { ... } Name;`** (G29 Phase 5 item 5, Codex
  review): clang gives the enum itself no name at all in this idiom (a
  bare `EnumDecl` with `"name": ""`, a sibling of the following
  `TypedefDecl`/`TypeAliasDecl`, never nested inside it) — the
  `enum_underlying` producer's `kind == "EnumDecl" and name` guard
  silently skipped it, and the enclosing alias edge the typedef itself
  produces names only the enum's own (unresolvable) anonymous spelling,
  never the underlying type. So `typedef enum : detail::Handle { A }
  Public;` never recorded `api::Public`'s dependency on the private
  `detail::Handle`, and `public_to_internal_dependency` missed the risk.
  Fixed by remembering an anonymous `EnumDecl` sibling and, when the next
  sibling is a named `TypedefDecl`/`TypeAliasDecl` that resolves back to
  the exact same tag (an `id` match via `ownedTagDecl`/`decl`, not a name
  heuristic), emitting the `enum_underlying` edge under the typedef's own
  public name — the only identity this anonymous enum ever gets. Verified
  against real Clang 18 output for both the AST shape and the fix.
