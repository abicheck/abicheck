### Added

- **Virtual-dispatch graph (G29 Phase 5 item 3)**: a new
  `abicheck/buildsource/virtual_dispatch_graph.py` pass populates
  `VIRTUAL_CALL_MAY_DISPATCH_TO` (joins a virtual call's already-resolved
  base-method target against every `METHOD_POSSIBLE_OVERRIDE` candidate,
  always `resolution: "overapprox"`) and `TYPE_HAS_VTABLE` (a new `vtable`
  node per genuinely polymorphic class, per the Itanium ABI rule) on the
  optional L5 source graph. Unlike its sibling passes, it shells out to no
  compiler at all — both edges are pure transforms over already-folded
  `call_graph`/`type_graph`/`override_graph` state, driven by
  `inline_graph_fold.fold_virtual_dispatch_graph` right after
  `fold_override_graph`. `DECL_OVERRIDES_DECL` is registered vocabulary with
  no producer — already satisfied by `override_graph.py`'s existing
  `METHOD_POSSIBLE_OVERRIDE` edges (`resolution == "override_confirmed"`);
  `VTABLE_SLOT_MAPS_TO_DECL` (a precise per-slot vtable layout) stays
  reserved, deliberately deferred. See
  `docs/reference/source-graph-schema.md` for the schema detail.
