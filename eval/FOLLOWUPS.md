# abicheck field-evaluation — follow-up plan

Derived from the field evaluation ([FINDINGS.md](FINDINGS.md), problems P01–P21)
and the work already shipped on this branch. Each item carries **context**,
**pointers** (file / function), an **approach**, **acceptance**, and a
**cross-reference** to an existing usecase gap/plan where one already covers it.

> Most C++ source-ABI findings map onto **existing** planned gaps — the eval
> corroborates them with concrete real-world evidence rather than inventing new
> work. Net-new items are flagged **NEW**.

## Status map (P01–P21 + extras)

| ID | Area | Status | Follow-up owner |
|----|------|--------|-----------------|
| P01 split conda pkgs | discovery | documented (field guide) | — (conda reality) |
| P02 variant select | discovery | documented | — |
| P03 `--show-data-sources` preview-only | UX | **shipped** (preview-only made explicit) | §B1 NEW |
| P04 `-H` hard-errors w/o castxml | env | resolved (tool present) | — |
| P05 clang L4 empty decl tables | C++ L4 | **shipped** (clang AST emits decls/types) | §A1 (gap **G4**) |
| P06 serial L4 | perf | **shipped** (parallel) | §C1 (scaling validation) |
| P07 plain DB no toolchain | discovery | documented | §E3 NEW (validate) |
| P08 versioned-symbol noise | correctness | **shipped** (detector+collapse) | §G (gap **G15**) |
| P09 silent autotools | discovery | **shipped** (diagnostic) | — |
| P10 autotools bootstrap | discovery | documented | — (upstream) |
| P11 compare rename cost | perf | **shipped** (batch-demangle) | — |
| P12 meson `builddir` | discovery | **shipped** | — |
| P13 L4 infeasible on monorepo | perf/scope | documented + mitigations | §E4 (retry live) |
| P14 castxml no compile-DB `-I` | C++ L2 | **shipped** (compile-DB `-I`/flags bridged) | §A2 (gap **G16**/G4) |
| P15 castxml ✗ libstdc++ 13 | C++ L2/L4 | **mitigated** (clang default backend) | §A1 (gap **G4**) |
| P16 `--lang c` aborts on extern "C" | UX | **shipped** (warn + C++ retry) | §A3 (gap **G16**) |
| P17 thin build-option normalization | discovery | **shipped** (broadened vocabulary) | §B2 NEW |
| P18 L5 coupled to L4 | UX | **shipped** (`graph-build`) | — |
| P19 L4 needs generated headers | discovery | **shipped** (hint) | — |
| P20 multi-`.so` pairing | discovery | documented + eval guard | §F2 NEW (FP corpus) |
| P21 oneDAL Bazel toolchain | discovery | documented | §E2 (validate Bazel live) |

---

## A. C++ source-ABI unblock (the highest-leverage cluster)

The eval (run against the pre-`b2b19bc` state) framed this as "no real C++
source-ABI surface obtainable today". **The code has since advanced past the
eval snapshot**: the clang AST-JSON backend is the *default* source extractor
and already emits declarations + types, and the `-p`/compile-DB bridge already
carries the build's include paths into the header parse. A1 and A2 are therefore
**verified shipped** below (regression-tested at the unit level); the residual
work is the live C++ validation campaign tracked under §C/§E.

### A1. clang declaration/type extractor (P15, P05) — gap **G4** — **shipped/verified**
- **Context.** `g4-header-ast-extractor.md` planned "a libclang-based header-AST
  extractor alongside castxml" because ICU/snappy yielded `reachable_declarations: 0`
  in the eval and castxml dies in `/usr/include/c++/13/bits/basic_string.h`.
- **What actually shipped (no new dependency).** The `clang -ast-dump=json`
  backend (`source_extractors/clang.py`) already produces real `SourceEntity`
  decls/types/typedefs/enums/constexpr/macros from the AST — not just body
  fingerprints — and is the *default* inline extractor (`inline.py:161`,
  `_make_source_extractor` returns `ClangSourceExtractor` unless `castxml` is
  explicitly requested). The linker (`source_link._route_entity`) routes
  functions → `reachable_declarations` and records/enums/typedefs →
  `reachable_types`. So a stock clang toolchain yields a non-empty C++ source
  surface and sidesteps the castxml-on-libstdc++13 break (P15) entirely — no
  `python-clang`/`libclang` (cindex) dependency was needed (ADR-001).
