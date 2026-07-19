<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Build-source coverage provenance and Flow-2 export linking** (triage
  AC-002, AC-003): two independent honesty gaps when folding build/source
  facts into a snapshot. (1) `_combine_packs` rebuilt each managed layer's
  coverage row from the *first* input pack in supplier order that carried a
  row for that layer, which need not be the pack that actually supplied the
  layer's payload — a combined pack could embed 63 L3 compile units yet
  report `L3_build: not_collected` (or, cross-pack, attach one pack's stale
  `not_collected` L4 row to another pack's real `source_abi`). The row now
  comes from the pack that supplied the payload and is honored as that pack
  recorded it, so a real payload keeps its `present`/`partial` row while a
  non-`None` but *empty* placeholder (e.g. an explicit empty
  `compile_commands.json`, which yields an empty `BuildEvidence`) keeps its
  honest `not_collected` row rather than being reported as build context with
  no compile units behind it. (2) `dump <binary> --build-info <abicheck_inputs pack>`
  (and the `--sources` pack form) ingested the Flow-2 pack through
  `_load_inputs_pack_or_raise` without forwarding the analyzed binary's L0
  exports, so the linked source surface reported `matched_symbols=0` and no
  `source_decl_to_binary_symbol` mapping — unlike the `--inputs` path, which
  already relinked. Both the `dump`/embed path and the `compare` side-pack path
  (`_resolve_side_pack`, for `--old/new-build-info`/`--old/new-sources` inputs
  packs) now seed the ingest with the snapshot's exports, so the source
  declarations map onto the DSO's exported symbols.
- **Build/source layer selection and honesty** (triage AC-001, AC-006,
  AC-007): three further fixes to how build/source evidence is chosen and
  reported. (AC-001) An explicit raw `--sources` cold scan now supplies L4/L5,
  beating a pre-baked Flow-2 pack passed via `--build-info` for its L3 —
  `embed_build_source` routes the raw-sources inline collection into
  `_combine_packs`'s sources slot (which outranks `--build-info` for L4/L5),
  while a `--build-info`-only run still falls back to the pack's L4/L5. The
  `compare` side-pack precedence (an explicit `--build-info` pack still
  overrides a snapshot's embedded L4/L5) is unchanged. (AC-006) The L5 source-graph
  coverage row now reports `partial` whenever a call/type pass is *degraded*
  (its live replay never completed and only structural/plugin edges were
  folded), instead of reading `present` just because those edges made the graph
  non-empty — the missing passes are named in the coverage detail. (AC-007) An
  explicit `--depth build`/`source` with a real compile database supplied via
  `-p`/`--compile-db` (for the L2 header parse) but no dedicated `--build-info`
  now reuses that same `compile_commands.json` as the L3 build source, instead
  of ignoring it for L3 and re-running a build-system query.
