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
| P03 `--show-data-sources` preview-only | UX | **OPEN** | §B1 NEW |
| P04 `-H` hard-errors w/o castxml | env | resolved (tool present) | — |
| P05 clang L4 empty decl tables | C++ L4 | **OPEN** | §A1 (gap **G4**) |
| P06 serial L4 | perf | **shipped** (parallel) | §C1 (scaling validation) |
| P07 plain DB no toolchain | discovery | documented | §E3 NEW (validate) |
| P08 versioned-symbol noise | correctness | **shipped** (detector+collapse) | §G (gap **G15**) |
| P09 silent autotools | discovery | **shipped** (diagnostic) | — |
| P10 autotools bootstrap | discovery | documented | — (upstream) |
| P11 compare rename cost | perf | **shipped** (batch-demangle) | — |
| P12 meson `builddir` | discovery | **shipped** | — |
| P13 L4 infeasible on monorepo | perf/scope | documented + mitigations | §E4 (retry live) |
| P14 castxml no compile-DB `-I` | C++ L2 | **OPEN** | §A2 (gap **G16**/G4) |
| P15 castxml ✗ libstdc++ 13 | C++ L2/L4 | **OPEN** | §A1 (gap **G4**) |
| P16 `--lang c` aborts on extern "C" | UX | **OPEN** | §A3 (gap **G16**) |
| P17 thin build-option normalization | discovery | **OPEN** | §B2 NEW |
| P18 L5 coupled to L4 | UX | **shipped** (`graph-build`) | — |
| P19 L4 needs generated headers | discovery | **shipped** (hint) | — |
| P20 multi-`.so` pairing | discovery | documented + eval guard | §F2 NEW (FP corpus) |
| P21 oneDAL Bazel toolchain | discovery | documented | §E2 (validate Bazel live) |

---

## A. C++ source-ABI unblock (the highest-leverage cluster)

The single biggest gap the eval surfaced: **no real C++ source-ABI surface is
obtainable today** on a stock toolchain. castxml ≤0.6.3 cannot parse libstdc++ 13
(P15); the clang L4 extractor sidesteps that but emits only body fingerprints,
**zero declarations/types** (P05). Result: L4 is empty for the C++ libraries that
need it most. These map to existing gaps **G4** and **G16**.

### A1. libclang declaration/type extractor (P15, P05) — gap **G4**
- **Context.** `g4-header-ast-extractor.md` already plans "a libclang-based
  header-AST extractor alongside castxml". The eval is the concrete motivation:
  ICU/snappy yield `reachable_declarations: 0` today; castxml dies in
  `/usr/include/c++/13/bits/basic_string.h`.
- **Pointers.**
  - `abicheck/buildsource/source_extractors/clang.py` — current clang extractor
    (`extract` ~L1164); emits `SourceEntity` *body fingerprints*, not decl tables.
  - `abicheck/buildsource/source_extractors/castxml.py` — the declaration backend
    that breaks on modern libstdc++.
  - `abicheck/buildsource/source_extractors/base.py` — `SourceAbiExtractor` iface.
  - `abicheck/buildsource/source_abi.py` — `SourceAbiTu.reachable_declarations`
    (the field that comes back empty).
  - Registry: `UC-ARCH-header-only` / `UC-ARCH-c-library` evidence
    `abicheck/dumper_castxml.py`.
- **Approach.** Add a `libclang` (cindex) extractor producing real
  `SourceEntity` decls/types/typedefs/enums from the AST (not text fingerprints);
  prefer it over castxml when `python-clang`/`libclang` is present; fall back to
  castxml, then to the body-fingerprint clang path. Reuse the compile-DB flags
  per TU.
- **Acceptance.** snappy/ICU `--sources` runs return non-zero
  `reachable_declarations` / `reachable_types`; the 9 source-replay findings fire
  on a real C++ lib; new integration test on a compiled C++ fixture.