- **Acceptance (pinned).** `tests/test_source_extractors_clang.py::test_clang_ast_yields_nonzero_reachable_surface`
  feeds a representative clang AST through `source_abi_from_clang_ast` →
  `link_source_abi` and asserts both `reachable_declarations` and
  `reachable_types` are non-empty (the literal eval metric), at the fast-lane
  unit level (no clang needed). The live snappy/ICU `--sources` confirmation is
  the §E source-tier campaign (D1/E4).
- **Residual.** A dedicated cindex backend is *not* planned — the AST-JSON path
  covers the acceptance. castxml remains an opt-in alternative (`--source-extractor castxml`).

### A2. castxml/clang inherits compile-DB include paths (P14) — gap **G16**/G4 — **shipped/verified**
- **Context.** Public headers routinely `#include` *generated* headers
  (`snappy-stubs-public.h`); without the build's `-I`, the header parse fails
  `file not found`.
- **What actually shipped.** `cli._resolve_build_context_flags` runs
  `build_context_for_header(db, header).to_castxml_flags()` whenever a compile DB
  is supplied via `-p`/`--compile-db`, deriving `-I`/`-isystem`/`-D`/`-U`/`-std`/
  `--target`/`--sysroot` from the matched TU; `_merge_gcc_options` folds them into
  the castxml invocation. So the build dir holding generated headers is on the
  include path automatically — no manual `-I`.
- **Acceptance (pinned).** `tests/test_build_context.py::TestPerHeaderMatching::test_matched_tu_include_paths_flow_into_castxml_flags`
  asserts the matched TU's include dirs (where generated headers land), defines,
  and ABI flags all reach `to_castxml_flags()` without a manual include.

### A3. `--lang c` heuristic should warn, not abort (P16) — gap **G16** — **shipped**
- **Context.** `G16` already lists this (`--lang c` + `extern "C"` fails because
  castxml drives clang C++-ish). The eval reproduced it on `zlib.h`: the "header
  appears to contain C++ syntax" hint **aborts** instead of degrading.
- **Pointers.** `abicheck/cli.py:644` / `:1506` (`--lang` option); the hint emit
  in the castxml driver (`abicheck/dumper_castxml.py`); plan
  `docs/development/plans/g16-header-scope-toolchain-robustness.md`.
- **Approach.** Demote the heuristic to a warning + auto-retry under the other
  language mode; never hard-fail a correct `extern "C"` header.
- **Acceptance.** `dump zlib.h --lang c` succeeds (or warns + falls back), no abort.
- **Effort·Risk.** S · low. Fold into the G16 work.
- **Shipped.** `dumper._castxml_dump` now factors the single invocation into
  `_run_castxml_attempt` and, when an explicit `--lang c` parse fails *and* the
  header carries C++ constructs (`extern "C"`/class/namespace) *and* the failure
  is not a frontend-too-old signature, retries once in C++ mode with a warning
  rather than hard-failing. A pure-C header that fails in C mode is not retried
  (the failure is real), and if both modes fail the originally-requested C-mode
  error/hint is surfaced. Tests: `tests/test_castxml_toolchain_robustness.py::TestLangCFallsBackToCpp`.

---

## B. Net-new code fixes (NEW)

### B1. `--show-data-sources` is preview-only (P03) — NEW — **shipped**
- **Context.** Running `dump --show-data-sources` prints the L0–L5 table but
  **collects nothing** and embeds nothing — surprising; a user expects it to also
  produce the snapshot.
- **Pointers.** `abicheck/cli.py:749`/`:776` (`show_data_sources` branch),
  `abicheck/cli_datasources.py` `print_data_sources`.
- **Approach.** Make it additive (collect **and** print), or rename to
  `--explain-data-sources` and emit a loud "preview only — no data embedded" line.
- **Acceptance.** Either the snapshot is written with embedded facts, or the
  preview-only nature is unmissable in output + `--help`.
- **Effort·Risk.** S · low.
- **Shipped (made the contract unmissable).** The `--show-data-sources` help now
  opens with "Preview only … No snapshot is written and no L3/L4/L5 facts are
  embedded", and `print_data_sources` prints a loud trailing notice to stderr
  after the table. Tests: `test_dwarf_snapshot.py::TestCLIDwarfFlags::test_dump_help_flags_data_sources_preview_only`
  and the `preview-only` assertions in `test_show_data_sources_via_runner`.

### B2. Build-option normalization vocabulary is thin (P17) — NEW — **shipped**
- **Context.** LLVM produced **6 build_options from 2,719 TUs**; zstd 0. The
  `command`-string DB *is* shlex-parsed (`build_context.py:91`), so this is **not**
  a parsing gap — `derive_build_options` only normalizes a small flag set
  (std/exceptions/rtti/visibility), missing most ABI-relevant flags.
