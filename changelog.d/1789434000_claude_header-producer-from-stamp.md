### Changed

- Header-AST snapshots take their `castxml`/`clang` producer label from the frontend that the parser stamps at parse time, not from the parser's concrete class. Output is unchanged; the dump, manifest and contract tests pin the label to the backend registry key.
