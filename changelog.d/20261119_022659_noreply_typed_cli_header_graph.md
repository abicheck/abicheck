<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Dump format detection and public scope have one owner each** — magic-byte format detection moves to `extract/binary_format.py` (`detect_format`, driven by the handler registry), and the effective public header set of a dump moves to `workflows/dump/default_header_graph.py` (`effective_public_scope`). No behaviour change; `dumper.py` shrinks from 1041 to 1026 lines.
