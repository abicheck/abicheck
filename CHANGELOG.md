# Changelog

All notable changes to abicheck are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

### Performance

- **DWARF-only dumps reuse one open `DWARFInfo` across all extraction passes.**
  The basic-metadata, advanced-metadata, and snapshot-builder passes previously
  opened the ELF and built a `DWARFInfo` independently, so a large C++ library's
  DIEs were parsed multiple times from cold caches (the F5b finding from the pvxs
  validation). A shared `DwarfSession` now threads one open handle through all
  three passes; the snapshot walk hits the DIE cache the metadata passes warmed
  instead of re-parsing. Output is byte-for-byte identical (validated A/B on real
  compiled binaries); the redundant snapshot DIE walk drops from a full cold
  parse to near-free on the measured fixtures. Internal only — no CLI or API
  surface change. `tests/test_perf_dwarf_session_scaling.py` (new,
  `integration`) guards the win going forward: a same-binary A/B timing
  comparison (session reuse must stay reliably faster than independent opens)
  and a CU-count scaling exponent gate on the production `dwarf_only` dump
  path, compiling multi-CU C++ binaries that reproduce the pvxs
  repeated-type-across-CUs pattern.

### Added

- **L5 source graph now populates type/reference dependency edges
  (ADR-041 P0).** New `abicheck/buildsource/type_graph.py` folds
  `TYPE_INHERITS` (base classes), `TYPE_HAS_FIELD_TYPE` (field types),
  `DECL_HAS_TYPE` (parameter types), and `DECL_REFERENCES_DECL` (non-call
  variable/enum references) into the L5 graph alongside the existing call
  graph, whenever a semantic source mode runs with `clang++` available
  (`--depth source`+). These edge kinds were reserved in the schema since
  ADR-031 and already read by `public_to_internal_dependency`
  (`crosscheck.py`), but nothing populated them — so that check now catches
  cases the call graph alone misses, e.g. a public struct with a private
  field type or a public class inheriting an internal base. New
  `coverage.type_edges` / `coverage.reference_edges` counters on the L5
  graph report collection honestly.

