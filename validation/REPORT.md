# abicheck real-world validation report

**Date:** 2026-06-01
**abicheck version:** 0.2.0 (`napetrov/abicheck` @ `main` / commit `2d43b4b`)
**Author:** automated validation run (Claude Code session)
**Purpose:** Validate the latest `main` against real-world C/C++ shared libraries,
quantify false positives / usability gaps, and produce an evidence base for the
next planning + improvement round.

---

## 1. Executive summary

I ran abicheck against **33 real shared-object comparisons** drawn from **11
curated version pairs across 7 upstream ecosystems** (oneTBB, OpenSSL,
Protobuf/abseil, ICU, libpng, zstd, libxml2). oneTBB is the headline target
because it is oneDAL's core threading dependency and already has ABI-policy work
in this repo (frozen-namespace). All binaries are real upstream release artifacts
(conda-forge), reproducible by digest.

**Headline outcomes:**

| Comparison class | Behaviour | Verdict quality |
|---|---|---|
| **Stripped ↔ stripped** (symbols-only; the common "compare two releases" case) | **Strong.** Correct verdicts on OpenSSL, libpng, zstd, Protobuf, ICU. | ✅ Trustworthy |
| **DWARF present, no headers** (e.g. conda oneTBB 2021.x) | **~90 % false-positive breaking findings** from `std::`/`tbb::detail` types. | ❌ Misleading by default |
| **Mixed DWARF ↔ stripped** (debug build vs released stripped binary) | **Avalanche of phantom breaks** (1 100+ "removed" core public types). | ❌ Actively wrong |

**The single most important finding:** in DWARF-only mode *without headers*,
abicheck treats **every type the compiler happened to emit DWARF for** as public
ABI surface — including standard-library internals and the library's own
documented-internal namespaces. On a genuinely ABI-compatible oneTBB minor bump
(2021.5 → 2021.9, same `libtbb.so.12` SONAME) this produces **216 "breaking"
findings, verdict `BREAKING`, 2.7 % binary-compatibility** — of which ~90 % are
not public ABI at all. A six-rule internal-namespace suppression collapses this to
**2 findings (99.1 % compatible)**, and the 2 residual findings are *also* false
positives. The real signal in that comparison is a single, correct, valuable RISK:
the GCC-11-built library now requires `CXXABI_1.3.13`.

abicheck's genuine strengths were also clearly visible: symmetric symbols-only
comparison is reliable, runtime version-requirement detection
(`GLIBC`/`CXXABI`/`OPENSSL_*`) is real and useful, fingerprint-based **rename
detection** correctly tracks ICU's `_73`→`_75` symbol-suffix scheme, and
major-version breaks are detected with correct SONAME recommendations.

---

## 2. Environment & method

