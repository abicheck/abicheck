### Added

- **The L2 header-only semantic graph now carries per-node provenance for a
  `--ast-frontend hybrid` dump.** A hybrid merge unifies which backend
  (castxml/clang) contributed each declaration's facts, but the graph node
  built from it never recorded which backend actually saw the declaration in
  the first place. `dumper_hybrid.merge_snapshots` now stamps a
  `"visibility"`-named `fact_provenance` entry per merged function/variable
  (`"castxml"` for a castxml-primary entry, `"clang"` for a clang-only
  one), and `buildsource.header_graph.build_header_only_graph` reads it back
  to add an additive `attrs["visibility_provenance"]` on the graph's
  `source_decl` nodes — pure enrichment, never changing an existing attr's
  meaning or present on a non-hybrid/unrecorded declaration. No detector
  reads it yet; it's the scoped piece of G31 Phase C's "hybrid-backend
  provenance-tagged graph merging" item that was previously only a design
  sketch.