- **Pointers.** `abicheck/build_context.py` `derive_build_options`;
  `abicheck/buildsource/adapters/compile_db.py` (`collect` ~L53, imports
  `derive_build_options`); `abicheck/buildsource/build_evidence.py` `BuildOption`.
- **Approach.** Broaden the normalized vocabulary: `-fno-omit-frame-pointer`,
  `-stdlib=`, `-D_GLIBCXX_USE_CXX11_ABI`, `-m32/-m64`, `-march`/`-mtune`,
  sanitizers, LTO, `-fPIC`/`-fPIE`, `-fvisibility-inlines-hidden`. De-dup
  library-wide but keep per-target divergence as drift signal.
- **Acceptance.** LLVM/zstd L3 surfaces the real ABI-affecting flag set; a flag
  flip between releases shows as `build_flag_changed` drift.
- **Effort·Risk.** M · low.
- **Shipped.** Extended `ABI_RELEVANT_FLAG_PREFIXES` (`adapters/base.py`) with
  `-stdlib=`, `-march=`/`-mtune=`/`-mfloat-abi=`/`-mfpmath=`, `-fsanitize=`/
  `-fno-sanitize=`, `-fPIC`/`-fpic`/`-fPIE`/`-fpie` (+ negatives) and
  `-f[no-]omit-frame-pointer`. `derive_build_options` already projects unknown
  ABI-relevant flags into `BuildOption`s and `build_diff._diff_options` already
  emits `ABI_RELEVANT_BUILD_FLAG_CHANGED` for any keyed drift, so no new
  ChangeKind was needed. A `-stdlib=libstdc++ → libc++` swap now reads as a single
  drift finding. Tests: `tests/test_build_source_pack.py::test_broadened_abi_flag_vocabulary_is_captured`,
  `test_stdlib_flip_surfaces_as_abi_build_flag_drift`, `test_march_added_surfaces_as_abi_build_flag_drift`.

---

## C. Performance validation

### C1. P06 parallel-L4 scaling (validate) — **shipped**
- **Context.** Parallel L4 shipped (deterministic). Measured freetype 42-TU only:
  25.8s → 19.1s (1.35×) — modest because L3/L5/serialization are serial and the
  TU count is small.
- **Shipped.** `eval/scaling.py` times `dump --sources` at several
  `ABICHECK_L4_JOBS` levels on real trees (freetype 42 TU, zstd 76 TU) and
  renders `eval/SCALING.md`. The curve (jobs 1/2/4/8 on a 4-CPU host) confirms the
  L4 clang extraction parallelizes but is a *minority* of whole-dump time, so the
  end-to-end speedup is **Amdahl-bounded** (~60–83% serial), peaks by ~2 jobs, and
  regresses under oversubscription. Pure helpers (`speedup_rows`,
  `amdahl_serial_fraction`, `render_scaling`) unit-tested in
  `tests/test_eval_scaling.py`.
- **Acceptance (met).** Scaling curve (jobs=1/2/4/8) on 2 trees in `eval/SCALING.md`
  (`python eval/scaling.py --jobs 1,2,4,8`).

---

## D. eval-suite infrastructure (NEW) — **shipped**

### D1. Add the build/source (L3/L4/L5) tier to the runner — **shipped**
- **Context.** The benchmark suite (`eval/runner.py`) ran only the binary
  (L0/L1) tier. The source tier was not folded in.
- **Shipped.** `eval/runner.py --tier {binary,source,both}` (default `binary`).
  The source tier iterates manifest entries with a `source:` block:
  `_scan_source_side` shallow-clones the repo at the tag (`_git_clone_tag`),
  configures it (`_cmake_configure` → `compile_commands.json`, honoring
  per-entry `cmake_subdir`/`cmake_args`), runs `dump --sources <tree>
  --build-info <build> --collect-mode source-target`, and `_source_coverage`
  counts the embedded `build_source` L3 (compile units/targets/options) / L4
  (declarations/types/macros) / L5 (nodes/edges) facts; the two sides are then
  `compare`d. Gated on git+cmake (skips gracefully with a row per entry when
  absent), notes when clang is missing (partial L4). `render_report` gained a
  Source-tier table; `REPORT.md` now carries both tiers. Manifest doc + zstd
  `cmake_subdir: build/cmake` added.
- **Acceptance (met).** `eval/REPORT.md` gains a reproducible source-tier table
  (`python eval/runner.py --tier source`); pure helpers unit-tested in
  `tests/test_eval_runner.py`.

### D2. Wire the suite into CI as a scheduled lane — **shipped**
- **Context.** The suite is a real-world verdict **regression guard** (`expect`
  in `manifest.yaml`); it was manual.
