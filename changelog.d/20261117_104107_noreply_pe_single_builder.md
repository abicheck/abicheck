### Changed

- **The header AST parser is imported from its owner** — `header_ast_parser` now lives in `extract/header_ast_backend.py`, next to the backend resolution it calls. The old `dumper._header_ast_parser` name is retired; callers import `header_ast_parser` from the owner. No behaviour change.