- **Version-over-version internal-dependency finding now spans the full
  dependency-edge family, not calls alone (ADR-041 P0 slice 2).** The L5
  semantic graph diff's `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` check
  (`diff_source_graph_findings`) previously only walked `DECL_CALLS_DECL`
  edges to find a public entry newly reaching an internal declaration —
  missing the `TYPE_HAS_FIELD_TYPE`/`TYPE_INHERITS`/`DECL_HAS_TYPE`/
  `DECL_REFERENCES_DECL` edges the P0 slice-1 type graph started producing.
  A public struct that gains a private field type or base class, or a public
  function that gains a private parameter type or a reference to an internal
  constant, is now caught between two versions exactly like a newly-added
  internal call already was. `crosscheck.py`'s intra-version
  `public_to_internal_dependency` check already covered all five edge kinds;
  both checks now share one `source_graph.DEPENDENCY_EDGE_KINDS` constant so
  they cannot drift apart again. No new `ChangeKind` — this only broadens the
  recall of the existing `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` finding. The
  closure is restricted to dependency edge kinds actually collected on *both*
  graphs (`_common_dependency_edge_kinds`), so a collector improvement — e.g.
  the type-graph pass running for the first time on one side — cannot make a
  pre-existing, unchanged dependency look newly added. Coverage is judged per
  extractor-pass family (`_DEPENDENCY_EDGE_FAMILIES`: `call_graph.py`'s single
  kind vs. `type_graph.py`'s four, folded together by one AST pass), not per
  exact edge kind — otherwise a genuinely new dependency of a kind with no
  prior edges (but whose sibling kind from the same pass already exists on
  both sides) would be dropped too. New `SourceGraphSummary.extractor_passes`
  field (additive, no schema version bump) records that a pass ran to
  completion independent of edge count — closing the residual gap where a
  pass genuinely finds zero edges of its whole family on one side (e.g. no
  struct anywhere yet had a private field), which edge presence alone cannot
  distinguish from "the pass never ran". Falls back to edge-presence inference
  when the flag is absent (older packs, hand-built graphs). Internal-target
  classification now requires *positive* internal provenance (explicit
  `private_header`/`source` visibility, or project-file provenance plus a
  non-system-looking name) instead of merely "not declared by a public
  header" — the latter also matched a third-party/stdlib type used as a new
  field/parameter type, which is not declared by any project header either but
  is not internal. `DECL_NODE_KINDS`/`PUBLIC_VISIBILITIES`/
  `INTERNAL_VISIBILITIES`/`UNANNOTATED_VISIBILITIES`/`looks_like_system_name`/
  `is_public_dependency_node`/`is_internal_dependency_node` now live in
  `source_graph.py` as the shared source of truth; `crosscheck.py`'s
  matching definitions are now aliases onto them instead of an independent
  copy. The out-of-band `abicheck collect --call-graph` path
  (`cli_buildsource_helpers._collect_call_graph`) now also records
  `extractor_passes["call_graph"]`, matching the inline `dump --sources` path
  fixed above — a version diff over two *collected* packs benefits from the
  zero-edge coverage fix too, not just inline dumps.

- **`graph explain` proof paths for the two dependency-reachability findings
  (ADR-041 P0 slice 3).** `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` and
  `CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED` previously asserted a fact
  ("public entry X now reaches internal Y", "N → M known static callees")
  with no concrete edge chain proving it. New `source_graph._dependency_path`
  (BFS witness-path reconstruction) + `_format_dependency_path` thread a
  human-readable chain — e.g. `pub() --[DECL_CALLS_DECL]--> helper()
  --[DECL_HAS_TYPE]--> detail::Impl` — into each finding's description; the
  intra-version `PUBLIC_TO_INTERNAL_DEPENDENCY` cross-check (already a single
  edge) now also names the connecting edge kind. Appended to the existing
  `description` text, not a new `Change` field. Also fixes a regression the
  prior slice's family-widening had reintroduced: widening credit from one
  present edge kind to its whole family is now conditional on **both** sides
  *confirming* `extractor_passes[pass_name]`, not merely inferred from edge
  presence — a Kythe-ingested pack (`graph_backends.ingest_kythe_entries`)
  only ever produces `DECL_REFERENCES_DECL`, never the Clang type graph's
  other three kinds, so a lone Kythe ref edge was wrongly granting blanket
  coverage credit to base-class/field-type/parameter-type checks it never ran.
  Two more fixes from a sixth review: (1) `extractor_passes` is no longer
  stamped for a *narrowed* run (a changed-path/`--since` scan or an unseeded
  run scoped to L4's `headers-only` selection) — only a pass that examined
  the whole compile DB may claim confirmed coverage, since a scoped pass's
  "found nothing" says nothing about the rest of the codebase; (2)
  `_public_types()` now requires the type's own `visibility` attr to be
  public, not just "declared by a `header`-kind node" (every declaring file,
  public or private, gets a `header` node) — otherwise a private type could
  be treated as a dependency-closure entry and a private type gaining its own
  new private field/base could wrongly emit `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`
  with no public API involved.

- **`compare --profile` run profiles (ADR-040 Lever 3).** A single `--profile`
  flag bundles common workflow defaults so you don't retype them: `ci-gate`
  (`--depth headers --scope-public-headers --format review --exit-code-scheme
  severity`), `release` (`--depth full --scope-public-headers --format markdown
  --recommend`), and `quick` (`--depth binary --stat`). Explicit flags always
  override the profile. First step of the ADR-040 plan to reduce `compare`'s
  flag surface from 79 toward the ADR-037 ~20 target.

### Changed

- **Breaking — side-aware `--header`/`--include`/`--sources`/`--build-info`
  (ADR-040 Lever 1, Phase B).** The per-side `--old-header`/`--new-header`,
  `--old-include`/`--new-include`, `--old-sources`/`--new-sources` and
  `--old-build-info`/`--new-build-info` flags are removed; each concept is now a
  single repeatable flag that takes an optional `old=`/`new=` value prefix:
  `compare a.so b.so --header old=v1/foo.h --header new=v2/foo.h`. A bare value
  (or `-H`/`-I`) still applies to both sides. This collapses `compare`'s visible
  flag count by 6 (and `appcompat`'s by 4). The GitHub Action's per-side
  `old-header`/`new-header`/`old-include`/`new-include` inputs are unchanged —
  they now map to the side-aware flags internally.

- **Breaking — side-aware `--pdb-path`/`--debug-root`/`--probe-matrix`/
  `--debug-info`/`--devel-pkg`/`--version` (ADR-040 Lever 1, Phase C).**
  Continuing the collapse, the remaining per-side pairs fold into single
  repeatable flags with the same `old=`/`new=` value prefix: `--old-pdb-path`/
  `--new-pdb-path` → `--pdb-path`, `--debug-root1`/`--debug-root2` →
  `--debug-root`, `--probe-matrix-old`/`--probe-matrix-new` → `--probe-matrix`,
  `--debug-info1`/`--debug-info2` → `--debug-info`, `--devel-pkg1`/`--devel-pkg2`
  → `--devel-pkg`, and `--old-version`/`--new-version` → `--version` (a string
  flag whose per-side defaults stay `old`/`new`). Lowers `compare`'s visible
  flag count by a further 5. The `--ast-frontend` triple is intentionally kept
  (its base flag is shared with `dump`/`scan`). The GitHub Action's per-side
  inputs are unchanged — they map to the side-aware flags internally.

- **Debug-resolution + `--show-redundant` demoted to `.abicheck.yml` (ADR-040
  Lever 2).** A new `debug:` config block carries `format`, `dwarf_only`,
  `debuginfod`, and `debuginfod_url`; `scope.show_redundant` carries the
  redundancy-filter toggle. The corresponding CLI flags (`--debug-format`,
  `--debuginfod`, `--debuginfod-url`, `--dwarf-only`, `--show-redundant`) are now
  hidden but still function as per-run overrides (`CLI > config > default`, the
  same cadence as the severity family), lowering `compare`'s visible flag count
  by 5. The boolean toggles are two-way (`--dwarf-only/--no-dwarf-only`,
  `--debuginfod/--no-debuginfod`, `--show-redundant/--no-show-redundant`) so a
  one-off run can force `false` over a config `true`. The coarse `--debug-root` stays a visible override. The toolchain family
  (`--gcc-*`/`--sysroot`/`--nostdinc`) is intentionally **not** demoted — it is
  the shared `compare`/`dump`/`scan` L2 compile-context surface — and
  `--scope-public-headers` stays visible as the everyday scoping on/off switch.

- **`compare` flag budget is now derived from a documented ledger.** The
  `COMPARE_FLAG_BUDGET` ceiling is computed as `BASE + len(COMPARE_FLAG_BUDGET_RAISES)`,
  so a new visible flag cannot be added without a rationale entry — closing the
  gap that previously let `--post-manifest` land undocumented by silently
  consuming budget slack. Backfills the missing `--post-manifest` rationale.

- **`scan --baseline`/`--estimate` extracted to `cli_scan_baseline.py`.**
  Internal refactor: `cli_scan.py` dropped from 1898 to 1483 lines (clearing the
  1500-line soft cap) with no behaviour change; historical import paths are
  preserved.

### Removed

- **Deprecated `scan-mode` / `source-method` GitHub Action inputs.** They mapped
  to the CLI's deprecated `--mode`/`--source-method` and `scan-mode` defaulted to
  `pr`, so every scan run emitted a deprecation warning. Removed from `action.yml`
  and `action/run.sh`; use the `depth` input (omit it for `auto`). The CLI flags
  themselves are unchanged.

### Fixed

- **`scan`/`dump --sources` now seeds L2 header includes from the build.** A
  zero-config source scan whose public headers `#include` a dependency's headers
  (e.g. EPICS pvxs headers pulling in `<epicsTime.h>`) hard-failed the L2 parse
  with `fatal error: … file not found`, because the aggregate-header parse only
  searched the user's `-I` — even though the discovered compile database (used by
  the L4 replay) already knew the dirs. When no explicit `-I` is given, abicheck
  now derives the include-dir union from that compile DB and feeds it to the L2
  parse (best-effort fallback; explicit `-I` still wins, failures degrade to the
  old behaviour). Providing `--sources` alone now parses headers that reach into
  a dependency SDK.

- **Quadratic slowdown comparing large C++ libraries.** The internal-leak walk
  (`internal_leak._resolve_type_name`) scanned the entire type map on every BFS
  node to canonicalize unqualified type names, making a compare of a
  thousands-of-types surface (e.g. EPICS pvxs `libpvxs`) hang for minutes at
  99 % CPU. A precomputed final-`::`-segment suffix index makes the lookup O(1);
  the affected compare drops from > 340 s to ~70 s with identical results.

- **False `exported_object_alignment_reduced` on RTTI symbols.** The alignment
  detector reported spurious "alignment reduced" changes on `_ZTV/_ZTI/_ZTS/_ZTT`
  (vtable / typeinfo / VTT) symbols, whose `st_value` alignment is a
  linker-placement artifact — 21 false findings on a single clean pvxs patch
  release. These prefixes are now exempted, matching the symbol-size detector.

- **"castxml not found" error now names the clang fallback.** On a clang-only
  host, header (`-H`) scans hard-failed with an install-castxml message that
  never mentioned `--ast-frontend clang` / `ABICHECK_AST_FRONTEND=clang`. The
  error now points to the clang JSON-AST backend (with its layout-evidence
  caveat) as an alternative.

- **Misleading "evidence layer not collected" warning.** `dump` warned
  *"supply --build-info/--compile-db or install clang/castxml"* even when the
  layer's extractor had run — e.g. an L4 replay that parsed TUs but linked 0
  symbols, or an unseeded `--depth source` that selected 0 TUs. The warning now
  distinguishes a genuinely **absent** layer (→ supply a compile DB / install the
  frontend) from one that **ran but linked nothing** (→ points at the coverage
  rows: public-header-roots mismatch, use `--max`/`--changed-path`/`--since`, or a
  snapshot/source mismatch). No more "install the tools you already have."

- **Action installs `bear`.** `action/install-deps.sh` now installs `bear` (and
  notes `bear -- make`) so Make/Autotools projects that don't emit a
  `compile_commands.json` get authoritative L3/L4 instead of reduced-confidence
  `make -n` scraping.

- **GitHub Action `scan` mode passed the wrong config flag.** `action/run.sh`
  forwarded the `build-config` input as `--build-config` in `scan` mode, but
  `abicheck scan` only accepts `--config` (as `dump` already used) — so setting
  `build-config` on a `scan` step hard-failed with exit 64
  (`No such option '--build-config'`). Now passes `--config`.

- **`abicheck dump <binary> --inputs ./abicheck_inputs/`** folds a build-emitted
  Flow-2 pack (from the `abicheck-cc` wrapper or the Clang facts plugin) straight
  into the artifact snapshot and links the source surface against the binary's
  exports — the same result as a follow-up `abicheck merge`, in **one command**.
  Removes the separate `merge` step for the common single-artifact plugin/wrapper
  flow (`merge` remains for multi-input folds). No compiler frontend is re-run.
- **External-linkage variables in the clang source backend + Clang plugin.**
  The clang backend and `contrib/abicheck-clang-plugin` previously emitted an
  empty `variables` list, so exported data symbols (namespace globals, static
  data members such as `llvm::raw_ostream::RED`) could never map to a source
  declaration. Both now emit `variable` entities keyed identically
  (`id=_hash("variable", mangled|name, type)`), gated to external linkage:
  block-scope locals and internal-linkage variables are dropped so a header
  constant never inflates `decls_without_symbol` or triggers a spurious
  `source_binary_provenance_mismatch`: a C++ namespace/file-scope `static`, a
  namespace-scope `const` without `extern`, or an anonymous-namespace variable by
  their Itanium mangled linkage encoding (the `L` seniority marker or a
  `_GLOBAL__N_` component), a C / `extern "C"` file-scope `static` (which clang
  gives no mangled name) by an explicit storage-class filter, and an
  MSVC / clang-cl namespace-scope top-level `const` without `extern` (whose
  `?…` mangling carries no Itanium marker) by a type-based fallback; a `static`
  data member stays external. `constexpr` keeps its own path. The
  `CLANG_EXTRACTOR_VERSION` bump (0.5→0.6) invalidates stale `--cache-dir` dumps
  that predate the `variables` field. The C.6
  differential-conformance fixture gains globals, a static member, and an
  internal `const` so the gate covers variables. Measured on LLVM 18.1.3
  `LLVMSupport`: +24 exported symbols now map.
- **Template-instantiation RTTI attribution.** `source_link` now attributes an
  exported vtable/typeinfo emitted for a template instantiation
  (`_ZTVN…format_object<char>…E`) to the captured class-*template* pattern when
  one is on the public surface, closing the largest source of unmatched
  synthesized exports. Gated on a genuine `template` entity so the
  exact-specialization guard (only `A<int>` present ⇒ `A<char>` stays an
  orphan) is preserved. Measured on `LLVMSupport`: unmatched exports 818 → 747,
  vtable/typeinfo orphans 85 → 14.

### Changed

- **`merge` / `dump --inputs`: fold identical facts once (perf).** A per-TU Flow-2
  pack re-emits each public-header decl once per compile — a ~20× blow-up on
  template-heavy libraries — and `link_source_abi` kept every copy, so the linked
  surface, its content-hash `json.dumps`, and the relink all scaled with the
  duplication. The linker now folds byte-identical entities on a full-identity key
  (name + mangled + all `*_hash` fields), so overloads and genuine ODR variants
  stay split while true duplicates collapse. Measured on LLVM 18.1.3 `LLVMSupport`
  (174-TU pack): fold time **~120 s → ~16 s**, linked surface **130 512 → 6 599**
  declarations, embedded baseline **362 MB → 35 MB**, with byte-identical symbol
  mapping (1656/2613).
- **Clang plugin: prune the AST-dump JSON parse (perf).** The plugin hashes AST
  subtrees by dumping them to clang's JSON and canonicalizing; it now parses
  that JSON keeping only the ~11 hash-relevant keys and skipping the rest
  (delegating kept leaves to `llvm::json::parse`, so every emitted hash is
  byte-identical). On a from-scratch LLVM `LLVMSupport`+`LLVMDemangle` build the
  plugin's compile-time overhead drops from **3.44× → ~2.1×** (parse phase −68%
  on template-heavy TUs); C.6 conformance stays green and a
  constexpr-string/nested-template stress case in the fixture guards the
  parser's escape/skip paths. `ABICHECK_PLUGIN_PROFILE=1` prints the per-TU
  dump/parse/canonicalize split.

- **Example catalog: five new cases (`case165`–`case169`)** giving five
  previously example-less `ChangeKind`s a dedicated, compilable fixture:
  `polymorphic_type_non_virtual_dtor` (new polymorphic factory type without a
  virtual destructor, ADR-027 anti-pattern), `func_ref_qual_changed` (`str()`
  → `str() &` renames the mangled symbol), `base_class_virtual_changed`
  (non-virtual base becomes a virtual base), `func_virtual_removed`
  (devirtualized method leaves the vtable while its symbol survives) and
  `overload_added` (a new overload silently re-routes recompiled call sites).
  `ground_truth.json` entries may now set `"pattern_verdicts": true` to
  validate a case under the opt-in `--pattern-verdicts` analysis mode.
- **Toolchain / runtime environment drift (binutils & glibc skew — 6 new
  `ChangeKind`s).** Rebuilding the same source on a newer distro/toolchain can
  change where the binary loads without touching its interface; these findings
  name that root cause. New concepts doc:
  `docs/concepts/environment-drift.md`; Markdown reports gain an
  **Environment & Toolchain Drift** section grouping these (plus the existing
  toolchain/stdlib drift kinds) so "the API moved" and "the build environment
  moved" are separable at a glance.
  - **`runtime_floor_raised`** (RISK) — per provider-library/version-prefix
    roll-up of `symbol_version_required_added`: one headline finding naming
    the old → new deployment floor (e.g. `GLIBC_2.28 → GLIBC_2.34`) and the
    imported symbols that pulled it up (`__libc_start_main@GLIBC_2.34` alone
    means a pure relink artifact; a real API symbol means new runtime use).
  - **`--env-matrix` / `EnvironmentMatrix.runtime_floors`** — declare target
    runtime floors (`runtime_floors: {GLIBC: "2.28"}`) and version-requirement
    findings become decidable: at/below the floor → COMPATIBLE, above it →
    BREAKING, undeclared prefixes keep the RISK default (per-finding
    `effective_verdict` modulation, rule `runtime_floor_contract`). Also
    settles `dt_relr_introduced` via its implied glibc ≥ 2.36 requirement.
    Available on the Python API (`compare(..., env_matrix=...)`,
    `CompareRequest.env_matrix_path`).
  - **`dt_relr_introduced`** (RISK) / **`dt_relr_removed`** (COMPATIBLE) —
    packed relative relocations (`-z pack-relative-relocs`, binutils ≥ 2.38
    distro default) require glibc ≥ 2.36 to load. The synthetic
    `GLIBC_ABI_DT_RELR` verneed marker folds into this finding instead of
    surfacing as a cryptic unparseable version requirement.
  - **`rpath_type_changed`** (RISK) — `DT_RPATH` ↔ `DT_RUNPATH` flip
    (`--enable-new-dtags` drift): same paths, different lookup semantics
    (dependency subtree vs direct deps; `LD_LIBRARY_PATH` precedence). A pure
    type flip replaces the `rpath_changed`+`runpath_changed` noise pair.
  - **`hash_style_removed`** (RISK) — a symbol hash-table style (`.hash`
    SysV / `.gnu.hash` GNU) present in the old binary was dropped
    (`--hash-style` drift); loaders supporting only that style break.
  - **`time64_abi_changed`** (BREAKING) — 32-bit time64/LFS flip:
    `time_t`/`off_t`-family typedefs resized together (`_TIME_BITS=64` /
    `_FILE_OFFSET_BITS=64`, glibc ≥ 2.34) — one root-cause diagnostic for the
    mass per-symbol width churn, mirroring the ILP64 collapse detector.
  - `ElfMetadata` now captures `has_dt_relr` and `hash_styles`
    (serialization-compatible; detectors gate off on legacy snapshots so a
    stale baseline never fabricates a finding).
  - **Catalog case 170** (`examples/case170_env_runtime_floor_raised`) — a
    committed snapshot pair encoding the relink-on-newer-distro scenario
    (same interface, `__libc_start_main` rebinds `GLIBC_2.28` → `GLIBC_2.34`),
    with `env-newer.yaml`/`env-older.yaml` matrices showing the floor contract
    settling the verdict both ways. Validated compiler-free by
    `tests/test_environment_drift.py`.
- **Platform-coverage extension (24 new `ChangeKind`s, total 342).** Closes the
  parsed-but-never-diffed and small-parser gaps found in the checker-coverage
  review, across four fronts:
  - **Dynamic loader / import surface (ELF, PE):** `imported_symbol_added` /
    `imported_symbol_removed` (undefined-symbol set; on PE also per-DLL
    imported-function drift incl. import-by-ordinal), `interpreter_changed`
    (PT_INTERP), `bind_now_disabled`, `dynamic_loading_flags_changed`
    (DF_1_NODELETE/NOOPEN/ORIGIN), `elf_init_fini_changed`,
    `exported_object_alignment_reduced` (copy-relocation hazard), and
    `allocator_replacement_added`/`_removed` (global `operator new`/`delete`
    exports). PE delay-load imports are now parsed and diffed as dependencies.
  - **Platform identity & deployment floors:** `elf_endianness_changed`
    (EI_DATA), `x86_isa_baseline_raised` (GNU_PROPERTY_X86_ISA_1_NEEDED,
    x86-64-v2/v3/v4), `os_deployment_floor_raised` (Mach-O minos, PE subsystem
    version, ELF NT_GNU_ABI_TAG kernel floor), `pe_hardening_weakened` /
    `pe_hardening_improved` (DllCharacteristics DEP/ASLR/HIGH_ENTROPY_VA/CFG —
    the first PE hardening diff), `library_version_downgraded`,
    `macho_filetype_changed`, `macho_linkage_flags_changed`, and
    `macho_reexport_changed`. Mach-O now also parses LC_RPATH (feeds
    `rpath_changed`), decodes arm64e cpusubtypes, walks the dyld export trie
    (trie-only exports + weak/re-export flags), and reports strong↔weak export
    flips via the existing binding kinds.
  - **Language contracts (header tier):** `func_variadic_added`/`_removed`
    (C ellipsis), `func_contract_attribute_added`/`_removed` (nonnull,
    noreturn, format, alloc_size, malloc, warn_unused_result, …; a
    calling-convention attribute flip routes to `calling_convention_changed`),
    `func_exception_spec_changed` (dynamic `throw(...)`), and
    `var_alignment_changed` (`alignas` on exported variables). A persisting
    virtual method whose `vtable_index` moved now emits `type_vtable_changed`.
  - **MSVC/PDB & kernel type info:** PDB method calling conventions
    (LF_ONEMETHOD → LF_MFUNCTION CV_call_e) are wired into
    `calling_convention_changed`; raw BTF/CTF blob snapshots now bridge
    function prototypes and typedef targets into the signature/typedef
    detectors instead of dropping them.

- **G23 Phase D — ecosystem detectors (7 new `ChangeKind`s).**
  - **kABI (`Module.symvers`) diff** — pass two kernel `Module.symvers`
    manifests to `compare` (recognized by filename or content) to get
    `kabi_symbol_removed` / `kabi_crc_changed` / `kabi_symbol_namespace_changed`
    (BREAKING), `kabi_export_type_changed` (API_BREAK) and `kabi_symbol_added`
    (COMPATIBLE). The export-type check compares only the GPL *license class*
    and flags the restricting direction (`EXPORT_SYMBOL` → `EXPORT_SYMBOL_GPL`,
    which locks out proprietary modules); the relaxing direction and a
    namespace-only change (`EXPORT_SYMBOL` → `EXPORT_SYMBOL_NS`) are not flagged
    as export-type changes. The 5-field (namespace) and 4-field pre-5.4 formats
    are both parsed.
  - **`long_double_abi_changed`** (BREAKING) — a `long double` representation
    migration (ppc64 IBM ↔ IEEE128, or `__float128`) re-paired from a
    removed↔added symbol pair via their demangled types; collapses the redundant
    add/remove into one finding. The same-mangling case
    (`-mlong-double-64`/`-mabi=ibmlongdouble`, which keeps the symbol name)
    is caught from the DWARF `long double` base-type byte size when debug info
    is present on both sides.
  - **`unnamed_type_in_public_abi`** (RISK) — a newly-exported symbol embeds a
    lambda closure (`Ul…E_`) or unnamed struct/enum (`Ut…_`), whose mangling is
    compiler-ordering-fragile. Lambda closures are detected from the mangled
    `Ul…E[<n>]_` token directly, so detection is stable across platform
    demanglers, and the check requires the baseline to have captured an ELF
    symbol table so a pre-existing leak is never mistaken for a new one.
- **G23-A4 refinement** — a release that newly gains `STB_GNU_UNIQUE` exports
  (e.g. first enables `-fgnu-unique`) now reports `symbol_binding_became_unique`
  once at the library level, catching the dlclose-inhibition risk that the
  both-sides transition detector missed for added symbols. Skipped when the
  baseline captured no symbol table (unknown, not proven-absent).

- **G23 Phase B2 — L1 DWARF vtable-group reconstruction (2 new `ChangeKind`s,
  both BREAKING).** Reconstructs per-class vtable-group structure from DWARF
  inheritance and reports two breaks the per-type field/base diff cannot see:
  - `secondary_vtable_group_changed` — a direct or virtual base gained or lost
    virtual functions, so it started/stopped owning a *secondary* vtable group
    in a derived class whose own base declaration list is unchanged (a
    cross-type effect). Reserved for that case — a moved base is still reported
    by `base_class_position_changed` / `type_base_changed`.
  - `virtual_base_offset_changed` — a same-set reorder of virtual bases shifts
    the virtual-base offset table; invisible to the non-virtual
    `base_class_position_changed` check. Reconstruction is tri-state guarded: an
    indeterminate base (absent on that side) emits nothing rather than guessing.

- **G23 Phase B1 — Itanium multi-inheritance vtable machinery (3 new
  `ChangeKind`s, all L0/binary-only).** Recovered from `.dynsym` thunk and VTT
  symbol names + sizes — no DWARF or headers, works on fully stripped binaries:
  - `vtable_thunk_offset_changed` (BREAKING) — a virtual-override thunk's
    `this`-adjustment offset shifted (a secondary base subobject moved). Catches
    the multi-inheritance base-reorder break that the primary-vtable
    `vtable_slot_count_changed` misses because the `_ZTV` size is unchanged.
  - `vtable_thunk_set_changed` (BREAKING) — a persisting method gained/lost a
    vtable thunk (a secondary-base virtual override was added/removed).
  - `vtt_slot_count_changed` (BREAKING) — a class's VTT (`_ZTT`) size changed
    (virtual-base construction scaffolding changed).
  - Virtual-override thunks (`_ZTh`/`_ZTv`/`_ZTc`) are now excluded from the
    generic exported-function surface (`is_abi_relevant_elf_symbol`), so a
    thunk-offset shift no longer also surfaces as a spurious
    `func_added`/`func_removed`/`func_likely_renamed`.

- **G23 Phase A — Linux ELF artifact-fact detectors (12 new `ChangeKind`s).**
  ELF metadata capture and diff rules for facts readable from the binary alone
  (L0), with no DWARF or headers required:
  - **Static-TLS drift** (`static_tls_introduced` → RISK, `static_tls_removed`
    → COMPATIBLE): `DF_STATIC_TLS` adoption makes a library un-`dlopen`-able.
    Reported only when the library actually participates in TLS (defined or
    imported `STT_TLS`).
  - **Control-flow-integrity drift** (`cet_protection_weakened` /
    `branch_protection_weakened` → RISK, `_improved` counterparts → COMPATIBLE):
    x86 CET (IBT/SHSTK) and AArch64 branch protection (BTI/PAC) decoded from
    `.note.gnu.property`.
  - **ELF identity / ABI-flags guard** (`elf_machine_changed`,
    `elf_class_changed`, `elf_abi_flags_changed` → BREAKING;
    `elf_osabi_changed` → RISK): the ELF-side counterpart to
    `pe_machine_changed` / `macho_cpu_type_changed`, including decoded per-arch
    float-ABI/EABI `e_flags` bits (ARM/RISC-V/MIPS).
  - **GNU-unique binding transitions** (`symbol_binding_became_unique` /
    `symbol_binding_lost_unique` → RISK): `STB_GNU_UNIQUE` inhibits `dlclose()`
    and carries a process-wide ODR-uniqueness guarantee.
  - The shipped `security` policy now gates `cet_protection_weakened`,
    `branch_protection_weakened`, and `static_tls_introduced` to break.

- **G23 Phase C — clang toolchain-flag drift robustness.** `toolchain_flag_drift`
  reads ABI-relevant compiler flags from `DW_AT_producer`; clang records them
  only under `-grecord-command-line`. The producer scan now unions the recorded
  `abi_flags`/`vector_abi_flags` across *all* compilation units instead of
  keeping only the first CU's, so a flag recorded on a non-first TU (common with
  clang) is no longer dropped. No new `ChangeKind`s.