- **Effort·Risk.** L · medium (libclang version skew). **Do first — unblocks A2/A3, B, and all C++ validation.**

### A2. castxml/clang inherits compile-DB include paths (P14) — gap **G16**/G4
- **Context.** Public headers routinely `#include` *generated* headers
  (`snappy-stubs-public.h`); the `-H` path doesn't pass the build's `-I`, so L2
  fails `file not found` until the user adds `-I <builddir>` by hand.
- **Pointers.**
  - `abicheck/dumper_castxml.py` `_build_castxml_command` (~L302),
    `extra_includes` handling (~L131/L149).
  - `abicheck/build_context.py` — per-TU include flags already parsed
    (`_try_consume_include` ~L250, `_try_consume_isystem` ~L262).
  - Bridge point: the `-p`/`--compile-db` path in `abicheck/cli.py` `dump`.
- **Approach.** When a compile DB is supplied/auto-discovered, derive `-I`/
  `-isystem`/`-D`/`--target`/`--sysroot` from the matched compile unit and pass
  them into the header-AST invocation automatically.
- **Acceptance.** `dump <so> -H include/ -p build/` parses a public header that
  includes a generated header **without** a manual `-I`.
- **Effort·Risk.** S–M · low.

### A3. `--lang c` heuristic should warn, not abort (P16) — gap **G16**
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

---

## B. Net-new code fixes (NEW)

### B1. `--show-data-sources` is preview-only (P03) — NEW
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

### B2. Build-option normalization vocabulary is thin (P17) — NEW
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

---

## C. Performance validation

### C1. P06 parallel-L4 scaling (validate) — follow-up to shipped work
- **Context.** Parallel L4 shipped (deterministic). Measured freetype 42-TU only:
  25.8s → 19.1s (1.35×) — modest because L3/L5/serialization are serial and the
  TU count is small.
- **Pointers.** `abicheck/buildsource/source_replay.py` `run_source_replay`
  (phased parallel loop), `_l4_jobs`; `ABICHECK_L4_JOBS` env.
- **Approach.** Benchmark on larger TU counts (zstd 92; LLVM scoped to N changed
  TUs) to confirm the L4 fraction approaches N×; record in `eval/REPORT.md` source tier.
- **Acceptance.** A scaling curve (jobs=1/2/4/8) on ≥2 trees in the eval report.
- **Effort·Risk.** S · low (measurement only).

---

## D. eval-suite infrastructure (NEW)

### D1. Add the build/source (L3/L4/L5) tier to the runner
- **Context.** The new benchmark suite (`eval/runner.py`) runs only the binary
  (L0/L1) tier (22/22 verdicts match). The source tier (old `bsdrive.py`
  prototype) was not folded in.
- **Pointers.** `eval/runner.py` (`run`, `scan_one`), `eval/manifest.yaml`
  (`source:` repo/tags already present for zlib/zstd/snappy); the retired prototype
  logic is in git history (`eval/field-eval/scripts/bsdrive.py`).
- **Approach.** Add `runner.py --tier source`: for manifest entries with a
  `source:` block, clone at the tag, configure (or `--build-query`), run
  `dump --sources --collect-mode source-target`, record L3/L4/L5 coverage +
  timings into `results/`. Gate on `clang`/`cmake` presence; skip gracefully.
- **Acceptance.** `eval/REPORT.md` gains a source-tier table; reproducible.
- **Effort·Risk.** M · medium (needs toolchain; gate on availability).

### D2. Wire the suite into CI as a scheduled lane
- **Context.** The suite is a real-world verdict **regression guard** (`expect`
  in `manifest.yaml`); today it's manual.
- **Pointers.** `.github/workflows/` (mirror the `mutation.yml`/`performance.yml`
  scheduled-lane pattern); `eval/runner.py` exit on `verdict_matches_expected`
  drift.
- **Approach.** Weekly / label-triggered job: `pip install pyyaml`, run the binary
  tier, fail on any `expect` drift, upload `results/`. Network-gated (anaconda.org).