> **Note on the "oneDAL build machine".** This session ran in an ephemeral,
> network-isolated cloud container (Ubuntu 24.04, gcc 13.3 / clang 18, 4 vCPU,
> 15 GiB RAM) — there is no persistent oneDAL build host attached to it.
> `castxml`, `abidiff`, and `abi-compliance-checker` are **not** installed, so
> this run exercises abicheck **standalone** in the exact mode most users hit:
> comparing two pre-built `.so` files with no headers. To keep oneDAL relevance,
> validation is centered on **oneTBB** (oneDAL's threading runtime) plus a broad
> set of C/C++ libraries with known ABI history. All inputs are real upstream
> release binaries pulled from conda-forge (reproducible by version), not
> synthetic fixtures.

**Harness:** `validation/scripts/run_matrix.py` reads `validation/data/manifest.json`,
extracts every real (non-symlink) ELF `.so` from each package, matches old↔new by
logical name (e.g. `libtbb`, `libcrypto`), and runs
`abicheck compare … --format json --recommend`, capturing verdict, counts, exit
code, wall time, and stderr into `validation/data/results.json`.

**Debug-info reality (drives everything below):** conda-forge ships oneTBB **2021.x
with DWARF** but **2022.x stripped**; libxml2 **2.9.7 with DWARF**, **2.9.9
stripped**; OpenSSL/Protobuf/ICU/libpng/zstd are **stripped both sides**. This
asymmetry is realistic and is exactly what surfaces the most severe issues.

---

## 3. Validation matrix

| Pair | Library | Old → New | Mode (old→new) | Expectation | Why chosen |
|---|---|---|---|---|---|
| T1 | oneTBB | 2021.5.0 → 2021.9.0 | dwarf→dwarf | compatible | same `libtbb.so.12`; oneDAL-relevant |
| T2 | oneTBB | 2021.9.0 → 2022.0.0 | dwarf→sym | compatible | series bump, ABI retained; **mixed mode** |
| T3 | oneTBB | 2022.0.0 → 2022.3.0 | sym→sym | compatible | patch within 2022 |
| P1 | Protobuf | 6.33.2 → 6.33.5 | sym→sym | compatible | patch, same SONAME 33 |
| P2 | Protobuf | 6.33.5 → 7.34.1 | sym→sym | breaking | major SONAME bump 6→7 |
| O1 | OpenSSL | 3.5.4 → 3.5.6 | sym→sym | compatible | patch within 3.5 |
| O2 | OpenSSL | 3.5.6 → 3.6.0 | sym→sym | compatible | minor, OpenSSL3 ABI-stable |
| I1 | ICU | 73.2 → 75.1 | sym→sym | breaking | major; versioned-symbol suffix change |
| N1 | libpng | 1.6.53 → 1.6.58 | sym→sym | compatible | patch within libpng16 |
| Z1 | zstd | 1.5.5 → 1.5.7 | sym→sym | compatible | minor within `libzstd.so.1` |
| X1 | libxml2 | 2.9.7 → 2.9.9 | dwarf→sym | compatible | patch; **mixed mode** |

Full per-`.so` results: `validation/data/results.json`. Exact filenames/digests:
`validation/data/manifest.json`.

---

## 4. Results overview

33 comparisons. Verdicts vs. expectation:

| Outcome | Count | Notes |
|---|---|---|
| Correct compatible (sym→sym) | 14 | OpenSSL, libpng, zstd, Protobuf sublibs, TBB 2022 patch |
| Correct breaking (true major break) | 7 | Protobuf 6→7, ICU 73→75 core libs |
| **False breaking — DWARF-no-header noise** | 3 | TBB T1 (`libtbb`/`libtbbmalloc`/`_proxy`) |
| **False breaking — mixed DWARF↔stripped** | 5 | TBB T2 (4 libs), libxml2 X1 |
| **False breaking — RTTI-lambda churn** | 1 | Protobuf patch `libprotobuf` |
| Verdict inconsistency within one bump | — | ICU sublibs split BREAKING vs COMPATIBLE_WITH_RISK |

**Exit-code behaviour was internally consistent** with the verdicts (BREAKING→4,
COMPATIBLE/…_WITH_RISK→0), so CI gating works as designed — the problem is the
*verdict*, not the plumbing.

---

## 5. What works well (keep / amplify)

1. **Symmetric symbols-only comparison is reliable.** Every stripped↔stripped
   compatible pair (OpenSSL ×2, libpng, zstd, TBB 2022 patch, Protobuf sublibs)
   returned `COMPATIBLE`/`COMPATIBLE_WITH_RISK`. This is the dominant real-world
   workflow and it is trustworthy. `compare-release` over two release directories
   produced a clean per-library table (OpenSSL 3.5.6→3.6.0: `libcrypto`
   COMPATIBLE, `libssl` COMPATIBLE_WITH_RISK).

2. **Runtime version-requirement detection is genuinely valuable.**
   `symbol_version_required_added` correctly flagged new
   `CXXABI_1.3.13/1.3.15`, `GLIBC_2.14`, and `OPENSSL_3.6.0` requirements — real
   "won't load on older systems" deployment risks that a pure symbol diff misses.

3. **Rename detection handles ICU's symbol-versioning scheme.** ICU renames every
   symbol with a version suffix each release (`u_feof_73 → u_feof_75`,
   `icu_73:: → icu_75::`). Fingerprint matching paired them as renames with
   size/confidence evidence instead of remove+add — impressive and correct
   mechanically.

4. **Major-break detection + release recommendation is sound.** Protobuf 6→7 and
   ICU 73→75 → `BREAKING` with `version_bump: major` and
   `soname_action: bump_performed` (it correctly recognised the SONAME *was*
   already bumped). For the oneTBB false-break it said `bump_missing` — the logic
   is right; only its breaking-input was wrong.

5. **The breakdown/scoping scaffolding already exists.** Output already contains
   `abi_surface_breakdown` (public / rtti_churn / internal_churn), a "Filtered
   (internal/private)" bucket, and `coverage_warnings`. The machinery to fix the
   false positives below is *present* — it just isn't catching the right cases by
   default.

---

## 6. False positives (root-caused, with evidence)

Evidence excerpts: `validation/data/false_positive_evidence.json`.

### FP-1 — Standard-library & internal-namespace types treated as public ABI (DWARF, no headers)

**Case:** oneTBB 2021.5.0 → 2021.9.0, `libtbb.so.12` (dwarf→dwarf).
**Reported:** verdict `BREAKING`, **216 breaking** (marked public=206), 2.7 % compatible.
**Reality:** ABI-compatible minor bump, same SONAME.

Namespace breakdown of the 216 "breaking" findings:

| Owner | Count | Public ABI? |
|---|---|---|
| `tbb::detail::*` (oneTBB documented-internal) | 139 | No |
| `std::` / `__gnu_cxx` (libstdc++) | 54 | No |
| anonymous `<lambda()>` | 1 | No |
| other (mangled/plain) | 22 | mixed |

Representative findings:
- `type_field_removed: std::__cxx11::basic_string<…>::npos`
- `type_field_removed: std::integral_constant<bool, false>::value`
- `type_removed: std::__atomic_base<int>`

**Root cause (proven):** the two builds were compiled with **different
toolchains** — DWARF producer strings are `GNU … 9.4.0` (2021.5) vs `GNU …
11.3.0` (2021.9), both with LTO. `npos`/`value` are `static constexpr` members;
the number of `npos` member DIEs differs between the two objects (**37 vs 18**),
so newer GCC/LTO simply emitted fewer static-member DIEs. abicheck reads that as
"field removed." **None of these are ABI changes** — `std::basic_string`'s layout
is fixed by the libstdc++ ABI. The library inlines STL, so the STL types leak into
DWARF and get scored as the library's own surface.

**Proof it's recoverable:** applying `validation/suppress_internal.yaml` (six
`namespace:` rules for `std`, `__gnu_cxx`, `tbb::detail`) drops the result from
**216 → 2 breaking (99.1 % compatible)**, with **241 changes suppressed**. The
feature works; the *default* does not protect users.

