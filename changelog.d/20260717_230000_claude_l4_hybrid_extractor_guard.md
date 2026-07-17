<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- `--ast-frontend hybrid` reached L4 source-ABI replay (`dump --sources`/
  `--build-info`) unchanged, since it is the same shared `--ast-frontend`
  flag used for L2 header-AST parsing — but L4 replay has no dual-backend
  hybrid extractor, only a single castxml-or-clang one per TU. It was
  silently running clang alone while recording `source_abi:hybrid` as if
  both backends had run. Now recorded as an explicit skipped extractor with
  a clear note instead, matching what the docs already said.
