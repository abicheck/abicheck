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
  comes from the pack that supplied the payload, and a layer whose facts are
  embedded can never advertise `not_collected` (a `partial` qualifier is
  still preserved). (2) `dump <binary> --build-info <abicheck_inputs pack>`
  (and the `--sources` pack form) ingested the Flow-2 pack through
  `_load_inputs_pack_or_raise` without forwarding the analyzed binary's L0
  exports, so the linked source surface reported `matched_symbols=0` and no
  `source_decl_to_binary_symbol` mapping — unlike the `--inputs` path, which
  already relinked. The embed path now seeds the ingest with the snapshot's
  exports, so the source declarations map onto the DSO's exported symbols.
