---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# m1360 performance round: the asks not taken, and why (2026-09-24)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Lists the m1360 performance asks deliberately not taken, with reasons (scan rewrite, union vocabulary, loc strip, forked child needs an ADR). Kept so they are not re-derived.


The m1360 analysis (577d856a4 → ff29268fc, oneDAL castxml/clang legs) listed
ten upstream asks. Seven landed in the PR that added this entry: compacted
ASCII clang AST cache entries (`storage/json_compact.py`), AST-intake trace
boundaries plus a `VmHWM` field on every sample, the `/proc/*/stat` fallback
for `tree_processes`, the `find_by_value_types` leaf hoist, the single-name
demangle cache read, the template-names index shared with the defaults
builder, and the note that a castxml run still builds its header graph from
a clang AST (`docs/learn/graph-coverage.md`). The same PR fixed the
constants/typedefs dependency-scoping defect (`extract/
flat_map_dependency_scope.py`). The remaining asks, recorded so they are
not re-derived:

- **One stdlib-reference scan for both `exclude_export_only_roots`
  variants** (4 scans, 6-9 s). Not a pure memo: the two callers also differ
  in `committed_roots` and in full-scan vs early-exit, and the export-only
  exclusion changes which records the closure walk reaches, not only which
  seeds it starts from. Serving both from one scan needs a walk that carries
  two provenance labels per reached record through the typedef-alias
  provenance tiers — a rewrite of `_StdlibReferenceScan`, not a cache. The
  cheap special case (no `EXPORT_ONLY` declaration in the snapshot, so both
  seed sets coincide) does not apply to the measured library, which carries
  ~10k export-only functions.
- **"Share the 3 fixed reachability regexes across snapshots"** (8
  compiles, 4-5 s). The patterns are not fixed: `_StdlibReferenceScan`
  compiles each from the snapshot's own spelling vocabulary, and
  `VOCABULARY_CACHE` already serves a repeated vocabulary (the two flag
  variants of one snapshot hit it). The misses are OLD and NEW having
  different vocabularies. Sharing needs a comparison-scoped union
  vocabulary with per-snapshot filtering of matches. That is exact —
  `finditer_allow_nested` enumerates every valid candidate at every offset,
  so filtering a superset's matches to a subset vocabulary yields exactly
  the subset's matches — but it needs the scan to be constructed with both
  sides' vocabularies, which today it never sees. Worth doing together with
  the item above, since both restructure the same scan.
- **Keep-list strip of `loc`/`range` at store time** (another 64.8% of the
  compacted document). Not done: the header-graph call-graph pass reads
  `file`/`id` from those objects (`service_header_graph_attach`,
  `parse_clang_ast_calls`), and clang's location encoding is *sticky* (a
  `file` is stated once and inherited by following nodes;
  `extract/headers/clang/locations.py`), so dropping a subtree's `loc`
  silently re-attributes every later node. A correct strip must first
  materialise locations, which means parsing — not a streaming re-encode.
- **A derived form for the primary clang pass**, like the header-graph
  projection sidecar. The primary pass *is* the declaration extractor
  (`_ClangAstParser`'s eleven `parse_*` passes over id-keyed whole-document
  indexes), so its "derived form" is the snapshot itself, which the
  whole-snapshot disk cache already stores. A projection narrower than the
  AST but wider than the snapshot is the streaming-parse design (gap e),
  blocked on those parser indexes.
- **Confining each side's dump to a forked child** (measured −66% post-run
  residency at 45 roots, because the plateau is pymalloc arena
  fragmentation, not retention). A real design option — there is no
  process isolation anywhere in the tree today — but it changes how
  results, caches, memos and trace state cross a process boundary, and it
  needs its own ADR.

**Transition effect of the scoping fix.** A snapshot dumped after this
change drops dependency-header typedefs the kept declarations do not name
(482 libstdc++ internals on a small `std::vector`/`std::map` library). A
comparison against a baseline dumped *before* it lists them as
`typedef_removed`; under the default public-header scoping they are
filtered as non-public, stay visible in the disposition list, and do not
move the gate. Regenerate a stored baseline to remove the noise.
