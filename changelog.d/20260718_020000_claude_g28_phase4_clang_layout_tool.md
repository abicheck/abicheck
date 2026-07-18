<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **G28 Phase 4**: an optional companion tool
  (`tools/clang-layout-tool/`, built separately with LibTooling against a
  pinned LLVM release) that gives the direct-clang L2 backend
  (`--ast-frontend clang`) real compiled layout — field offsets, base
  offsets, and vtable-pointer placement — which `clang -ast-dump=json`
  never computes on its own. Fully opt-in via `ABICHECK_CLANG_LAYOUT_TOOL`
  pointing at the compiled binary; unset (the default), behavior is
  completely unchanged. See `tools/clang-layout-tool/README.md` for build
  instructions and `docs/development/plans/g28-castxml-clang-l2-parity-hardening.md`
  for the design writeup.
