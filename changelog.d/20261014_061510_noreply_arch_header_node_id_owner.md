<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **One owner for the `header://` node id** — `model.graph_identity.header_node_id` is now the single constructor for a header node id. The L5 source graph and the public-surface graph previously each kept a private copy that had to stay byte-identical to join. The id strings are unchanged; internal `_header_node_id` callers in `buildsource/` moved to the public name.