### FP-2 — Residual: anonymous & reserved-name types still scored

After FP-1 suppression, the 2 remaining "breaking" findings are also false:
- `type_removed: <lambda()>` — anonymous closure types have no cross-version ABI
  identity and must never be ABI surface.
- `typedef_removed: __native_type` — a libstdc++/pthread-internal
  (`__gthread`/`std::thread`) typedef; double-underscore reserved name.

(The one *correct* finding in that comparison is the RISK
`symbol_version_required_added: CXXABI_1.3.13` — true verdict should be
**COMPATIBLE_WITH_RISK**.)

### FP-3 — RTTI/typeinfo churn of internal lambda types scored as `var_removed`

**Case:** Protobuf 6.33.2 → 6.33.5, `libprotobuf` (sym→sym).
**Reported:** `BREAKING`, 6 `var_removed`.
**Reality:** patch release, same SONAME 33; ABI-compatible.

All six "removed variables" are `_ZTI…`/`_ZTS…` (typeinfo / typeinfo-name)
symbols for **anonymous lambdas** nested in
`google::protobuf::io::Printer::WithDefs/WithVars`. These RTTI symbols churn
across builds (lambda identity is not stable) and are not part of the documented
ABI. Notably the existing `rtti_churn` classifier did **not** catch them — they
were scored as public `var_removed` and drove a `BREAKING` verdict. (Compare:
`libprotobuf-lite`/`libprotoc` in the same pair correctly returned
COMPATIBLE_WITH_RISK.)

