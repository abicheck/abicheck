### Fixed

- **The source graph spells a lambda closure as the rest of the snapshot does** — closure markers in the L5/surface graph's node ids, labels, edges and attributes are now renumbered to the same stable `#N` ordinal as the snapshot's declarations, both when a snapshot is dumped and when a stored one is loaded (on the graph's first use, so loading stays lazy). A closure only the graph names is numbered after the declarations' closures. A baseline written before this fix whose graph still spells `:line:col` is numbered as graph-only.