- **CPython extension-module `abi3` / Limited-API support (G14).** abicheck now
  recognises CPython extension modules — built with Cython, pybind11, nanobind,
  or hand-written C — and checks the contract the export table cannot see: the
  CPython C-API symbols the module *imports* from libpython, plus its
  stable-ABI (`abi3` / `Py_LIMITED_API`) conformance. `abicheck scan --binary
  ext.so --abi3 3.9` audits a single module against a target `Py_LIMITED_API`
  floor; the violations are advisory by default and gate CI when promoted with
  `--crosscheck python_stable_abi_violation=error`. `compare` gains a
  deployment-`RISK` change kind for
  `abi3` builds — `python_stable_abi_violation` (a new import outside the stable
  ABI, e.g. an internal `_Py*` symbol). Classification uses a vendored,
  authoritative copy of CPython's `Misc/stable_abi.toml` (≈970 symbols), so the
  `abi_only` `_Py*` symbols the Limited-API macros expand to (`_Py_Dealloc`,
  `_PyObject_GC_New`, `_PyArg_*_SizeT`, `_Py_NoneStruct`, …) are correctly
  treated as stable rather than flagged, while `PyUnstable_*` (PEP 689) is
  flagged. `compare` also raises `python_abi3_dropped` when a module that was an
  `abi3` build becomes version-specific (dropping every other interpreter it
  used to support). Interpreter-*floor* conformance is checked
  by `scan --abi3` (where the user supplies the floor), not at compare time,
  since a bare `.abi3.so` carries no declared floor to judge against. Version-specific (`cpython-3XX`) modules are deliberately not
  subject to the stable-ABI checks, so a normal per-interpreter extension never
  false-positives. **Free-threaded (PEP 703, `Py_GIL_DISABLED`) builds** are
  recognised from the `t` SOABI marker (`cpython-3XXt` / `cp3XXt`), correctly
  treated as version-specific (never `abi3`, since `Py_LIMITED_API` is
  incompatible with `Py_GIL_DISABLED`), and `compare` raises
  `python_gil_abi_changed` when a module crosses the GIL/no-GIL boundary between
  builds, and `python_abi3_floor_raised` when both builds carry an explicit
  `cpXY-abi3` tag but the new declared floor is higher (`cp39-abi3` → `cp310-abi3`
  drops CPython 3.9 users) — exact, read from the tag on both sides, no
  min-of-imports inference. Cross-platform (ELF/PE imports, plus new Mach-O
  undefined-symbol capture; on Windows a version-specific `pythonXY.dll` import
  under `abi3` is flagged). See the
  [Python Extensions](docs/user-guide/python-extensions.md) guide.
