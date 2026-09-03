### Changed

- **ADR-061 continuation**: 15 of the 23 `dumper*.py` header-AST/DWARF
  parsing-engine modules are now classified into the `extract`
  responsibility layer in `architecture/modules.yaml`:
  `dumper_ast_config.py`, `dumper_ast_config_cpp20.py`,
  `dumper_ast_config_cpp20_chains.py`,
  `dumper_castxml_probe.py`, `dumper_castxml_typedefs.py`,
  `dumper_clang_attributes.py`, `dumper_clang_errors.py`,
  `dumper_clang_qualifiers.py`, `dumper_clang_streaming.py`,
  `dumper_debug.py`,
  `dumper_elf_fallback.py`, `dumper_elf_symbols.py`,
  `dumper_layout_backfill.py`, `dumper_sysinc.py`,
  `dumper_toolchain.py` (`dumper_clang.py`/`dumper_scoping.py` were
  already classified by an earlier PR). Every one of these is genuine
  fact-extraction/config-resolution code (parses castxml XML or clang AST
  JSON, resolves compiler flags, decodes DWARF, streams/prunes AST output)
  with no `compare`-shaped construction of `Change`/`ChangeKind` objects.

  A 19th module, `dumper_cache.py`, was originally classified `extract`
  in this same commit and was moved to `storage` instead after a Codex
  review finding: it owns on-disk AST cache path management, atomic
  cache writes, and memoized-entry lifecycle (`store_cached_ast`/
  `load_cached_ast`/`_atomic_write`/`_cache_path`) — this repo's own
  task-routing table (`AGENTS.md`) is explicit that "serialize
  snapshots/baselines, own their schemas/migrations, or manage caches"
  is `storage`'s domain, not `extract`'s. Safe for its own imports
  (`storage`'s `may_import` is `[model]`, and `dumper_cache.py`'s only
  local import is the unclassified `deadline`) and safe for every
  existing consumer (`extract`-classified `dumper_clang_errors.py` and
  `workflows`-classified `workflows/extraction.py` both already permit
  `storage` in their own `may_import` lists, so reclassifying the
  callee only widens what's reachable, never narrows it).

  **Closed the `frontends -> extract` direct-import gap this classification
  opened**, the same way PR #907 did for `buildsource/`: three CLI/compat
  modules (`cli_dump_helpers.py`, `cli_dump_non_elf.py`, `compat/cli.py`,
  classified `frontends`) imported `dumper_cache.ast_memoize_scope`,
  `dumper_clang_streaming.suppress_streaming_prune`, and
  `dumper_contract._manifest_declared_includes` directly (one module-scope,
  one lazy, one function-local). `abicheck/workflows/extraction.py`
  (already the documented sole CLI-to-extract-engine facade) gained
  re-exports for all three names, and each call site now imports through
  it instead of the origin module.

  **Seven files deliberately left unclassified, each for a real,
  structural reason rather than an oversight:**

  - `dumper.py` itself — the module the whole cluster builds toward
    (`dump()`), imported directly (not through the facade) by
    `cli_dump_helpers.py`/`compat/cli.py` (both `frontends`) and, at
    function scope, by `probe_harness.py` (classified `compare`). The
    first two are routable through `workflows/extraction.py` the same way
    the three names above were, but `probe_harness.py`'s edge is not:
    `compare`'s `may_import` is `[model]` only, so a `compare`-classified
    module cannot reach `extract` through *any* facade under ADR-061's
    allowed-import table — closing it needs `probe_harness.py`'s own
    classification revisited first (it already reads as workflow-shaped —
    it drives a compile-and-snapshot pipeline — not `compare`-shaped), a
    separate, out-of-scope decision. Left unclassified with this reasoning
    recorded, per this ADR's established "leave it, document why" pattern
    (`template_graph.py`, PR #903).
  - `dumper_hybrid.py` — imports `diff_cxx_rules`/`diff_helpers` directly,
    both already classified `compare`. `extract`'s `may_import` is
    `[model, storage]`; `compare` is not reachable from `extract` at all,
    facade or not, so this is a structural block, not a missing
    re-export.
  - `dumper_castxml.py` — the castxml XML parser itself. Three
    `compare`-classified modules (`diff_symbols.py`,
    `diff_symbols_renames.py`, `diff_templates.py`) import its
    `is_synthetic_ctor_key`/`is_synthetic_dtor_key`/
    `SYNTHETIC_CTOR_KEY_PREFIX` constants directly, at both module and
    function scope — the identical `compare -> extract` structural
    block as above. A correct fix would move that shared vocabulary
    somewhere `compare` may import from (`model`), which is a real,
    separate data-model change to `dumper_castxml.py`'s own public
    surface, not a facade-routing fix.
  - `dumper_clang_expr.py` — imports `diff_cxx_rules.
    itanium_scope_components` directly, already classified `compare`. The
    identical structural `compare -> extract` block as `dumper_hybrid.py`
    and `dumper_castxml.py` above, not an oversight: confirmed via
    `scripts/check_architecture.py` that it stays correctly unclassified
    in `architecture/modules.yaml` (a Codex review finding on this same
    PR caught this changelog's own file count originally omitting it as
    the fourth exclusion, counting only three).
  - `dumper_clang_vtable.py` — imports `_SCOPE_NODE_KINDS` from the
    already-unclassified `dumper_clang_expr.py` above (`from
    .dumper_clang_expr import _SCOPE_NODE_KINDS`) — not a direct
    `compare`-classified import itself, but a real transitive
    `extract -> compare` dependency through `dumper_clang_expr.py`'s own
    `diff_cxx_rules` import. `scripts/check_architecture.py`'s
    dependency-direction check only flags an edge to a *classified*
    target, so it stayed silent on this one even while this file was
    classified `extract` — a genuine blind spot in the static checker
    (it never walks *through* an unclassified intermediary to see what
    that intermediary itself imports), caught by a Codex review finding
    on this same PR and confirmed by reading the import directly. Fixed
    by removing `dumper_clang_vtable.py` from `extract`'s classification
    too, matching its unclassified neighbor's own reasoning above.
  - `dumper_manifest.py` — imports `tu_merge.merge_fragments`; the
    still-unclassified `tu_merge.py` imports `_is_cc_attribute` from
    `compare`-classified `diff_symbols.py` (`from .diff_symbols import
    _is_cc_attribute as _is_cc_attribute`). The identical transitive-edge
    blind spot as `dumper_clang_vtable.py` above, caught by the same
    Codex review round and confirmed the same way (reading the import
    directly, then confirming `tu_merge.py` stays unclassified in
    `architecture/modules.yaml`). Fixed by removing `dumper_manifest.py`
    from `extract`'s classification too — no downstream consumer of
    `dumper_manifest.py` sits inside a physically-migrated package
    directory, so this removal alone needed no further plumbing.
  - `dumper_contract.py` — its `_attach_extraction_contract` function
    carries a lazy, function-local `from .comparability import (...,
    compute_extraction_contract, ...)` (only reached when a snapshot's
    profile fields are actually attached), and `comparability.py` is
    itself unclassified while owning `compare`-shaped extraction-contract
    comparison logic — the identical transitive `extract -> compare`
    blind spot as the two entries above, caught by the same Codex review
    round. Unclassifying it needed one more step the other two didn't:
    `abicheck/workflows/extraction.py` (already a physically-migrated
    `workflows`-layer module) directly imports
    `dumper_contract._manifest_declared_includes` as part of its own
    documented CLI-to-extract-engine facade role — once
    `dumper_contract.py` stopped being classified, that direct import
    tripped `check_architecture.py`'s separate `unclassified-import`
    check (a *migrated* source directory importing an unclassified flat
    module is itself an error, distinct from the transitive-edge blind
    spot this whole entry is about). Resolved by adding
    `abicheck.dumper_contract` to `architecture/modules.yaml`'s
    `public_root_surfaces` list — the mechanism
    `docs/contribute/adr/061-responsibility-package-architecture.md`
    already documents as the explicit, designed exemption for exactly
    this shape ("a migrated package legitimately needs to import this one
    specific unclassified flat module"), previously used only for
    `api_types.py`/`errors.py`. `check_architecture.py` stays 0 errors
    with this combination (`dumper_contract.py` unclassified,
    `abicheck.dumper_contract` allowlisted as a public root surface).

  Verified via `scripts/check_architecture.py` (0 errors, repo-wide),
  `scripts/check_ai_readiness.py` (0 errors, 146 warnings — unchanged
  from baseline; one `CLI_CONTRACT_ALLOWLIST` line-pin was updated for a
  shifted line number after the import edits, no allowlist entry added or
  removed), `mypy abicheck/` (clean, no new errors), `scripts/adr_status_sync.py`
  (clean), a targeted run across every `dumper*`-named test module plus
  `cli_dump_helpers`/`cli_scan`/`architecture` coverage, and the full fast
  unit suite.
