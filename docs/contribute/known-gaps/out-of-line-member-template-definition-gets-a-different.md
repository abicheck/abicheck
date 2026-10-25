---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Out-of-line member template definition gets a different EntityId scope than its in-class declaration (header parser; template_graph fixed)

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-10-25): CLOSED.** Fixed by `extract/headers/clang/redeclarations.py`; see the closed entry in [Known gaps — closed and moot](../archive/known-gaps-closed.md). The investigation below is kept for history. Earlier status: Status (re-verified 2026-10-09 at `456989f`): PARTIAL.

`struct A { template<class T> void f(T); }; template
<class T> void A::f(T) {}` -- confirmed by direct compilation
(`clang -Xclang -ast-dump=json`) that clang emits TWO
`FunctionTemplateDecl` nodes for `f`: one lexically nested inside `A`'s
own `CXXRecordDecl` (the in-class declaration), and one at the
ENCLOSING namespace's own lexical level (a sibling of `struct A`, not a
child of it) carrying a `parentDeclContextId` pointing back at `A`'s own
node id -- clang's own signal for "this out-of-line definition's real
semantic owner is `A`, even though it isn't lexically nested inside
it." `_ClangAstParser._walk` computes both `scope`/`scope_path` purely
from LEXICAL nesting, with no `parentDeclContextId` handling anywhere in
this codebase, so the out-of-line definition gets `scope=()` while the
in-class declaration gets `scope=(Record("A"),)` -- `parse_functions`
parses BOTH nodes into separate `Function` entries with disagreeing
`EntityId`s for what is really one entity. The review further confirmed
`parse_variables` has the analogous gap for an out-of-line class-template
static data member definition. **Not fixed**: unlike this phase's other
fixes (each a small, local addition to one already-threaded parameter),
closing this properly needs a NEW general-purpose facility this codebase
doesn't have yet -- a typed, `ScopePath`-valued sibling of the existing
`dumper_clang_expr._index_decl_id_qualified_names` (which already indexes
every decl id to a FLAT qualified-name string for a different consumer,
but a flat string cannot be losslessly converted back into a typed
`ScopePath` -- collapsing `Record`-vs-`Namespace`-vs-`InlineNamespace`
is exactly the ambiguity `ScopePath` was built to prevent). That index
would need building once per parse, threading through both
`parse_functions` and `parse_variables`, and reasoning through further
edge cases this investigation did not exhaustively enumerate (an
out-of-line member of a NESTED class, an out-of-line member of a
class-template SPECIALIZATION, and whether castxml's own `context`
resolution has the identical gap for parity). A correct fix needs that
index plus verifying each edge case against a real compilation before
landing, not a narrow patch for only the one reported shape.