- **`post_manifest` library — POST Python ABI-commitment checking.**
  [POST Python](https://post-py.org/) compiles a typed subset of Python to a
  shared library whose stable C ABI is the set of `pp_*` symbols documented in a
  versioned JSON export manifest (`post-py build --emit-manifest`). New
  `abicheck/post_manifest.py` provides a tolerant manifest parser and three
  checks against POST's commitments: manifest↔binary validation (every promised
  `pp_*`/ufunc-loop symbol is exported; ELF/PE/COFF/Mach-O), a compiler-
  independent manifest↔manifest ABI diff (using the manifest's dtypes, which a
  stripped binary lacks), and a `post_abi` version-bump gate.
- **`compare --post-manifest <manifest.json>`.** Scope a binary comparison to a
  POST manifest's committed ABI surface: only changes to the manifest's
  `pp_*`/ufunc-loop symbols count toward the verdict, while private `__pp_*`
  kernel churn and other non-committed exports are demoted to the filtered
  ledger (`--show-filtered`). Type-level and internal-leak findings are always
  kept (scoping never hides a break). Plumbed through `CompareRequest`, the
  Tier-2 service, and the post-processing pipeline as `public_surface_allowlist`.

- **Python-level API diffing for extension modules (G23).** Complementing the
  G14 native-C-ABI check, abicheck now recovers the **Python-visible API** a
  CPython extension exposes to `import` — its top-level functions, classes,
  methods, and their signatures (parameter names, kinds, defaults, and type
  annotations) — and diffs two versions of it. The surface is recovered
  **statically** from a sibling PEP 484 `.pyi` type stub (parsed with `ast`,
  never imported or executed); the stub is discovered next to the binary
  automatically and attached in both `dump` and `compare`, so a single
  `compare` surfaces both native-ABI and Python-API changes. Two builds can be
  byte-for-byte C-ABI-identical yet break every caller (a renamed keyword
  argument, a dropped default) — that break lives in the Python signatures, not
  the export table, and is now caught. Fifteen `python_api_*` change kinds are
  emitted from an order-, kind-, and protocol-aware signature diff (not a
  name-set diff): `python_api_function_removed` / `_class_removed` /
  `_method_removed`, `python_api_parameter_removed` / `_added` (new required
  parameter) / `_renamed`, `python_api_default_removed`,
  `python_api_parameter_kind_changed` (a binding/order change —
  positional↔keyword-only, keyword→positional-only, or a positional
  reorder/insertion), `python_api_callable_kind_changed` (`def`↔`async def`, or
  method↔`property`/`staticmethod`/`classmethod`), and
  `python_api_overload_removed` (a dropped `@overload` variant) — all
  `API_BREAK`; `python_api_parameter_type_changed`
  and `python_api_return_type_changed` (`RISK`); and the corresponding
  `*_function_added` / `_class_added` / `_method_added` additions (`COMPATIBLE`).
  Adding an optional parameter, a default, or an annotation is backward
  compatible and not reported; `self`/`cls` and private (leading-underscore)
  names are excluded. The surface is recovered only for a **recognised**
  extension (a `PyInit_*` export), so a plain native library with an unrelated
  `.pyi` sibling is never mis-attributed a Python API. When no stub ships the
  check degrades honestly (surface absent) rather than false-negating.
  The recovered surface also acts as a **public-contract oracle** that removes
  native false positives: because an extension exports only `PyInit_`, its other
  exported C/C++ symbols and internal type layout are not part of any `import`
  consumer's contract, so native API-content findings on them are demoted to the
  audit ledger (`off-python-surface`) instead of driving the verdict — while
  `python_api_*` and the native load-contract findings
  (`python_stable_abi_violation` / `python_abi3_dropped` /
  `python_gil_abi_changed` / `python_abi3_floor_raised`) are never demoted
  (authority rule), load/linkage/security findings are kept, and a resolved C
  header surface takes precedence. This is measured as a first-class evidence
  layer (a `python-api` axis in the FP-rate gate and an L2-only signal in the
  per-tier accuracy gate). See the
  [Python Extensions](docs/user-guide/python-extensions.md#beyond-the-c-abi-the-python-level-api)
  guide.

### Changed

- **`merge` L4 coverage line now reports full accounting.** The stderr summary
  gained an `accounted/unmatched` clause — `L4_source_abi: present (471/834
  symbols matched, 834/834 accounted, 0 unmatched)` — so 100 % symbol accounting
  is visible. The bare `matched/exported` ratio undersold coverage (it counts
  only direct decl matches; RTTI/vtable/thunk are attributed and stdlib/internal
  are classified separately).

### Fixed

- **castxml frontend: ref-qualifiers and virtual destructors recovered.**
  Released castxml versions emit neither a `refqual` attribute on `<Method>`
  nor a `mangled` attribute on `<Destructor>`. The &/&& ref-qualifier is now
  derived from the Itanium mangling (so `func_ref_qual_changed` can fire on
  the default AST frontend), and virtual destructors are kept in the
  reconstructed vtable via a `~Name` fallback entry — previously every
  polymorphic type looked destructor-less to the
  `polymorphic_type_non_virtual_dtor` anti-pattern, producing false positives
  on safe classes. The destructor-slot matcher also recognises GCC's unified
  `D4`/`D5` clones recorded in DWARF linkage names.

- **Python overload required→optional widening no longer a false break.**
  `_overload_covers` compared required parameter shapes with exact equality, so
  an `@overload` that keeps a parameter but adds a default
  (`def f(x: int)` → `def f(x: int = ...)`) moved `x` from the required to the
  optional shape and was mis-reported as `python_api_overload_removed` /
  API_BREAK — even though every old call is still accepted. Coverage now uses an
  order-preserving "widened subsequence" check, so a required→optional widening
  is treated as compatible while a newly *required* parameter or a reordering of
  retained required parameters still counts as a removal.
- **Stdlib RTTI/guard classification.** Exported `typeinfo`/`vtable`/`guard
  variable` symbols for *nested* std types (`std::__detail::_AnyMatcher<…>`, …)
  demangle as `"typeinfo for std::…"`, so the `startswith("std::")` origin test
  missed them and they fell into the generic
  `cpp_export_without_public_source_decl` bucket instead of `dependency:stdlib`.
  `_is_stdlib_export`/`_is_tbb_export` now strip the RTTI/guard descriptor before
  the origin check (with nested-std mangled prefixes as a demangler-free
  fallback). Accounting totals are unchanged (both are non-public); only the
  reason label is now correct.

### Documentation

- **Producing source facts — wiring Flow B into a real build.** The
  `producing-source-facts.md` guide gained a make/CMake injection recipe for the
  `abicheck-cc` wrapper, an `ABICHECK_CC_EXTRACTOR` table (with the clang-only
  host note), a caveat that extraction concurrency is bound by the build's
  `-jN` (not `ABICHECK_L4_JOBS`), and a "reading the L4 coverage line" section
  distinguishing `matched` from `accounted`/`unmatched`.

### Added

- **14 new build/source-only `ChangeKind`s** for ABI/API failures no artifact
  layer can observe (enum: 254 → 268). L3 build-context flag flips
  (`enum_size_flag_changed`, `struct_packing_mode_changed`, `lto_mode_changed`,
  `char_signedness_changed`, `whole_program_vtables_mode_changed`,
  `sanitizer_mode_changed`, `float_abi_changed`, `stdlib_debug_mode_changed`),
  L4 source-replay findings (`public_macro_removed`, `inline_function_removed`,
  `public_typedef_removed`), and L5 version-over-version source-graph deltas
  (`public_api_internal_dependency_added`, `target_dependency_added`,
  `exported_symbol_source_owner_changed`). All default to `API_BREAK`/`RISK`
  and are never `BREAKING` on their own (ADR-028 D3). The four flag-flip kinds
  added last are covered by unit tests rather than separate example cases —
  they are structurally identical to cases 152–155.
- **Example cases 152–161** demonstrating each new kind. They ship hand-built
  evidence-model fixture pairs (`old.json`/`new.json`) instead of compiled
  binaries and are validated compiler-free (`scripts/gen_l3l4l5_examples.py`,
  `tests/test_l3l4l5_examples.py`).

### Documentation

- **Explained *what each evidence layer buys* for accuracy** — a new
  "What each layer buys: fewer false negatives *and* fewer false positives"
  section in `concepts/evidence-and-detectability.md` with the tracked per-tier
  (L0–L3) matrix, plus a "layering principle" callout in the ABI/API handling
  guide. Makes explicit that adding a layer cuts *both* error kinds (not a
  trade-off), why L1 transiently introduces false positives that L2 scoping
  removes, and how L4/L5 extend the same story to source-only breaks no artifact
  tier can see.

### Fixed

- **Docs: the `scan --depth` ladder is now stated identically everywhere**
  (`binary|headers|build|source|full`, no user-facing `graph` rung per
  ADR-037 D6) — `concepts/scan-and-evidence-levels.md` listed a contradictory
  rung set, the "cheap gate" worked example pinned the wrong rung for a
  compiler-free scan, and two MCP tool docstrings carried the stale ladder.
- **Docs: one evidence-layer story across the funnel** — the landing page
  still pitched "three-layer analysis" while other pages teach the
  five-source L0–L4 model; pages teaching L0–L4 now also forward-reference
  the derived L5 code the scan docs use.
- **Docs: `mcp-integration.md` covers all seven MCP tools** — `abi_audit`,
  `abi_estimate`, and `abi_scan` were undocumented, and the
  `--timeout`/`--max-file-size` scoping undercounted which tools they bound.
- **Docs: README exit-code table no longer misstates exit `1`** (it exists
  only under the severity scheme) and notes that exit `8` requires
  `--fail-on-removed-library`.

- **Source→binary symbol matching now recovers ctor/dtor ABI clones.** The
  linker (`link_source_abi` / `relink_surface_exports`) folds C++ Itanium
  ctor/dtor clone tags (`C1`/`C2`/`C3`/`C4`, `D0`/`D1`/`D2`/`D4`) so one source
  ctor/dtor declaration claims every clone the compiler exports, instead of
  orphaning the siblings as "exported symbol with no source decl". Also bridges
  GCC's non-standard unified `C4`/`D4` DWARF linkage tag to the real `C1`/`C2`
  exports.
- **`merge` no longer truncates the binary export set.**
  `_exported_symbols_from_snapshot` unions the authoritative platform dynamic
  export table (`elf.symbols` / `pe.exports` / `macho.exports`) instead of only
  the DWARF-shaped `functions[].mangled` view. On a pvxs Flow-C merge this lifts
  `matched_symbols` from 6 to 287 (`exported_symbols` 126 → 950).

- **Source→binary matching normalizes Mach-O spellings for all names.** The
  linker normalizes the optional Mach-O leading underscore (`__ZN…` → `_ZN…`)
  for every Itanium source/export key, not only ctor/dtor canonical keys, so an
  ordinary C++ method the plugin records as `__ZN1A3fooEv` exact-matches the
  export table's `_ZN1A3fooEv` instead of orphaning into the unmatched sets.
- **`merge` excludes non-default ELF version aliases from the relink set.** A
  symbol that exists only as `foo@VER` (no default `foo@@VER`) can't be linked
  against by an unversioned consumer, so it no longer enters the export set —
  preventing the two-way reconciliation from masking a real `public_not_exported`.

### Changed

- **Clang facts plugin fails loud on a misconfigured `public-roots`** (ADR-038
  Flow C, Caveat A): it now emits a diagnostic (and records it in the pack's
  `diagnostics`) when `public-roots` matches zero declarations though header
  decls were seen outside the roots, instead of silently producing an empty
  pack with exit 0.
- **Clang facts plugin auto-derives `public-roots` when omitted** (ADR-038 Flow
  C): with no explicit `public-roots=`, roots are inferred from the compile's
  `-I`/`-iquote` include dirs (compiler/system entries excluded) and a one-time
  inference note is emitted, so a forgotten flag yields a populated surface
  rather than a silently empty pack. An explicit `public-roots=` still scopes
  the surface precisely.

### Added

- **Per-evidence-tier accuracy gate** (`scripts/check_tier_accuracy.py`, mirrored
  in `tests/test_tier_accuracy_gate.py`, wired into CI with a step-summary
  matrix) — measures *what each evidence level buys*. One labelled logical
  change per case is projected down to what each tier observes (L0 symbols → L1
  debug → L2 headers → L3 build) and run through `compare`; verdicts collapse to
  a 3-band ordinal (non-breaking / risk / breaking). It quantifies, per tier,
  **over-calls (false positives)** and **under-calls (false negatives)** and
  which transition removes each — so "each higher level reduces false positives"
  (the L1→L2 scoping layer) and "lower levels are insufficient to catch some
  real breaks" (L0/L1 under-calls that only headers or build context reveal)
  become tracked, gated facts rather than assertions. Gates on top-tier
  correctness + under-call monotonicity (more evidence never hides a break an
  earlier tier caught — the authority rule, ADR-028 D3).
- **Per-axis FP-rate trend reporting** — the public-surface FP-rate gate
  (`scripts/check_fp_rate.py`) tags each corpus case with its scoping axis;
  `--json` now carries a `by_category` breakdown and `--markdown` renders a
  per-axis accuracy table for a CI step-summary / release-over-release trend.
- **FP-rate corpus now guards enum-reachability and pointer/opaque precision**
  — eight cases (both polarities each) lock in that internal (unreferenced)
  enum value-change / member-removal changes and pointer-only *opaque* handle size
  changes scope out, while public-reachable enums and pointer-only
  *fully-defined* type size changes stay breaking. Baselines remain 0/0.
- New user-guide page **CI Gating** (`docs/user-guide/ci-gating.md`) — the
  missing hub explaining how baselines, policies, suppressions, and severity
  combine into the exit code (order of operations + the two exit-code
  schemes), cross-linked from the four detail pages.
- **`from-libabigail.md` is now an actual migration guide** — command swap,
  bitmask→scalar exit-code translation, an `abidiff`→`abicheck` flag-by-flag
  map, `abidw`/`abipkgdiff` equivalents, and INI→YAML suppression
  translation. The verdict-parity QA matrix it previously contained moved to
  `docs/development/libabigail-parity.md`, refreshed against the current
  `PARITY_CASES` in `tests/test_abidiff_parity.py` (the vtable/return/param/
  struct-size gaps it still listed as open were closed by castxml
  integration).
- New user-guide page **Producing Source Facts (Flow A/B/C)** documenting the
  three source-fact producers, a selection tree, and the `public-roots`/
  `ABICHECK_CC_HEADERS` header-resolution trap.
- **Two-way reconciliation in the `public_not_exported` cross-check** (ADR-035
  D4): a public header decl the L4 source-linker already tied to an export under
  a variant spelling (ctor/dtor clone, Mach-O underscore, ABI-tag/substitution
  drift) is no longer double-reported as "declared but not exported".

---

## [0.4.0] — 2026-07-01

### Changed

- **Breaking — CLI interface contract (ADR-037 / G22).** Reshaped the command
  surface behind a typed Tier-2 service chokepoint and shared option-family
  decorators. Migration notes:
  - `--header-backend` is renamed to `--ast-frontend` (per-side
    `--old-ast-frontend` / `--new-ast-frontend`); the old spelling is rejected.
  - `compare-release` and `deep-compare` are folded into `compare` via
    input-type dispatch — pass two directories/packages for the release/bundle
    flow, or `compare --sources …` for the former deep-compare path. (The
    GitHub Action still accepts `mode: compare-release` as an alias.)
  - Per-side L2 header backend on `compare`, and `--public-header-dir` on
    `scan` to classify public/internal provenance.
- **Breaking — command-surface consolidation (ADR-037 / G22).** Completed the
  ADR-037 cleanup begun in 0.4.0: removed the remaining deprecated command
  paths and introduced a shared `--lang` factory used identically across
  commands.
- **`scan` depth is a single `--depth` dial** (auto default), with auto-strict
  pins and `--build-info` sniffing, replacing the earlier per-mode flags.
- **Unified compile-context across `dump` / `compare` / `scan`** — the same
  `--gcc-*`, `--ast-frontend`, `--sysroot`, `--nostdinc`, and `--lang` flags
  behave identically on all three, plus native `--baseline` /
  `--baseline-header` / `--baseline-include` on `scan`.
- Version bump to 0.4.0.

### Added

- `graph` command group: `graph compare` (structural source-graph diff) and
  `graph explain` (localize a symbol or finding through the L5 source graph).

### Fixed

- Uniform CLI help: every option carries help text, options are grouped into
  rich-click panels, and shared flags use one canonical spelling
  (`-v/--verbose`, `-o/--output`, `-H/--header`) — all contract-tested.
- **L5 call-graph pass no longer risks an OOM-kill on a constrained host.** The
  unseeded `--depth source` / `--mode pr-deep` call-graph pass runs the same
  multi-GiB `clang -ast-dump=json` per TU as the L4 replay but, unlike L4, its
  worker count was CPU-bound only — so a small cgroup / CI container could spawn
  N concurrent giant ASTs and be OOM-killed. The call-graph worker count now
  shares the L4 RAM/cgroup-aware clamp (`ABICHECK_L4_JOB_MEM_GIB` budget);
  `ABICHECK_CALL_GRAPH_JOBS` still overrides CPU count but memory wins, mirroring
  `ABICHECK_L4_JOBS`. See `docs/development/performance.md` § "Scan-level
  scalability sweep".
- **Seedless `--depth source` no longer pays a full-tree call-graph cost.** An
  unseeded s5 run scoped its L4 replay to the public-API surface (headers-only,
  ~1 TU) but ran the L5 clang call-graph pass over the *whole* compile DB, so its
  cost scaled with the whole tree even though its reported L4 coverage stayed at a
  fraction (seedless-vs-seeded widened to ~3.7× at 16 TUs). The unseeded
  call-graph pass now scopes to the **same** compile units the L4 replay used, so
  it is consistent with the L4 surface and no longer scales with the tree
  (~2.4× faster on a synthetic n=8 tree, identical verdict). Seeded runs
  (`--since`/`--changed-path`) and `--depth full` are unchanged.

---

## [0.3.0] — 2026-06-03

### Added

#### Release Recommendation (semver + SONAME)
- New `abicheck/semver.py` derives a **release recommendation** from the
  policy-aware verdict + change set: a semantic-version bump
  (`major`/`minor`/`patch`/`none`) and a SONAME action
  (`bump_required`/`bump_performed`/`bump_missing`/`no_bump_needed`).
- Always emitted in `abicheck compare --format json` under the additive
  `release_recommendation` key (also in `--stat --format json` and leaf mode);
  opt-in for Markdown via the new **`--recommend`** flag (works in leaf mode
  too). Policy-aware (honours `--policy sdk_vendor`/`plugin_abi` and custom
  policy files).
- JSON schema bumped to **1.1** (additive): `release_recommendation` documented
  as an optional object in `abicheck/schemas/compare_report.schema.json`.
- New tests: `tests/test_semver_recommendation.py`,
  `tests/test_workflow_scenarios.py` (drop-in upgrade, additive minor,
  host↔plugin load contract, policy-scoped decision).

#### User-Scenario / Flow Catalog (end-to-end scanner validation)
- New internal **user-scenario catalog** under `tests/scenarios/*.yaml` (grouped
  by theme, merged by globbing so it scales past one file): defines real-world
  *user flows* (CI gate, public-surface compliance scan, SARIF for code
  scanning, release recommendation, suppression, offline snapshots, …) —
  distinct from `examples/` (change-type fixtures) and `plans/` (backlog).
- `tests/test_scenarios.py` drives each automated scenario through the abicheck
  **CLI end-to-end** (CliRunner on JSON snapshots) and asserts the documented
  outcome, validating abicheck as a *scanner tool*, not only a change detector.
  Every scenario's `validates:` is checked against the use-case registry.
- Captures the missed usage scenario from **issue #235** (public-header scoping
  must suppress private ABI breaks) as `SC-PUBLIC-SURFACE-SCOPE`, now an
  end-to-end regression guard.

