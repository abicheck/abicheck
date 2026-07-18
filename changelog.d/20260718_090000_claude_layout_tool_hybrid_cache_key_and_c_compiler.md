<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- The whole-snapshot dump cache's key only folded in the G28 Phase 4 layout
  tool's identity for a `"clang"`-resolved backend, but `service.run_dump`'s
  `"hybrid"` branch recurses into its own `header_backend="clang"` sub-dump
  (which gets the same `attach_clang_layout` enrichment) before
  `merge_snapshots` folds any clang-only declarations — carrying their layout
  facts — into the merged result. A hybrid dump's cache entry created before
  enabling/changing `ABICHECK_CLANG_LAYOUT_TOOL` could be silently reused
  afterward too. Now included for `"hybrid"` as well as `"clang"`.
- `attach_clang_layout` always ran the layout tool with the `c++` driver,
  regardless of `--lang c`, while the main clang dump this enrichment layers
  on top of already resolves `cc`/`clang` for a C dump
  (`cli_dump_helpers.perform_elf_dump` / `service._attach_header_graph`'s own
  `"cc" if lang == "c" else "c++"` convention). On a C-only toolchain with no
  `clang++` at all, this second pass silently failed to resolve any driver,
  losing every C struct's layout enrichment even though the main dump
  succeeded. Now mirrors the same convention.