- **Shipped.** `.github/workflows/eval-suite.yml` (mirrors the
  `performance.yml`/`mutation.yml` pattern): `workflow_dispatch` +
  weekly `schedule` (Mon 05:31 UTC) + `eval`-label PR trigger. The **binary-tier
  job gates** — `python eval/runner.py --tier binary --fail-on-drift` exits
  non-zero on any verdict drift / scan error (`runner.drift_rows`). The
  **source-tier job is non-gating** (`continue-on-error`): installs git+cmake+clang,
  runs `--tier source`, writes coverage to the job summary, uploads `results/`.
  Network-gated (anaconda.org + GitHub clones), so it never runs on every push.
- **Acceptance (met).** Green scheduled lane; red on an injected verdict drift
  (the `--fail-on-drift` gate, unit-tested via `drift_rows`).

---

## E. Coverage / platform validation (NEW)

The entire eval was **Linux/ELF**. These are untested paths, not known bugs.

### E1. PE/PDB (Windows) + Mach-O (macOS) source/build paths
- **Pointers.** `abicheck/pe_metadata.py`, `abicheck/pdb_*.py`,
  `abicheck/macho_metadata.py`; conda-forge ships `win-64`/`osx-64` subdirs
  (`condafetch.py` already parameterizes `subdir`).
- **Approach.** Extend the manifest with win-64/osx-64 entries; scan a handful of
  the same libraries' Windows/macOS builds.
- **Acceptance.** ≥5 PE and ≥5 Mach-O pairs scanned with sane verdicts.
- **Effort·Risk.** M · medium (PDB/dSYM availability).

### E2. Bazel adapter, live (P21) — **shipped**
- **Pointers.** `abicheck/buildsource/adapters/bazel.py` (cquery/aquery jsonproto).
- **Shipped.** Captured a real `bazel aquery --output=jsonproto` from a minimal
  `cc_library` (CppCompile/CppLink/CppArchive actions) and pinned it as a fixture
  (`tests/fixtures/bazel/cc_library_aquery.jsonproto.json`). Tests assert the
  adapter and `collect --bazel-aquery` produce non-empty L3 (compile + link units)
  from the genuine export — guarding against the hand-built fixtures drifting from
  what bazel actually emits.
- **Acceptance (met).** Non-empty L3 (1 compile unit, 2 link units) from a real
  aquery export. *Env note:* bazel's embedded JDK truststore rejected the proxy;
  the live capture needed `--host_jvm_args=-Djavax.net.ssl.trustStore=/etc/ssl/certs/java/cacerts`.

### E3. Cross-compiler toolchain capture (P07) — **shipped**
- **Pointers.** `abicheck/buildsource/compiler_record.py`
  (`.GCC.command.line` / `DW_AT_producer`); `build_diff._diff_toolchains`.
- **Shipped.** Built the same lib with gcc vs clang, confirmed each producer is
  recovered, and surfaced the swap as `TOOLCHAIN_VERSION_CHANGED`
  (`GNU 13.3.0 -> Clang 18.1.3`) through `collect --read-compiler-record` +
  `compare`. The validation flushed out a latent bug: `_diff_toolchains` only
  compared the *intersection* of language keys, and clang's producer carries no
  language token, so a gcc↔clang swap was silently missed — fixed with an
  identity-level fallback (fires on a compiler-id swap or a both-versions-known
  version change; target & missing-version are treated as unknown to avoid
  mixed-evidence false positives). Tests:
  `tests/test_compiler_record_cross_toolchain.py` (live, Linux/ELF) + unit tests
  in `tests/test_build_source_pack.py`.
- **Acceptance (met).** Toolchain/producer recorded; gcc↔clang drift visible.

### E4. L4 on a monorepo, live (P13) — **mechanism validated; LLVM-scale gated**
- **Context.** Bounded out earlier (hours). Now feasible with `source-changed`
  scope + P06 parallel + the per-TU cache.
- **Shipped (mechanism, live on zstd).** `collect --source-abi --source-abi-scope
  changed --changed-path <f> --source-abi-cache <dir>` replays only the TUs that
  touch the changed path and caches per-TU dumps: full target replay 76/76 TUs =
  48.6s → changed-scope 2/2 TUs = 4.7s → warm-cache re-run = 3.4s. Scoped L4 +
  near-free cached re-runs work end-to-end.
- **Residual (gated).** The LLVM-scale run still needs a *built* tree for the
  generated headers (P19); a configure-only LLVM checkout fails L4 on TUs that
  pull tablegen'd headers, and a partial build is hours / tens-of-GB — out of
  reach in the eval container. The enabling machinery is validated; the
  LLVM-specific run stays documented-as-gated.