#### Use-Case Coverage Evaluation + machine-checked registry
- New `docs/development/usecase-coverage-evaluation.md` maps abicheck against
  the full application/library ABI-API change use-case space and records the
  code/test/example follow-ups (gaps G1–G8).
- New `docs/development/usecase-registry.yaml` — the machine-checkable source of
  truth for every use case (`status`, `axis`, `evidence`, `gap`, `next_steps`),
  validated by `tests/test_usecase_registry.py`: coverage claims must cite
  evidence paths that exist, and unfinished items must carry a gap + plan. This
  makes the use cases first-class, extensible, and testable.
- Cross-platform honesty: `docs/reference/platforms.md` now states the
  validation reality (Linux = CI-validated baseline; macOS/Windows =
  parser-level/partial), guarded by `tests/test_platform_coverage_honesty.py`.

#### JUnit XML Output
- **`--format junit`** for `compare` and `compare-release` commands — produces
  JUnit XML reports for CI systems (GitLab CI, Jenkins, Azure DevOps) that
  display ABI check results as standard test results in their dashboards.
- Each exported symbol/type maps to a `<testcase>`; breaking changes become
  `<failure>` elements with severity type and source location.
- Supports `--show-only` filtering, suppression files, and policy overrides.
- New module: `abicheck/junit_report.py` (stdlib only, no external dependencies).