- **Acceptance.** Green scheduled lane; red on an injected verdict drift.
- **Effort·Risk.** S–M · low.

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

### E2. Bazel adapter, live (P21)
- **Pointers.** `abicheck/buildsource/adapters/bazel.py` (cquery/aquery jsonproto);
  oneDAL (`uxlfoundation/oneDAL`) or protobuf (Bazel builds).
- **Approach.** Capture a real `bazel aquery --output=jsonproto` from a small
  Bazel C++ project, feed via `--build-info`; verify L3 compile/link units.
- **Acceptance.** Non-empty L3 from a real aquery export.
- **Effort·Risk.** M · medium (Bazel toolchain heavy).

### E3. Cross-compiler toolchain capture (P07)
- **Pointers.** `abicheck/buildsource/compiler_record.py`
  (`.GCC.command.line` / `DW_AT_producer`); `adapters/cmake_file_api.py` (targets/
  toolchains). The DWARF-bearing conda libs from the eval (libuv, openblas, bzip2)
  are ready inputs.
- **Approach.** Compare a gcc-built vs clang-built same library; confirm toolchain
  identity is captured and drift surfaces.
- **Acceptance.** Toolchain/producer recorded; gcc↔clang drift visible.
- **Effort·Risk.** S–M · low.

### E4. L4 on a monorepo, live (P13, now feasible)
- **Context.** Bounded out earlier (hours). Now feasible with `source-changed`
  scope + P06 parallel + the per-TU cache.
- **Pointers.** `--collect-mode source-changed`, `run_source_replay(changed_paths=…)`,
  `SourceAbiCache` (`--build-cache-dir`). LLVM checkout flow in git history
  (`eval/field-eval/scripts` clone+configure).
- **Approach.** Configure LLVM, replay only a small changed-TU set, measure.
- **Acceptance.** Scoped L4 on LLVM completes in minutes with cached re-runs near-free.
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

### F2. Grow the FP-rate corpus with eval cases
- **Context.** The versioned-scheme and multi-`.so`-bundle (P20) shapes aren't in
  the FP gate.
- **Pointers.** `scripts/check_fp_rate.py` + `tests/test_fp_rate_gate.py`
  (baselines 0/0); `examples/case141` is a ready versioned-scheme fixture.
- **Acceptance.** Versioned-scheme + bundle pairs in the corpus, baselines stay 0/0.
- **Effort·Risk.** S · low.

---

## G. G15 versioned-scheme leftovers — gap **G15** (`partial`)
- **Context.** Detector + collapse shipped for the C suffix scheme and the C++
  inline-namespace stamp (ICU 16428→657). Remaining per the registry `next_steps`.
- **Pointers.** `abicheck/versioned_symbol_scheme.py` (`_NS_VER`, `_dominant_ns_token`,
  `_scheme_key`); usecase `UC-CHANGE-inline-ns-version`.
- **Remaining.** (1) token vocabulary: libc++ `__1`/`__2`, Abseil `lts_<date>`,
  libstdc++ versioned namespaces (partially handled; add tests/fixtures);
  (2) cross-check the detected token against the SONAME and still surface the bump
  as the relink signal; (3) report the collapse count in the verdict summary.
- **Acceptance.** libc++/Abseil pairs collapse; SONAME bump still reported; summary
  shows "N version-renames collapsed".
- **Effort·Risk.** M · low.

---

## Recommended order
1. **A1 (G4 libclang decl extractor)** — unblocks A2/A3, all C++ validation, real L4 value.
2. **D1 + D2 (eval source tier + CI)** — turns this research into a standing guard.
3. **B1, B2, A3** — cheap, high-friction-removal UX/discovery fixes.
4. **E1 (PE/Mach-O)** — close the platform-coverage hole.
5. **C1, E2–E4, F, G** — depth & breadth as capacity allows.