- **Effort·Risk.** M · medium (needs built tree for generated headers, P19).

---

## F. Corpus expansion (NEW)

### F1. New ecosystems
- **Context.** Only conda C/C++ libs scanned. Untested: **Rust `cdylib`, Go `cgo`,
  Qt, Boost, libstdc++ itself**.
- **Pointers.** `eval/manifest.yaml` (add entries); `eval/condafetch.py` already
  handles arbitrary conda packages.
- **Acceptance.** ≥1 each of Rust/Go/Qt/Boost in the manifest with `expect` verdicts.
- **Effort·Risk.** S per lib · low.

### F2. Grow the FP-rate corpus with eval cases — **shipped**
- **Context.** The versioned-scheme and multi-`.so`-bundle (P20) shapes aren't in
  the FP gate.
- **Shipped.** Added three labelled cases to `scripts/check_fp_rate.py`:
  versioned-scheme churn on internal/ELF-only symbols (must scope non-breaking,
  FP guard), a multi-`.so`-bundle sibling-soname shape (hidden churn, FP guard),
  and versioned-scheme churn on the *public* surface (must stay breaking, FN
  guard). Corpus 13→16 cases; baselines stay **0/0** (verified by the gate and
  `tests/test_fp_rate_gate.py`).
- **Acceptance (met).** Versioned-scheme + bundle pairs in the corpus, baselines stay 0/0.

---

## G. G15 versioned-scheme leftovers — gap **G15** — **shipped**
- **Context.** Detector + collapse shipped for the C suffix scheme and the C++
  inline-namespace stamp (ICU 16428→657). Remaining per the registry `next_steps`.
- **Pointers.** `abicheck/versioned_symbol_scheme.py` (`_NS_VER`, `_dominant_ns_token`,
  `_scheme_key`); `post_processing.DetectVersionedSymbolScheme`.
- **Shipped.** (1) **Token vocabulary** — the `_NS_VER` regex already covered
  libc++ `__1`/`__2`, Abseil `lts_<date>`, and libstdc++ `__7`/`__8`; pinned with
  demangle-map tests (verified live against real mangled names from gcc-built
  fixtures). (2) **SONAME cross-check** — when a scheme is detected, the advisory
  now compares both sides' SONAME and, on a bump, appends a relink signal
  (`... dependents must relink ...`) so the collapse never hides it. (3)
  **Collapse count** — under the opt-in preset, the advisory records
  `caused_count` and its description reads `[N version-renames collapsed as
  compatible]` for the summary.
- **Acceptance (met).** libc++/Abseil/libstdc++ pairs collapse; SONAME bump still
  reported; summary shows "N version-renames collapsed".

---

## Recommended order
1. ~~**A1 (G4 decl extractor) + A2 (compile-DB `-I`)**~~ — **shipped/verified**: the
   clang AST-JSON backend (default) emits decls/types and feeds
   `reachable_declarations`/`reachable_types`; the `-p` bridge carries the build's
   include paths/flags into the parse. Both pinned by fast-lane regression tests.
2. ~~**D1 + D2 (eval source tier + CI)**~~ — **shipped**: `runner.py --tier
   source|both` records L3/L4/L5 coverage; `eval-suite.yml` runs the binary tier
   as a gating weekly/label lane (`--fail-on-drift`) and the source tier as a
   non-gating coverage lane. This is now the way to confirm A1/A2 *live* on
   snappy/ICU (the source-tier job).
3. ~~**B1, B2, A3**~~ — **shipped** (cheap, high-friction-removal UX/discovery fixes:
   `--show-data-sources` preview-only messaging, broadened build-flag vocabulary,
   `--lang c` → C++ auto-retry).
4. ~~**C1 (L4 scaling) + E2 (Bazel) + E3 (cross-compiler) + E4 (changed-scope
   mechanism) + F2 (FP corpus) + G (G15 leftovers)**~~ — **shipped**: scaling curve
   in `eval/SCALING.md`; real-aquery Bazel fixture; live gcc↔clang drift (+ a
   `_diff_toolchains` fix); changed-scope/cache L4 validated on zstd (LLVM-scale
   gated on a built tree); FP corpus grown to 16 cases (0/0); G15 token
   vocabulary + SONAME relink signal + collapse count.
5. **E1 (PE/Mach-O)** — close the platform-coverage hole (the remaining
   highest-value gap).
6. **E4 LLVM-scale, F1 (new ecosystems)** — depth & breadth as capacity allows.