#### Binary Fingerprint Rename Detection (Exploratory)
- **Binary fingerprint rename detection** (exploratory, ADR-003 extension):
  new `binary_fingerprint.py` module with `compute_function_fingerprints()`,
  `match_renamed_functions()`, and `compute_section_summary()`.  Uses function
  code size and SHA-256 hash from ELF `.dynsym` + `.text` to detect likely
  renames when symbol names change but the underlying code is identical.
  New `FUNC_LIKELY_RENAMED` change kind (verdict: `COMPATIBLE_WITH_RISK`).
  Integrated as the `fingerprint_renames` detector — fires only in
  `elf_only_mode` (stripped binaries without debug info or headers).

#### Debian Symbols File Adapter
- **`abicheck debian-symbols generate`** — generate Debian symbols files (`dpkg-gensymbols`
  format) from shared library binaries. Supports C++ demangled `(c++)` form, ELF symbol
  versioning (`@Base` / `@VERSION_NODE`), and automatic SONAME-to-package-name derivation.
  Options: `--package`, `--version`, `--no-cpp`, `-o`.
- **`abicheck debian-symbols validate`** — validate a Debian symbols file against a binary.
  Reports missing and new symbols. Respects `(optional)` tag semantics. Exit code `0` = match,
  `2` = mismatch.
- **`abicheck debian-symbols diff`** — diff two Debian symbols files showing added, removed,
  and version-changed symbols.
- Full Debian tag syntax support: `(c++)`, `(optional)`, `(arch=...)`, pipe-separated groups
  (`(c++|optional)`), and round-trip formatting preservation.
- New module: `abicheck.debian_symbols` with Python API for programmatic use
  (`generate_symbols_file`, `validate_symbols`, `diff_symbols_files`, `parse_symbols_file`).

#### ELF Symbol-Version Policy Checks
- **`symbol_version_node_removed`** (BREAKING) — detects when an entire version node
  (e.g., `LIBFOO_1.0`) is removed from the version script, listing affected symbols.
  Deduplicated with `symbol_version_defined_removed` (the more specific node-level
  change wins).
- **`symbol_moved_version_node`** (COMPATIBLE_WITH_RISK) — detects when a symbol
  migrates between version nodes (e.g., `LIBFOO_1.0` → `LIBFOO_2.0`).
- **`soname_bump_recommended`** (COMPATIBLE) — post-detector advisory emitted when
  binary-incompatible changes are detected but the SONAME is not bumped. This
  advisory can be escalated to BREAKING via `--policy-file` with
  `soname_bump_recommended: break`.
- **`soname_bump_unnecessary`** (COMPATIBLE) — advisory emitted when the SONAME is
  bumped but no binary-incompatible changes are detected.
- **`version_script_missing`** (COMPATIBLE) — advisory emitted when the new library
  exports symbols without a version script (`--version-script`).
- New `diff_versioning.py` module with version-node graph diffing, SONAME bump
  policy check (post-detector), and version-script presence detection.
- Cross-detector deduplication for `SYMBOL_VERSION_NODE_REMOVED` vs
  `SYMBOL_VERSION_DEFINED_REMOVED`.
- 35 new tests covering all version-policy scenarios and checker integration.

#### Config-key consistency follow-ups
- `--scope-public-headers/--no-scope-public-headers` toggle added to `appcompat`
  (previously always-on with no control) and to `compare-release` (toggle form).
- `--severity-preset`/`--severity-*` added to `compare-release` (aggregated across
  per-library, bundle, and matrix findings, honoring per-library `--policy-file`
  overrides; removed-library exit `8` still takes precedence) and `appcompat`
  (full-compare mode only — weak/`--check-against` keeps the verdict-based exit;
  app-scoped to `breaking_for_app`, with missing required symbols/versions floored
  as hard breaks).
- `--debug-format {auto,dwarf,btf,ctf}` selector on `compare`/`dump`; the legacy
  `--btf`/`--ctf`/`--dwarf` flags are hidden from `--help` but remain functional.
  `--compile-db` is likewise hidden (still an alias of `-p/--build-dir`).
- `--report-mode impact` (sugar for `full` + `--show-impact`).
- `appcompat` now warns (instead of silently ignoring) when `-H`/`-I` are supplied
  in weak (`--check-against`) / `--list-required-symbols` mode.
- New rationale doc `docs/development/config-key-review.md` (full CLI/config-key
  surface audit with per-mode inconsistency analysis and implementation status).

### Changed

