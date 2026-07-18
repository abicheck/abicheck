<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- `service.run_dump`'s hybrid branch called `attach_clang_layout` a second
  time on the already-merged snapshot, even though its recursive
  `header_backend="clang"` sub-dump already got the same enrichment before
  the merge — a provably redundant extra invocation of the compiled G28
  Phase 4 layout tool (nothing left to backfill) on every hybrid dump with
  `ABICHECK_CLANG_LAYOUT_TOOL` enabled. Removed.
- The whole-snapshot dump cache's key only accounted for the layout tool's
  identity when the *statically resolved* backend was `"clang"`/`"hybrid"`,
  but a genuinely-unpinned `"auto"` request can silently runtime-fallback
  from castxml to clang (`dumper._header_ast_parser`'s G16 toolchain/
  `#error`-guard recovery) — invisible to that static resolution, which
  can't know in advance whether castxml will succeed for a given header set.
  That fallback's snapshot is clang-sourced and gets the same enrichment an
  explicit `--ast-frontend clang` dump would, so its cache key now also
  depends on the layout tool's identity for this case (an explicit
  `castxml` request/pin never triggers the fallback and is correctly
  excluded).

### Documentation

- Clarified that G28 Phase 3's (`--ast-frontend hybrid`) constructor/
  destructor identity reconciliation only runs within one hybrid dump
  invocation: comparing an existing plain-castxml JSON baseline (still keyed
  by the synthetic placeholder) against a fresh hybrid dump of the same,
  unchanged headers still reports the known false
  `FUNC_REMOVED`/`FUNC_ADDED` pair, since there is no cross-invocation
  reconciliation against an already-persisted pre-hybrid baseline.
- Updated `docs/reference/environment.md` and the G28 plan doc's Phase 4
  write-up, which still described `attach_clang_layout` as gated on
  `ast_producer == "clang"` only, missing the `hybrid` case added earlier
  this PR.