### FP-4 — Mixed DWARF↔stripped produces a phantom-removal avalanche

**Cases:** oneTBB 2021.9→2022.0 `libtbb` (**1 137 breaking**); libxml2 2.9.7→2.9.9
`libxml2` (**1 149 breaking**). Both are compatible bumps.

When the old side has DWARF and the new side is stripped, abicheck has rich types
on one side and none on the other, so it reports **everything as removed/changed**:
- libxml2: **165 of 211** `type_removed` are core public types that obviously still
  exist (`_xmlNode`, `_xmlDoc`, `_xmlEntity`, `_IO_FILE`, …); plus 590
  `typedef_removed` and 142 `func_return_changed` where the new return type is
  literally `?` (e.g. `initxmlDefaultSAXHandler`: `old: void → new: ?`).
- TBB 2021.9→2022.0: 460 `type_removed` + 409 `typedef_removed`.

This is the most dangerous class because **comparing a debug CI build against a
released stripped binary is a normal thing to do**, and the result is hundreds of
confident, wrong "breaking" findings. abicheck *does* emit a coverage warning, but
still renders `BREAKING` with full counts. The asymmetry of evidence is the bug:
absence of a type on the stripped side is absence of *evidence*, not evidence of
*removal*.

---

## 7. Usability gaps

1. **No safe default for "DWARF but no headers."** This is the most common state
   of a real distro/conda `.so`, and it is precisely where abicheck is least
   trustworthy. `--scope-public-headers` can't help without headers (it warns it
   "fell back to the full export table … treat as manual-review-required"), yet
   the verdict is still emitted as a hard `BREAKING`. Users have no way to know the
   216 findings are 90 % noise without reading every line.

2. **The human-facing `review` digest leads with the noise.** For oneTBB T1 the
   "Top impacted symbols" list is entirely `std::…` types — the worst possible
   first impression for a PR comment, even though a "Filtered (internal/private):
   148" bucket exists alongside it.

3. **Recovering the truth requires hand-authored suppressions.** The fix for FP-1
   exists but is manual: the user must already know to exclude `std`, `__gnu_cxx`,
   and `tbb::detail`. There is no built-in "ignore standard-library &
   reserved/internal namespaces" default or profile.

4. **Verdict inconsistency across sub-libraries of one release.** ICU 73→75 yields
   `BREAKING` for `libicuuc`/`libicui18n`/`libicutu`/`libicudata` but
   `COMPATIBLE_WITH_RISK` for `libicuio`/`libicutest` (rename detection paired all
   their symbols). A consumer reads mixed verdicts for a single coordinated major
   bump. Relatedly, ICU's suffix renames are *binary breaks* (old `u_feof_73` is
   gone) but are filed as RISK, so a binary-only consumer is under-warned.

5. **Discoverability of the right knobs.** `compare --help` is ~120 lines; the
   options that actually matter for these cases (`--public-symbols-list`,
   `--scope-public-headers`, `--suppress`, `--policy`) are buried among PE/Mach-O/
   debuginfod options irrelevant to the Linux release-diff flow.

---

## 8. Performance & scale

Wall-clock per comparison on 4 vCPU (cold, single-process):

| Library (symbols) | Time |
|---|---|
| `libicui18n` 73→75 (~9.5k syms) | **30.3 s** |
| `libtbb` 2021.9→2022.0 (mixed, 1.1k findings) | 8.0 s |
| `libtbb` 2021.5→2021.9 (dwarf, 260 findings) | 16.2 s |
| `libicuuc` (~3.8k syms) | 6.1 s |
| `libcrypto` (~6k syms, sym-only) | 2.8 s |
| typical small lib | < 1 s |

No crashes, hangs, or tracebacks across all 33 runs. Largest absolute cost is the
DWARF/rename-detection path on large symbol tables (ICU i18n at 30 s); symbols-only
on a 6k-symbol `libcrypto` is fast (2.8 s). Worth a scaling benchmark before
recommending abicheck on very large surfaces (oneDAL's own `libonedal_core` is far
bigger than anything here).

