---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The L5 source graph's own node identities are never renumbered alongside the flat snapshot's closure markers (Codex review on PR #868, fresh evidence)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/storage/snapshot_load_normalization.py:123-138 docstring still says surface_graph node/edge identities are not renumbered. renumber_anonymous_closure_identities (storage/closure_identity.py) has no graph handling.

`renumber_anonymous_closure_identities` rewrites a
closure's `:<line>:<col>` discriminator to a stable `#N` ordinal across
`AbiSnapshot.functions`/`variables`/`types`/`enums`/`typedefs`/
`constants`/`fact_provenance` (`_LAMBDA_IDENTITY_FIELDS`), but
`service_header_graph_attach._attach_header_graph`'s embedded
`build_source.source_graph` is built by a genuinely separate clang
parse (`buildsource.header_graph`), whose node ids
(`graph_facts._decl_node_id`/`_type_node_id`) are derived directly from
the raw, un-renumbered identity string -- confirmed by reading both
functions, which apply no renumbering at all. A closure-parameterized
declaration therefore reads as `Foo<(lambda:file.h#1)>` in the flat
snapshot but `Foo<(lambda:file.h:20:5)>` in its own source-graph node, so any
consumer trying to correlate the two (e.g. matching a flat finding back
to its graph neighborhood) sees two different spellings for the same
entity. **Not fixed here**: a correct fix needs the ordinal map
`collect_anonymous_type_ordinals` computes from the flat fields to also
be applied to every graph node id/name *and* every edge's `src`/`dst`
reference to it -- and the source graph can name a closure the flat
ABI-surface fields never mention at all (an internal-linkage helper
visible only in the L5 graph), which the flat-only ordinal map has no
entry for, so naively reusing it risks leaving some graph-only closures
unrenumbered while their flat-visible siblings are. A correct fix likely
needs the ordinal collection widened to scan the graph's own node/edge
strings too, verified against a case that actually mixes flat-visible
and graph-only closures in one header -- a real, cross-cutting change
to two independently-evolving modules, not a same-PR reactive patch.

**Same gap, also reachable from the load path (Codex review, fresh
evidence).** `storage.snapshot_load_normalization.normalize_anonymous_
type_spellings_on_load` (added to close a sibling bug: a raw pre-strip
on-disk baseline was left completely unrenumbered on load, see this
file's own git history) rewrites the identical flat
`_LAMBDA_IDENTITY_FIELDS` only, called after `snapshot_from_dict` has
already decoded a schema-v29+ document's `AbiSnapshot.surface_graph` --
so a loaded raw-marker baseline's attached graph keeps its own
un-stripped node/edge identities even once the flat fields are
normalized. Not fixed here, for the same reason as above: it needs the
same graph-aware widening this entry already calls for, not a second,
independent patch on the load side.

- ~~Neither `scan`'s dry-run report validates `--abi3-floor` applicability before
reporting success~~ **Fixed (CLI cleanup phase two, PR 5 follow-up).**
Both `frontends.cli.scan_dry_run.render_scan_dry_run` (single-binary) and
`frontends.cli.artifact_set_dry_run.render_artifact_set_dry_run`
(`--artifact-set`) now run a cheap, binary-only extension probe
(`python_ext.detect_python_extension_from_binary` -- container-only ELF/
PE/Mach-O read, no DWARF/AST parse, the same "binary export table parse"
already priced under the L0_binary dry-run row) and route a non-qualifying
candidate through `DryRunResult.block()` (exit 1), matching the real run's
`EVIDENCE_CONTRACT_ERROR`. The message text is shared with
`scan_engine._run_abi3_audit`'s real precondition failure via
`python_ext.abi3_precondition_message()` so the three callers cannot
independently drift. See `tests/test_scan_dry_run_abi3.py`.

- ~~A pinned depth backed only by a query-declaring `--config` (no
`--sources`/`--build-info`) prices L3/L4/L5 at zero TUs/zero cost~~
**Fixed (CLI cleanup phase two, PR 5 follow-up).** `_estimate_total_tus`
gained a `query_only` branch: when `req.build_config` declares a real
`build.query` and no `--sources`/`--build-info`/compile DB is given, the
L3 note is flagged `[UNKNOWN: build.query declared, ...]` (the same
"annotate honestly rather than fold a floor into the summed total" shape
`_UNSCOPED_TU_NOTE_SUFFIX` already uses for the sibling `--build-target`
undercount case) instead of the confident-looking `0` every other
"nothing given" case reports. `_source_layer_estimates` carries the same
marker onto the derived `L4_source_abi`/`L5_source_graph` notes too
(Codex review, fresh
evidence: an earlier revision of this fix only flagged L3's own row, so
`--depth source`/`--depth graph` still priced the derived layers as a
confident zero), and `estimate_artifact_set`'s 4th return value
(`unknown_layers`) lets the `--artifact-set` aggregate renderer apply the
same per-layer treatment rather than a single project-wide flag hardcoded
to `L3_build`. Reaches both dry-run paths uniformly, since both call
`estimate_scan()`. See
`tests/test_scan_dry_run_abi3.py::test_estimate_total_tus_query_only_config_marks_count_unknown`
and its `test_estimate_scan_propagates_unknown_tu_count_to_l4_and_l5`/
`test_estimate_artifact_set_reports_unknown_layers_per_layer` siblings.
