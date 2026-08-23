### Performance

- **New, opt-in streaming pruner for the direct-clang L2 backend**
  (`abicheck/dumper_clang_streaming.py`, wired into
  `dumper_clang_errors._parse_clang_ast_result`): a `clang -ast-dump=json`
  parse can now collapse a free `FunctionDecl`/`VarDecl` node into a tiny
  placeholder the instant its own subtree completes, whenever it is
  confidently and entirely confined to a toolchain/dependency header (the
  same `is_dependency_header` rule `dumper_scoping.py`'s existing post-hoc
  filter already applies to functions/variables unconditionally) — reducing
  retained Python object count and the subsequent `_ClangAstParser`
  model-construction walk for a header pulling in heavy STL/template
  machinery. Every C++ method-shaped kind (`CXXMethodDecl`/
  `CXXConstructorDecl`/`CXXDestructorDecl`/`CXXConversionDecl`) is
  deliberately **never** pruned, unlike a free function or variable: a
  dependency base class's own methods can feed
  `dumper_clang_vtable.build_vtable`'s base-lookup recursion for a
  project-owned derived class's vtable, so pruning one could corrupt a
  retained class's reconstructed vtable rather than just drop a
  declaration. Never touches a record/enum/typedef node either (those can
  be retained via `dumper_scoping.py`'s "directly referenced" carve-out,
  which is only decidable once the whole snapshot's public surface is
  known), nor a node whose output the always-on semantic header graph
  (`service._attach_header_graph`) will also consume.
  **Off by default**, enabled only via `ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS=1`
  — matching this codebase's existing convention for a real, tested, but
  not-yet-default-wired performance path (`ABICHECK_CLANG_LAYOUT_TOOL`,
  `ABICHECK_PARALLEL_EXTRACTION`). Measured on synthetic repros (see the new
  module's docstring and `docs/contribute/plans/
  libclang-selective-ast-traversal.md`'s problem statement for the numbers):
  under clang's delta/"sticky" location encoding, most nodes deep inside a
  large dependency-header subtree inherit their file from an un-observable
  (from a bottom-up `json.load` hook) ambient context rather than carrying
  it explicitly, so this conservative, correctness-first cut prunes a real
  but modest fraction of content, and — because Python's `object_pairs_hook`
  forces extra per-object allocation and a Python-level callback for
  *every* JSON object in the document, not just the pruned ones — measured
  wall time on a single-header repro was *worse* than the unpruned baseline
  despite the real reduction in retained functions/nodes. Documented
  explicitly as a narrow, evidence-backed building block rather than a
  general performance fix; the new plan doc uses this same measurement to
  motivate investigating a libclang-bindings-based alternative that could
  avoid the Python-level per-object cost entirely. A manifest (`--dump-manifest`)
  dump now computes its pruning root set once, as the union across *every*
  translation unit's own `forced_includes`/`project_owned` includes, rather
  than each TU only seeing its own -- otherwise a declaration reachable
  under a root only a *different*, non-contributing TU declares ownership
  of could be misclassified as a dependency and permanently pruned. All
  three remaining direct `dumper.dump()` callers with no dependency-scope
  wrapper of their own (the ABICC-compat CLI's `compat dump`/descriptor
  paths, and `appcompat.check_appcompat`'s two dumps) now also suppress the
  pruner for the same "full/unscoped surface, no post-hoc filter to retain
  what's pruned" reason `cli_dump_helpers.py`'s choke points already do.
  The placeholder now also retains the pruned declaration's own clang `id`
  (previously dropped): a sibling declaration's default expression can
  reference a pruned dependency `VarDecl`/`FunctionDecl` via a compact
  `referencedDecl` stub carrying only that id and a bare name, and
  `dumper_clang_expr._index_decl_id_qualified_names` resolves it back to a
  scope-qualified name by walking the whole (possibly pruned) root --
  dropping the id would have silently reintroduced the exact
  `a::VALUE`/`b::VALUE` bare-name collision that index exists to prevent.
  A manifest (`--dump-manifest`) dump no longer opens the in-process AST
  memoization scope for its primary parse: that scope only exists to hand
  off an AST to the always-on header-graph attach, which is guaranteed to
  no-op for a manifest dump (its own per-TU header lists are mutually
  exclusive with `-H`) -- opening it anyway was silently vetoing the
  pruner for a manifest's own TU parses whenever they shared a thread with
  the primary call (a single TU, or `ABICHECK_TU_JOBS=1`), protecting a
  memo nothing would ever read.
