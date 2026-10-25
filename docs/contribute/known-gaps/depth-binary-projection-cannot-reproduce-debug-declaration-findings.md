---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `--depth binary` on a header-built snapshot misses debug-only declaration findings (2026-10-25)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-25): PARTIAL.** Residual of bug class
`evidence.depth_projection_diverges_from_shallow_dump`.

**Fixed 2026-10-25: no false clean.** When the projection discards a
header-built snapshot's signatures and that snapshot carries debug info, it
records the evidence it could not rebuild on the projected snapshot
(`AbiSnapshot.projection_evidence_gaps`, runtime-only, never stored;
`policy/depth_projection_gaps.py`). `analysis_assurance` then reads
`partial` and names "debug-info function/variable signatures and typedefs"
and "debug-info record relations" as not assessed at that depth
(`policy/analysis_assurance/_depth_projection.py`). The verdict and exit code
are unchanged. A binary-only snapshot, or a header snapshot without debug
info (where a binary-only dump has no such evidence either), is not flagged.
`tests/test_depth_projection_declaration_gap.py` pins return type,
parameter type, typedef, vtable and base changes against a binary-only-dump
oracle through the CLI. What follows is the residual: the findings
themselves are still not reported.

`policy/depth_projection.py` projects a header-built snapshot down to the
`binary` rung. It now gives the findings a binary-only dump of the same
libraries gives, as far as the stored snapshot allows.
`tests/test_depth_projection_matches_shallow_dump.py` checks this over every
buildable catalog case and names each remaining difference with its reason.
What remains is evidence the header-built snapshot never stored:

- **Debug-info subprogram and variable records.** A binary-only dump reads
  function and variable signatures, typedefs and template arguments from
  them. The header dump keeps the header's own spelling of these instead,
  and the projection discards it. So `func_params_changed`,
  `func_return_changed`, `param_pointer_level_changed`, `var_type_changed`,
  `typedef_base_changed` and `integer_model_changed` are not reported at
  `--depth binary` on a stored header snapshot. The verdict can read lower
  than the binary-only dump's. On the catalog, case02, case10, case33,
  case46, case112, case199 and case201 read `NO_CHANGE` against `BREAKING`.
- **Record relations.** The persisted debug layout (`DwarfMetadata.structs`)
  carries size, alignment and field offsets. It does not carry bases,
  vtable slots, member access or field cv-qualifiers. So `type_vtable_changed`,
  `type_base_changed`, `vptr_introduced`, `base_class_offset_changed` and
  `field_became_const` are missed. A base class's tail growth reads as
  `type_field_added_compatible` rather than as its derived classes' layout
  change.
- **Type scope.** The projection keeps the debug layouts of types the
  header surface made public and that are present on both sides. A
  binary-only dump scopes by debug-info reachability from the exported
  subprograms and reports a layout present on one side as `type_removed` or
  `type_added`. The projection does not, because debug info lists the types
  the compiled code uses, not the types a library declares.

Fix direction: persist the debug-info subprogram and record-relation facts
the binary-only extractor reads (`extract/dwarf_records.py`) alongside the
layout channel in a header dump. That is a new persisted fact: it needs an
`EXTRACTOR_GENERATION` bump and a storage decision. Then the projection can
rebuild declarations from them, as it rebuilds layout records today
(`compare/debug_layout_view.debug_layout_declarations`). Do not fall back to
the header's own signatures at `binary`: that is the header evidence the
ceiling exists to remove.
