### Changed

- **ADR-061 continuation**: 19 more root-level `abicheck/*.py` files are now
  classified into the `extract` responsibility layer in
  `architecture/modules.yaml` (up from 61 to 80 entries overall for that
  layer): `btf_metadata.py`, `castxml_policy.py`,
  `clang_layout_tool.py`, `classify.py`, `ctf_metadata.py`,
  `dwarf_presence.py`, `dwarf_snapshot.py`, `dwarf_unified.py`,
  `dwarf_utils.py`, `package.py`, `pdb_metadata.py`, `pdb_model.py`,
  `pdb_parser.py`, `pdb_utils.py`, `provenance.py`,
  `source_smoke.py`, `sycl_context.py`, `tu_fragment.py`, `type_metadata.py`.
  All are raw fact-extraction/toolchain-probe/format-parsing modules with no
  `compare`/`policy`/`report`-shaped concerns and no cross-layer imports
  outside `model`/`storage`/already-`extract`-classified siblings.
  (`binder.py`/`resolver.py` were originally classified `extract` in this
  same commit too — see the "Post-merge cross-PR collision found and fixed"
  paragraph below for why they were moved to `workflows` instead, which is
  why they're absent from both this list and the 80-entry count.)
  `type_metadata.py` was not on the original candidate list but was added
  alongside `btf_metadata.py`/`ctf_metadata.py` since it is their shared
  "unified `TypeMetadataSource` protocol for all debug format readers" leaf
  and cleanly fits the same role.

  **Closed two `frontends -> extract` violations the same way PR #907
  established** (route through `abicheck/workflows/extraction.py`, the
  existing facade its own docstring documents as "the sole operation owner"
  for the CLI-to-extract boundary), since six `cli_*.py` modules
  (`cli_compare_release.py`, `cli_compare_release_helpers.py`,
  `cli_datasources.py`, `cli_dump_helpers.py`, `cli_resolve.py`,
  `cli_scan.py`) directly imported `package.py`, `dwarf_snapshot.py`,
  `dwarf_unified.py`, `clang_layout_tool.py`, `provenance.py`, and
  `classify.py` at module or function-local scope. `workflows/extraction.py`
  gained re-exports for `PackageExtractor`, `_ELF_MAGIC`, `_ET_DYN`,
  `_has_interp_segment`, `_has_shared_object_name`, `_is_elf_shared_object`,
  `detect_extractor`, `discover_shared_libraries`, `is_package`,
  `apply_provenance`, `attach_clang_layout`, `is_supported_compare_input`,
  `parse_dwarf`, and `show_data_sources`, and every one of those six CLI
  files' import sites was routed through it instead of the origin module
  directly. `package.py`'s own `resolve_debug_info` collides by name with
  `debug_resolver.py`'s already-re-exported `resolve_debug_info`, so it is
  re-exported under `resolve_package_debug_info` and imported with a local
  `as resolve_debug_info` alias at the one call site that needs it
  (`cli_compare_release.py`), keeping both origins independently readable
  and avoiding a silent shadow.

  **Selection method, same discipline as PR #899/#903/#907**: every
  candidate's role and every one of its own local imports (direct, and via
  `_source_layer_for`/`_layer_for` resolution) was checked against what
  `extract`'s `may_import: [model, storage]` allows *and* against whether
  any already-`compare`- or `frontends`-classified module imports it
  directly, rather than tentatively classifying the whole candidate list
  and reacting only to whatever the checker reported — several candidates
  turned out to be heavily depended on by `compare`'s `diff_*.py` files,
  which cannot reach an `extract`-classified module through any facade
  (`compare`'s `may_import` is `[model]` only, with no workflows/facade
  exception the way `frontends` has). Concretely, the following are
  deliberately **not** classified in this pass:

  - `demangle.py`, `elf_symbol_filter.py`, `binary_fingerprint.py` -- each
    has zero local imports and would import-check cleanly as `extract`, but
    each is imported *directly* by several already-`compare`-classified
    `diff_*.py` detector modules (`diff_symbols.py`, `diff_filtering.py`,
    `diff_namespaces.py`, `diff_templates.py`, `diff_unnamed_types.py`,
    `diff_symbols_renames.py`, `diff_elf_layout.py`, `diff_long_double.py`,
    and siblings). Since `compare -> extract` is unconditionally forbidden
    with no facade route (unlike `frontends`, `compare`'s `may_import` is
    exactly `[model]`), classifying any of these three as `extract` breaks
    the build regardless of routing. Their actual shape -- zero imports,
    used identically by every layer including `model`-classified
    `name_classification.py` and `checker_types.py` -- suggests they belong
    to `model` instead, not `extract`; that reclassification is out of this
    pass's mandate and left for a dedicated `model`-layer pass.
  - `deadline.py` -- zero local imports, but its role (scan-wide deadline
    propagation and process-group-safe subprocess execution) is
    cross-cutting infra used by `workflows`-classified `service_scan.py`/
    `scan_engine.py` and `frontends`-classified `cli.py` alike, not a
    binary/header/build/source fact reader. `cli.py` imports the whole
    module object directly (`from . import ... deadline`), which is itself
    a `frontends -> extract` violation with no clean facade shape (a
    facade re-exporting a bare module reference, rather than a name, would
    be a first for this codebase's established pattern). Left unclassified;
    its natural home is arguably `model` (like the three above) given how
    broadly every layer depends on it.
  - `cc_wrapper.py` -- the `abicheck-cc` Flow-2 compiler-wrapper console
    script (ADR-035 D5). Its own imports (`buildsource.build_evidence`,
    `buildsource.source_abi`, both already-documented unclassified
    residuals per PR #903/#907) create no forbidden edge today since it
    stays at its legacy path, but its role is a genuine CLI-entry-point +
    fact-extraction hybrid (it has its own `click`-based `main`), not a
    clean fit for either `extract` alone or the `engine-cli-boundary`
    gate's already-allowlisted click-importing shapes. Left unclassified
    for a dedicated pass to decide rather than forced on a guess.
  - `annotations.py`, `annotations_step_summary.py`, `root_cause_evidence.py`
    -- each is report-shaped (GitHub Actions workflow-command annotations,
    a `reporter.py`/`reporter_markdown.py`-split root-cause-evidence
    helper), importing `checker`/`checker_policy`/`contract_gating`/
    `reporter_markdown` -- none of which read a fact, all of which decide
    what to show about one.
  - `config_paths.py` -- zero imports, but its role (enumerating the
    recognized `.abicheck.yml` discovery locations within one directory) is
    project-config discovery, not fact extraction, and it is imported
    directly by two already-`frontends`-classified modules
    (`cli_helpers_compare.py`, `cli_options.py`) -- forcing it to `extract`
    would need the same facade treatment as `package.py`/`provenance.py`
    above, but the role itself doesn't belong there regardless.
  - `environment_matrix.py`, `l0_export_delta.py` -- both genuinely
    `compare`-shaped (an `EnvironmentMatrix` config passed into `compare()`
    that imports `diff_versioning.py`; the ADR-049 Phase 5 L0 hard-removal
    fold shared by `compare`/`scan --against`'s baseline comparison, which
    diffs two symbol sets and emits `Change` objects) even though their own
    direct imports (`diff_versioning.py`, `checker_types.py`) happen not to
    trip the checker today.
  - `internal_leak.py` -- reachability/classification logic that imports
    `checker_policy`/`impact.engine` and constructs `Change` objects; a
    `compare`/`policy`-shaped module, not a fact reader.
  - `qualified_name_segments.py` -- a zero-import leaf, but its own
    docstring states it is "shared between `diff_namespaces.py` ... and any
    other detector" needing versioned-inline-namespace name matching -- a
    `compare`-shaped identity primitive (also consumed by `serialization.py`
    for the same reason), not extraction.
  - `serialization.py`, `snapshot_cache.py`, `snapshot_io.py` -- each is
    explicitly `storage`-shaped per this repo's own task-routing table
    ("Serialize snapshots/baselines, own their schemas/migrations, or
    manage caches" -> `storage/`) and per their own module-map
    descriptions; forcing them into `extract` would be the wrong layer
    regardless of import safety.
  - `tu_merge.py` -- imports `diff_symbols.py`, which is `compare`-classified
    (`from .diff_symbols import _is_cc_attribute`), so `extract -> compare`
    is a real, unconditional violation; unlike the facade-routable
    `frontends` violations above, `extract`'s own `may_import` cannot be
    widened to cover this without an ADR.

  Verified via `scripts/check_architecture.py` (0 errors, repo-wide,
  including the six `frontends -> extract` sites this pass's facade
  routing closes), `scripts/check_ai_readiness.py` (0 errors, 146
  warnings -- identical to the pre-change baseline; one pre-existing
  `CLI_CONTRACT_ALLOWLIST` line-pinned entry for `cli_dump_helpers.py`'s
  own already-allowlisted direct `dumper.dump` call was updated by one line
  number, since this pass's net import-line changes there shifted it),
  `mypy abicheck/` (the same pre-existing 17 `yaml`-stub errors,
  unaffected), `scripts/adr_status_sync.py` (clean), a targeted run across
  every touched module and CLI command (523 passed), and the full fast unit
  suite.

  One file (`cli_dump_helpers.py`) is `debt.yaml`-tracked against a
  no-growth line-count baseline; the initial facade-routed import wrapped
  to a multi-line form that grew it past that baseline purely from import
  formatting. Resolved the same way PR #907 did: rather than keep a
  freshly-wrapped local import block, the three added names
  (`detect_binary_format`, `normalize_binary_input`, `show_data_sources`)
  were folded onto that module's existing top-level `workflows.extraction`
  import (already used elsewhere in the same file), and the now-redundant
  function-local import block was removed entirely -- netting a net
  *reduction* in that file's line count relative to the PR base.

  **Post-merge cross-PR collision found and fixed (proactive merge
  simulation, not a review finding)**: `binder.py`/`resolver.py`'s
  `extract` classification above was individually import-safe against this
  PR's own base commit, but a real merge-order collision surfaces once the
  parallel `claude/adr061-report-classification` PR (which independently
  classifies `stack_report.py` as `report`) lands alongside it --
  `stack_report.py` imports `SymbolBinding`/`DependencyGraph` from
  `binder.py`/`resolver.py` purely for type hints on already-computed
  result objects it formats, and `report`'s `may_import` is
  `[compare, model, policy, workflows]`, with no `extract`. Neither PR's
  own isolated `check_architecture.py` run could see this, since each was
  checked against a base commit that didn't yet carry the other's
  classification. Caught by a deliberate sequential-merge simulation of
  all five parallel ADR-061 classification branches into one throwaway
  worktree.

  Re-reading `binder.py`/`resolver.py` against this repo's own task-routing
  table (`AGENTS.md`) shows `extract` was the wrong layer for them in the
  first place, independent of the collision: `resolver.py`'s own docstring
  is "Transitive ELF dependency resolution with loader-accurate search
  order" and `binder.py`'s is "Symbol binding simulation across a resolved
  dependency graph" -- both are **coordinating dependency behavior** over
  already-extracted ELF facts (`resolver.py` calls `elf_metadata.
  parse_elf_metadata`, then simulates the dynamic linker's own multi-step
  search algorithm; `binder.py` walks the resulting graph simulating
  symbol resolution), which is `workflows`'s own table entry ("Coordinate
  dump, compare, scan, release, aggregate, project, or **dependency
  behavior**"), not "read a binary ... fact" (`extract`'s entry). Moved
  both from `extract`'s to `workflows`'s `legacy_paths` -- safe for their
  own imports (`workflows`'s `may_import` includes `extract`, so
  `resolver.py`'s `elf_metadata` import and `binder.py`'s `resolver.py`
  import are both still permitted), and it resolves the collision, since
  `report -> workflows` is allowed. `debug_resolver.py`, the third file
  classified `extract` alongside these two in the same commit, stays
  `extract` -- it has no report-layer consumer and no comparable
  dependency-coordination shape (it resolves one binary's own debug-info
  artifact, not a graph of them).

  Verified via `scripts/check_architecture.py` (0 errors, this branch in
  isolation) and, more importantly, by re-running the full five-branch
  merge simulation with this fix applied (0 errors across the fully-merged
  state). No Python source changed -- this is a pure `architecture/
  modules.yaml` reclassification, so no test/mypy re-verification beyond
  the architecture gate was needed.
