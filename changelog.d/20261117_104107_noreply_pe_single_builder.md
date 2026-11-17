### Changed

- **The header AST parser lives with its owner** — `header_ast_parser` moved from `dumper` to `extract/header_ast_backend.py`, next to the backend resolution it calls. `dumper._header_ast_parser` remains as the same object for existing callers. No behaviour change.
