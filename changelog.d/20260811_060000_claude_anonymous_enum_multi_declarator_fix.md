<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`type_graph.py`'s anonymous-enum `enum_underlying` fix dropped every
  declarator after the first** (G29 Phase 5 item 5, Codex review): one
  anonymous-enum declaration can introduce more than one declarator —
  `enum : U { A } first, second;` or `typedef enum : U { A } NameA,
  NameB;` — and clang emits the tag once followed by ALL of its
  declarators as siblings, each independently carrying the same id/marker
  linkage back to it (verified against real Clang 18 for both shapes).
  The earlier fix cleared its tracked pending anonymous enum immediately
  after the first matching declarator, so a second (or later) declarator
  of the same anonymous enum silently got no `enum_underlying` edge at
  all. Fixed by keeping the pending anonymous enum alive across a
  consecutive run of matching declarators, clearing it only when a
  sibling neither starts a new anonymous tag nor continues the current
  one.
