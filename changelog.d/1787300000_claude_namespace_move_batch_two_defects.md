### Fixed

- **`SYMBOL_RENAMED_BATCH`'s namespace-move roll-up fabricated a batch rename
  for two unrelated classes, and mislabeled a template-argument substitution
  as a namespace move.** (1) `emit_namespace_move_batches` required only
  `len(pairs) >= 2` before reporting a substitution, but any unrelated
  deleted class and unrelated added class sharing an enclosing scope always
  contribute exactly two pairs — the class's own compiler-generated
  `{ctor}`/`{dtor}` — to whatever one-component substitution their scope
  chains happen to mask into, regardless of whether the class actually
  moved namespaces (reported against real oneCCL data: an unrelated deleted
  `broadcastExt_attr` and added `window` fabricated a `broadcastExt_attr`
  → `window` "namespace segment" rename). Fixed by requiring support from
  2+ *distinct declaring entities*, collapsing a class's own ctor/dtor pair
  to one entity, so ctor+dtor of a single class is no longer sufficient
  evidence on its own. (2) `find_namespace_move_groups` could treat a
  template-parameterized scope component (one containing `<...>`) as the
  segment that changed, even when the component's *own* enclosing scope
  never moved and only a template argument nested inside it named a type
  that did — mislabeling a template-argument substitution as its own,
  redundant "namespace segment" rename (reported against real oneTBB data:
  `concurrent_priority_queue<tbb::detail::d1::graph_task *, ...>` vs. the
  `...d2...` spelling, alongside the real `d1` → `d2` group the actual move
  is already reported under). Fixed by never treating a template-bearing
  component as the differing scope position. A pretty-printed `<...>`
  spelling (the qualified-name/header-tier-fallback shape) is recognized by
  `diff_cxx_rules.component_embeds_template_args`'s literal `<` check; a
  real Itanium-mangled symbol's raw, un-demangled template-argument
  encoding (`itanium_scope_components` keeps it raw, so a literal `<` never
  appears there at all) is recognized by the new, structural
  `diff_cxx_rules.itanium_scope_components_with_template_positions` instead
  — tracked at parse time from whether a template-argument list was
  actually consumed, not guessed back out of the assembled component text
  (an earlier revision of this fix used a text-based guess for both shapes,
  which misread an ordinary identifier like `ICE` as a template block by
  coincidental spelling and silently excluded a real move of a class named
  that from detection).
