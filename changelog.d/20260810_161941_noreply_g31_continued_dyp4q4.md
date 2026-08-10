<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The direct-clang vtable reconstruction (`--ast-frontend clang`) now
  resolves a base that is a concrete template specialization
  (`struct D : A<int> {...};`), instead of silently treating it as
  unresolvable.** Clang emits the usable definition of a template
  specialization as a `ClassTemplateSpecializationDecl` — a different node
  kind from the `CXXRecordDecl`/`RecordDecl` pair the base-lookup index
  collected — so `A<int>`'s own vtable was invisible to `D`, and a
  no-keyword override added to `D` made its vtable appear to gain its
  *first* entry (a false `VPTR_INTRODUCED`), even when `D` was already
  polymorphic via the inherited slot. `dumper_clang_vtable.py`'s new
  `build_specialization_index()` reconstructs the specialization's own
  `Name<Arg1, Arg2>` spelling from its `TemplateArgument` children so it
  indexes under the same spelling a base reference names it by. Making that
  base resolvable also exposed a second, narrower gap: `owner_class_of()`'s
  mangled-name fallback recovers a specialization's *raw*, un-spelled
  Itanium template-argument encoding (`"AIiE"`), which never matches
  `RecordType.bases`'s spelled form (`"A<int>"`) — producing a false
  `TYPE_VTABLE_CHANGED` for an otherwise-correct override-slot reuse.
  Fixed by qualifying a specialization-owned method's `Function.name` with
  its spelled owner, the same convention the DWARF backend already uses
  for every member.
- **The same backend's vtable signature matching no longer misreads a
  ref-qualified method's exception specification as its parameter list.**
  A C++14+ declaration like `virtual void g() & throw();` has its own
  trailing `()` from the exception spec, which sits textually *after* the
  parameter list's own — so the previous `rfind(")")` search matched the
  exception spec's close paren instead, discarding the ref-qualifier and
  misclassifying an unrelated `void g() && throw();` as an override that
  replaces the base's slot in place. Now locates the parameter list's own
  matching close paren via the same depth-aware forward scan
  `_function_qualifiers()` already uses.