#### ELF-only function removals are now BREAKING
- **Breaking (verdict change):** a removed *exported* function symbol with no
  header/DWARF confirmation (`func_removed_elf_only`) is now classified
  **BREAKING** instead of compatible. Removing a dynamic export breaks old
  binaries that link or `dlsym()` it regardless of header evidence, matching
  `abidiff`/ABICC. This can change a `compare`/`compat` run from compatible to
  breaking (legacy `compare` exit `0`→`4`); the false-positive avalanche this
  could otherwise cause is held back by the shared transitive-runtime symbol
  filter below (those symbols no longer enter a non-runtime library's surface).

### Fixed

#### Windows example-platform metadata matches the validated build surface
- Corrected the platform declarations for eight advanced C++/template example
  fixtures (`case79`, `case85`, `case95`, `case100`, `case101`, `case102`,
  `case110`, `case111`) from Linux/macOS/Windows to Linux/macOS. These cases
  remain in the full catalog, but Windows CI no longer attempts CMake builds
  for fixtures that are not validated on the Windows toolchain.

#### Transitive stdlib/runtime symbols no longer leak into the ABI surface
- Centralized ELF ABI-relevance filtering into `abicheck/elf_symbol_filter.py`
  and shared it across the symbols-only dumper, DWARF snapshot extraction, and
  symbol/type diffing. Previously a weak transitive libstdc++/libc++ export
  could be filtered from symbols-only reports yet re-enter as a `PUBLIC` DWARF
  function, producing phantom `FUNC_REMOVED` and type-reachability findings
  (observed on oneTBB `libtbbmalloc` 2021.5→2021.9, where `abidiff` was clean).
- DWARF export indexing and `DW_AT_deleted` subprograms now consult the same
  filter; libstdc++/libc++ themselves are exempt (they *own* `std::`).
- Project-owned RTTI (`_ZTI*`/`_ZTS*` for the library's own types) is preserved;
  only standard-library RTTI prefixes are dropped.

#### Cross-mode config-key consistency (CLI surface review)
- **Breaking (default change):** `compare-release` now restricts findings to the
  public-header ABI surface **by default** (`--scope-public-headers` on), matching
  `compare` and the Python API. Previously it was off-by-default. Pass
  `--no-scope-public-headers` to restore the old unscoped output. This can change
  which findings (and therefore exit codes) a release surfaces in CI.
- **Breaking (default change):** `compare-release -j/--jobs` now defaults to `0`
  (auto-detect CPU count, i.e. parallel) instead of `1` (serial). Report ordering
  is deterministic regardless of `-j` (results are emitted in matched-library
  order), so this does not churn snapshots — but multi-library runs now parallelize
  by default.
- `compare --demangle` is now tri-state: it defaults **on** for the text
  formats whose renderer demangles symbols (`markdown`/`review`) and **off**
  for `json`/`sarif`/`html` (HTML symbols are rendered structurally);
  explicit `--demangle`/`--no-demangle` still wins.
- `compare` prints the active exit-code scheme (legacy verdict vs severity-aware)
  to stderr for human formats, so the previously-silent switch on the first
  `--severity-*` flag is now visible. Exit-code numbers are unchanged.

### Planned
- `--policy-file` schema validation improvements
- Version-stamped typedef suppression (libpng `png_libpng_version_X_Y_Z` pattern)

---

## [0.2.0] — 2026-03-21

### Added

#### Application Compatibility Checking ([ADR-005](docs/development/adr/005-application-compatibility.md)) (#157)
- **`abicheck appcompat`** — answer "Will my application break with the new library?" by
  intersecting the app's required symbols with the library diff. Only changes affecting symbols
  your binary actually uses are reported.
- Full mode (old lib + new lib + headers) and weak mode (`--check-against` a single library).
- `--list-required-symbols` to inspect which symbols your binary imports.
- `--show-irrelevant` to see filtered-out changes that do not affect your application.
- Works with ELF, PE, and Mach-O binaries.

#### Cross-Platform Support
- **Windows (PE/COFF)** and **macOS (Mach-O)** binary metadata analysis (exports, imports,
  dependencies) alongside existing Linux (ELF) support.
- **PDB parser**: Windows PE debug info extraction for type-level analysis.
- Windows MSVC/MinGW toolchain support matrix and smoke tests.
- macOS ARM64 regression tests and `install_name` coverage.

#### Configurable Severity Levels (#180)
- Four issue categories: `abi_breaking`, `potential_breaking`, `quality_issues`, `additions`,
  each assignable to `error`, `warning`, or `info`.
- **`--severity-preset`**: Built-in presets (`default`, `strict`, `info-only`) for quick
  configuration.
- Per-category overrides: `--severity-abi-breaking`, `--severity-potential-breaking`,
  `--severity-quality-issues`, `--severity-additions`.
- Severity controls report visualization (badges, section grouping) and exit codes.
- PolicyFile overrides supported. JSON output includes top-level `"severity"` object
  and per-change `"severity"` field.
- Replaces the removed `--fail-on-additions` flag.

#### Report Filtering & Deduplication ([ADR-004](docs/development/adr/004-report-filtering-and-deduplication.md))
- **Redundancy filtering**: Automatically collapses derived changes caused by root type changes
  (e.g. a struct size change that propagates to 30 `FUNC_PARAMS_CHANGED` entries). Root type
  changes are annotated with `caused_count` and `affected_symbols`. Use `--show-redundant` to
  disable filtering.
- **`--show-only`**: Comma-separated filter tokens to limit displayed changes by severity
  (`breaking`, `api-break`, `risk`, `compatible`), element (`functions`, `variables`, `types`,
  `enums`, `elf`), or action (`added`, `removed`, `changed`). AND across dimensions, OR within.
  Does not affect verdict or exit codes. Invalid tokens produce a clean CLI error.
- **`--stat`**: One-line summary mode for CI gates. With `--format json`, emits only the summary
  object (no changes array).
- **`--report-mode leaf`**: Root-type-grouped output that lists affected interfaces under each
  root type change, instead of listing every change individually.
- **`--show-impact`**: Appends an impact summary table showing root changes and how many
  interfaces each affects, with separate columns for direct and derived counts.
- All filtering features work across Markdown, JSON, SARIF, and HTML output formats.
  ABICC-compatible XML includes redundancy annotations but does not support `--show-only`.
- Redundancy annotations in SARIF (`caused_by_type`/`caused_count` in result properties,
  `redundant_count` in run properties) and XML (`<redundant_changes>`, `<caused_by>`,
  `<caused_count>` elements in both binary and source sections).

#### Package Extraction Layer (#161)
- Extract and compare shared libraries directly from **RPM, Deb, tar, conda, and wheel**
  packages without manual unpacking.
- Full-stack ABI checking with dependency resolution across package contents.

#### DWARF-only Snapshot Builder ([ADR-003](docs/development/adr/003-data-source-architecture.md))
- Headerless ELF analysis: build ABI snapshots from DWARF debug info alone, without
  requiring public headers or castxml.

#### Full-Stack Dependency Validation (#153)
- `abicheck deps` — show dependency tree and symbol binding status for a binary.
- `abicheck stack-check` — compare a binary's full dependency stack across two sysroots.
- `--follow-deps` flag for `compare` to include dependency info.
- Symbol origin tracking (`SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED`).

#### Suppression Enhancements (#146)
- `label` field for human-readable suppression rule names.
- `source_location` field for file-scoped suppression rules.
- `expires` field for time-limited suppression rules with automatic expiry.
- **Suppression audit mode**: detect stale rules matching nothing, high-risk suppressions
  masking BREAKING changes, and near-expiry rules.

#### Detection Improvements
- **ELF visibility tracking**: New `SYMBOL_ELF_VISIBILITY_CHANGED` ChangeKind for
  DEFAULT/PROTECTED/HIDDEN/INTERNAL transitions.
- **`FUNC_REMOVED_FROM_BINARY`**: New BREAKING-severity ChangeKind for mixed-mode (headers +
  ELF) when a function is removed from the binary but header still declares it.
- **Global variable ELF-only tracking**: Collect STT_OBJECT and STT_TLS symbols as Variable
  entries in ELF-only fallback, enabling VAR_REMOVED/VAR_ADDED detection without headers.
- **DWARF reserved field detection**: DWARF struct layout diff detects reserved field
  activations (matching AST-level detection). Patterns include `reserved`, `mbz`, `fill`,
  `filler`, requiring matching offset AND type.
- **DWARF opaque struct handling**: Opaque types (forward-declared, accessed only via pointer)
  no longer trigger BREAKING for internal field changes. Correctly deserialized from JSON
  snapshots.
- **Type name canonicalization**: `canonicalize_type_name()` normalizes "struct Foo" vs "Foo",
  "const int" vs "int const", and whitespace differences, reducing false positives in
  field/return/variable/parameter type comparisons.
- **Cross-detector deduplication**: Centralized dedup collapses overlapping FUNC_REMOVED/
  FUNC_ADDED/VAR_REMOVED/VAR_ADDED reports from function, PE, and Mach-O detectors.
- **Confidence/evidence tracking**: `Confidence` enum (high/medium/low) on `DiffResult` based
  on available data sources (ELF, DWARF, header, PE, Mach-O). Human-readable
  `coverage_warnings` for disabled detectors.
- **Import-time ChangeKind completeness assertion**: Every `ChangeKind` member must appear in
  exactly one of BREAKING/COMPATIBLE/API_BREAK/RISK sets, enforced at import time.

#### Additional Improvements
- **GitHub Action** (`abicheck/abicheck@v1`) for CI integration with per-mode verdict mapping,
  format validation, and severity-preset support.
- **MCP server** for AI agent integration.
- **`--strict-elf-only`** flag: injects PolicyFile override upgrading `FUNC_REMOVED_ELF_ONLY`
  to BREAKING.
- **`compare-release`** composable file-classifier pipeline to identify ABI-relevant files in
  directory scans, ignoring non-ABI files.
- 13 new ABI compatibility test cases (cases 42, 49–62).
- Sentinel enum detection by name pattern (`*_last`/`*_max`/`*_count`).
- `--allow-symbols-only` flag for ELF compare without headers.
- Cross-platform CMake build support for example cases.
- 11 new Architecture Decision Records (ADRs 003–013).
- Renovate bot for dependency updates, GitHub issue/PR templates, CODEOWNERS.

### Changed
- **`checker.py` split into focused modules** (#187): The monolithic checker (3,939 lines) was
  split into `diff_types.py`, `diff_platform.py`, `diff_filtering.py`, `diff_symbols.py`, and
  `checker_types.py`. New `service.py` provides shared orchestration (`resolve_input`,
  `run_dump`, `run_compare`, `render_output`).
- **Standardized error hierarchy**: `PolicyError`, `ReportError` and other domain-specific
  exceptions added to `errors.py`. 46 error sites migrated from generic `RuntimeError`/
  `ValueError`. All inherit from `AbicheckError` and their original builtin for backward compat.
- **Code consolidation** (−226 lines): Shared DWARF utilities (`dwarf_utils.py`), unified
  `binary_utils.py` with `detect_binary_format()`, deduplicated HTML report constants, and
  PDB/PE module cleanup.
- **`--fail-on-additions` removed**: Replaced by the configurable severity system
  (`--severity-additions`).
- **Documentation reorganized**: Complete structure overhaul with standardized file naming.

### Fixed
- **Appcompat DSO scoping**: Symbols from unrelated DSOs (e.g., libexpat symbols when checking
  libz) no longer falsely attributed to the target library (#184).
- **C++ header auto-detection**: `.h` headers default to C mode; auto-detect C++ from structural
  syntax (class/namespace/template). Fixes false mismatches when castxml used wrong language
  mode.
- **C++ DWARF function extraction**: Demangled export index via batch `c++filt` with three-tier
  `_is_exported` check. Fixes missed C++ function detection in DWARF path.
- **Enum change deduplication**: Same-kind symbol-based dedup pass for enum ChangeKinds, preferring
  entries with populated old_value/new_value.
- **Compiler internal type filtering**: Filter `__va_list_tag`, `__builtin_va_list`, etc. from
  DWARF path. Eliminates false positives from compiler internals.
- **PDB struct extraction**: Deferred canonical registration until fields successfully extracted,
  preventing empty layouts from blocking valid later duplicates.
- **Compat HTML ELF-layer miscounting**: ELF-layer changes (soname_changed, etc.) now correctly
  categorized instead of being counted as Interface Problems.
- **Namespace-qualified type names**: Fixed `split("::")[0]` truncating names like
  `ns::MyStruct` to `ns`. Replaced with `_root_type_name()`.
- **Enum rename one-to-one guard**: Prevents aliases from collapsing true removals.
- **DWARF placeholder object check**: Check `dwarf.has_dwarf` flag instead of just `is not None`.
- **`affected_pct` always 0.0%**: `old_symbol_count` now propagated through `_apply_warn_newsym`
  and `_limit_affected_changes`. Capped at 100%.
- **Enum symbol qualification**: Use member-qualified enum symbols (`Color::GREEN`) so AST/DWARF
  dedup works via exact description matching.
- **Human-readable function parameters**: Format params as `int, int*` instead of raw Python repr.
- **PolicyFile on DiffResult**: Store `PolicyFile` on `DiffResult` with `_effective_kind_sets()`
  so policy overrides correctly affect report section classification.
- **Safe output file writing**: `_safe_write_output()` helper with parent directory creation
  replacing bare `write_text()` calls.
- **ELF format validation**: Validate ELF format in `deps_cmd` and `stack_check_cmd` before
  processing, preventing cryptic errors on non-ELF inputs.
- **PIE executable detection**: Distinguish PIE executables from shared libraries via PT_INTERP
  segment check.
- **castxml timeout handling**: Catch `subprocess.TimeoutExpired` with user-friendly error.
  Diagnostic hint when castxml fails in C mode on C++ headers.
- **JSON `--show-only` metadata**: Add `filtered_summary`/`show_only_applied` to JSON output.
  Always include `old_file`/`new_file` keys (null when absent).
- **`--show-only` exit code**: No longer incorrectly affects exit codes (display-only).
- **Library removal verdict**: Elevate verdict to `COMPATIBLE_WITH_RISK` when libraries are
  removed from dependency list.
- **compare-release non-ABI file noise**: Directory scans now ignore scripts, configs, and
  documentation that caused spurious errors.
- **GitHub Action / CLI alignment**: Fixed 7 discrepancies in verdict/exit-code mapping,
  format validation, and severity-preset scoping.
- **DWARFExprOp, TOCTOU, PDB ODR fixes**: Fixed attribute access, file operation races,
  and One Definition Rule handling.
- `enum_last_member_value_changed` downgraded to risk severity in policy.
- ABICC compat: auto-forward `abicheck compat <flags>` to `compat check`.
- Test parity fixes for ABICC 2.3.

### Performance
- **Ancestor function cache**: Each ancestor type scanned at most once in
  `_enrich_affected_symbols`, eliminating quadratic behavior on large diffs.
- **Pre-compiled regex patterns**: Word-boundary patterns in `_filter_redundant`,
  `_is_pointer_only_type`, and `_has_public_pointer_factory` compiled once and cached.
- **ELF section scan optimization**: Capture `.gnu.version` and `.dynsym` sections during main
  `iter_sections()` loop instead of re-scanning.
- **Session-scoped CMake builds**: Integration tests share a single cmake configure pass
  (reduced ~29 passes to 1 on Windows).
- **Parallel test execution**: pytest-xdist support with `--dist worksteal` and filelock-based
  build directory sharing.

### Testing
- Test coverage improved from 86% to 93.4% through systematic review of 117 test files.
- Hypothesis-based property tests: identical snapshots must produce NO_CHANGE, single known
  mutations must detect the specific ChangeKind.
- Exhaustive policy × ChangeKind matrix test: every ChangeKind verified under all 3 policies.
- Ground truth v3 with `expected_kinds` and `expected_absent_kinds` for bidirectional validation.
- macOS ARM64 regression tests and `install_name` edge-case coverage.
- `func_deleted` edge-case regressions for ABICC #100 (`= delete` hardening).
- Fixed trivially-true tests, duplicate test bodies, wrong mocks, and weak assertions.
- Removed duplicate tests, added `slow` marker, parametrized repetitive assertions.

### Platform
- **Linux** (ELF/DWARF) — full support.
- **Windows** (PE/COFF/PDB) — binary metadata and header AST analysis.
- **macOS** (Mach-O/DWARF) — binary metadata and header AST analysis.

### Installation
- Published to **PyPI**: `pip install abicheck`
- Published to **conda-forge**: `conda install -c conda-forge abicheck`

---

## [0.1.0] — 2026-03-13

First public release of abicheck — a modern, Python-native ABI compatibility checker
for C/C++ shared libraries, designed as a drop-in replacement for
[abi-compliance-checker (ABICC)](https://lvc.github.io/abi-compliance-checker/) with
additional capabilities.

### Features

#### Core Analysis
- **Multi-tier detection**: castxml (header AST) + ELF symbol table + DWARF debug info
- **85 ChangeKinds** across BREAKING / API_BREAK / COMPATIBLE severity tiers
- **100% ABICC parity** for 55 documented ABI break scenarios; exceeds ABICC in 6 additional scenarios
- Works on **release builds** with headers + `.so` — no debug symbols required for core checks

#### ABI Break Detection
- Function/variable add/remove/type changes
- Struct/class size, alignment, field offset, vtable changes
- Enum member add/remove/rename/value changes
- Return type, parameter type/count/default changes
- noexcept, virtual, pure-virtual, static, const, volatile method changes
- Base class add/remove/reorder (multiple inheritance)
- Symbol binding/type/visibility changes
- ELF metadata: SONAME, DT_NEEDED, DT_RPATH, IFUNC, symbol versioning, TLS
- DWARF advanced: calling convention, frame register (CFA) drift, value ABI trait
  - CFA extraction uses modal heuristic (not max-PC) to avoid epilogue bias
  - `.dynsym` takes priority over `.symtab` (local symbols never shadow exported names)

#### Policy System
- **Built-in profiles**: `strict_abi` (default), `sdk_vendor`, `plugin_abi`
- **`--policy-file`**: YAML-based per-kind verdict overrides for project-specific rules
- `DiffResult.policy` field — all classification buckets (`breaking`, `source_breaks`, `compatible`) are policy-aware
- Single source of truth: `policy_kind_sets()` in `checker_policy.py`

#### CLI
- `abicheck dump` — create ABI snapshot JSON from `.so` + headers
- `abicheck compare` — diff two snapshots with `--policy`, `--policy-file`, `--format` (markdown/json/sarif/html), `--suppress`
- `abicheck compat` — ABICC drop-in CLI (accepts all ABICC flags)
- `abicheck compat-dump` — create snapshot from ABICC XML descriptor
- `abicheck --version` — print version

#### Reports
- Markdown, JSON, SARIF, HTML report formats
- Split reports: `--bin-report-path` / `--src-report-path` (binary vs source breaks)
- Suppression system: YAML rules with symbol/type/version/platform/scope filters
- RE2-based suppression engine (O(N) guaranteed, no ReDoS)

#### ABICC Compatibility
- Drop-in: all major ABICC flags accepted (`-strict`, `-source`, `-binary`, `-warn-newsym`, etc.)
- ABICC XML descriptor support via `abicheck compat`
- ABICC-compatible HTML report output (`-old-style`)
- Exit codes mirror ABICC (0/1/2)

#### Deployment Risk Verdict
- New verdict `COMPATIBLE_WITH_RISK`: binary-compatible changes that pose a deployment
  risk requiring manual verification of target environment constraints.
- `RISK_KINDS` classification set in `checker_policy.py` (currently: `SYMBOL_VERSION_REQUIRED_ADDED`).
- `DiffResult.risk` property to query risk-classified changes.
- `"risk"` severity level in YAML policy files (maps to `COMPATIBLE_WITH_RISK`).
- `SYMBOL_VERSION_REQUIRED_ADDED` moved from `BREAKING_KINDS` → `RISK_KINDS`:
  new GLIBC version requirements in `DT_VERNEED` now produce `COMPATIBLE_WITH_RISK`
  instead of `BREAKING` — existing compiled consumers are unaffected (already linked).
- `policy_kind_sets()` now returns a 4-tuple `(breaking, api_break, compatible, risk)`.
- `plugin_abi` policy treats `SYMBOL_VERSION_REQUIRED_ADDED` as `BREAKING`
  (host/plugin deployment-floor raise is an in-process load blocker).
- `_apply_warn_newsym` promotes `COMPATIBLE_WITH_RISK` → `BREAKING` when `-warn-newsym` is active.

#### SARIF exit code changes (migration note)
- `BREAKING`: exit code `1` → `4`
- `API_BREAK`: now emits `2` (was `0`)
- `COMPATIBLE_WITH_RISK`: emits `0` (binary-compatible; risk surfaced via `exitCodeDescription`)
- If your CI pipeline checks `exitCode == 1` on BREAKING, update to `exitCode == 4`.

### Platform
- **Linux only** (ELF/DWARF). Windows (PE) and macOS (Mach-O) are not yet supported.

### Installation
- **From source**: `pip install abicheck` or `pip install -e ".[dev]"` for development.
- `castxml` must be installed separately via system packages (`apt install castxml`)
  or conda-forge (`conda install -c conda-forge castxml`)

### Requirements
- Python ≥ 3.10
- `castxml` (mandatory — for header-based C/C++ AST parsing; included in conda-forge install)
- `g++` or `clang++` (accessible to castxml)
- See [Installation](docs/getting-started.md) for full setup instructions

### Known Limitations

- **Suppression system**: label/tag-based suppression, file-scoped suppression (by `source_location`),
  and suppression expiry dates are not yet implemented. Resolved in v0.2.0.

---

[0.1.0]: https://github.com/abicheck/abicheck/releases/tag/v0.1.0
[0.2.0]: https://github.com/abicheck/abicheck/releases/tag/v0.2.0
[0.3.0]: https://github.com/abicheck/abicheck/releases/tag/v0.3.0
[0.4.0]: https://github.com/abicheck/abicheck/releases/tag/v0.4.0
[Unreleased]: https://github.com/abicheck/abicheck/compare/v0.4.0...HEAD