---

## 9. Recommendations for the next planning round (prioritized)

> **Code-level root cause + architectural fix evaluation for every item below is
> in `validation/DESIGN_ANALYSIS.md`, and each scenario is encoded as a
> regression test in `tests/test_real_world_false_positives.py`.**

**P0 — Stop scoring non-ABI types by default.** ✅ *Implemented in this PR for the
universal cases (FP-1, FP-2).*
- Exclude `std::`, `__gnu_cxx::`, `__cxxabiv1::`, `__cxx11::` and anonymous
  (`<lambda>`, unnamed) types from type diffing via the single-source predicate
  `model.is_non_abi_surface_type()` (used by `diff_types`). Measured: oneTBB
  216→168 breaking, zero suite regressions.
- *Still open:* library-internal namespaces like `tbb::detail` (FP-1b) — left to
  the policy/frozen-namespace layer, not hardcoded; treat static-member DIE
  presence as non-signal.

**P0 — Make mixed DWARF↔stripped safe.**
- When one side lacks DWARF, **do not** report `type_removed`/`typedef_removed`/
  signature-change for types the stripped side cannot confirm. Auto-degrade to a
  symmetric symbols-only comparison (or mark such findings `unconfirmed`, excluded
  from the verdict). Absence of debug info ≠ removal. (FP-4)

**P1 — Internal-namespace defaults & profiles.**
- Ship a built-in "internal namespace" default (and let libraries declare their own,
  e.g. `tbb::detail`, `*::detail::*`, `*::internal::*`) so the frozen-namespace
  machinery applies without hand-written suppressions. Validate by reproducing the
  216→2 collapse automatically.

**P1 — Fix RTTI-lambda classification.**
- Route `_ZTI`/`_ZTS` symbols of anonymous/local types into `rtti_churn` (or
  filter them) instead of public `var_removed`. (FP-3)

**P1 — Honest verdict under low coverage.**
- When the public surface can't be resolved (no headers, fell-back scoping), cap
  the verdict at `UNCONFIRMED`/manual-review rather than a hard `BREAKING`, and say
  so in `--stat`/`review`. (Usability #1)

**P2 — Reporting & ergonomics.**
- In `review`/`markdown`, rank "Top impacted symbols" by *public* findings, push
  std/internal to a collapsed section. (Usability #2)
- Reconcile per-sub-library verdicts for a coordinated bump, and reconsider whether
  symbol-suffix renames (ICU) should read as binary-breaking. (Usability #4)
- A short "Linux release diff" quick-start / recipe and a slimmer default help.
  (Usability #5)

**P2 — oneDAL/oneTBB follow-up.**
- Re-run T1/T2 against oneTBB built **from source with public headers**
  (`oneapi/tbb.h`) and matched toolchains to separate true ABI deltas from the
  toolchain/STL noise measured here — then scale-test on a full oneDAL
  `libonedal_core.so`.

---

## 10. Reproducibility

```bash
pip install -e ".[dev]" zstandard
# 1. fetch the curated binaries (conda-forge; see data/manifest.json for exact files)
#    each URL is https://conda.anaconda.org/conda-forge/linux-64/<file>
# 2. extract .so from each package (.conda = zip>zstd>tar ; .tar.bz2 = tar)
# 3. run the matrix
python validation/scripts/run_matrix.py     # -> validation/data/results.json
# 4. reproduce the FP-1 recovery
abicheck compare <tbb-2021.5>/libtbb.so.12.5 <tbb-2021.9>/libtbb.so.12.9 \
  --suppress validation/suppress_internal.yaml --stat   # 216 -> 2 breaking
```

**Committed artifacts:**
- `validation/REPORT.md` — this report
- `validation/data/manifest.json` — the 11-pair matrix (exact upstream files)
- `validation/data/results.json` — all 33 comparison results
- `validation/data/false_positive_evidence.json` — FP exemplars (FP-1/3/4)
- `validation/suppress_internal.yaml` — the internal-namespace suppression
- `validation/scripts/run_matrix.py` — the harness

*Binaries are not committed (size); they are reproducible from the manifest.*
