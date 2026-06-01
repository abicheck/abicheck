# Real-world ABI validation report — June 2026

**Tool version:** abicheck @ `main` (commit `841dd19`, "Merge #270 — harden
real-world package validation").
**Date:** 2026-06-01.
**Author:** automated validation run (Claude Code agent).
**Purpose:** Take the latest `main` and exercise it against *real* shipped
binaries to (a) confirm it produces correct verdicts, (b) surface false
positives, usability gaps, and rough edges, and (c) collect concrete data to
seed the next planning/improvement round.

Raw machine-readable artifacts referenced below live in
[`realworld-2026-06/`](realworld-2026-06/README.md) — see its
`README.md` for the file inventory (harness scripts, per-pair result lines,
slimmed reports, and sweep summaries).

---

## 1. Executive summary

abicheck is **correct and robust** on real binaries: across 100 distinct
system libraries self-compared and 14 real cross-version oneDAL pairs, there
were **zero crashes, zero Python tracebacks, zero wrong verdicts on identical
input, and zero false-positive breaks on identical input**. Every real SONAME
bump was detected, and the multi-library `compare-release` workflow correctly
matched libraries across major SONAME changes and flagged removed libraries.

The dominant problems are **not correctness, they are signal-to-noise and
ergonomics on header-less C++ binaries** — which is the *most common*
real-world ABI-checking scenario (shipped, stripped-of-DWARF release `.so`
files):

1. **Internal/RTTI churn dominates the "breaking" count.** For oneDAL
   2025.11→2026.0 `libonedal_core`, **98% of the 1,709 "breaking" findings are
   RTTI symbols (`_ZTI`/`_ZTS`/`_ZTV`, 97%) or `internal`-namespace symbols.**
   The genuinely-actionable public-API breaks (~33) are buried. The tool
   *detects* the underlying cause (`visibility_leak`) but does not down-weight,
   group, or segregate the resulting churn in the verdict math.
2. **Human output shows raw mangled C++ names.** Additions, leaked symbols, and
   visibility-leak lists print `_ZN6oneapi3dal10covariance2v1...`. There is no
   demangle option for report output, even though `abicheck/demangle.py`
   exists. For a C++ library this makes the report close to unreadable.
3. **The default `--scope-public-headers` emits an alarming "UNCONFIRMED /
   manual-review-required" warning on every header-less comparison** — i.e. on
   the common case. It is technically accurate but reads like an error.
4. **Self/no-op compares are not a clean "no changes".** Comparing an identical
   file (same SHA-256) against itself reports `total_changes: 1` because the
   single-snapshot `visibility_leak` quality detector fires regardless of
   whether anything changed.

None of these are blockers; all are precision/UX investments that would
materially improve the experience on exactly the libraries people most want to
check.

---

## 2. Environment & methodology

| Aspect | Value |
|---|---|
| Host | Ephemeral Linux container (kernel 6.18), x86-64, Python 3.11.15 |
| Tools present | `gcc`, `g++`, `objdump`, `readelf`, network access |
| Tools **absent** | `castxml`, `abidiff` (libabigail), `abi-compliance-checker` |

> **Note on the "oneDAL build machine".** The goal referenced running on the
> oneDAL build machine. This session executed in an isolated cloud container,
> not that machine, so castxml/abidiff/ABICC parity lanes could not run here.
> That is *not* a gap for this report's purpose: real-world ABI checking of
> **shipped** libraries is overwhelmingly done in symbols-only mode (no headers,
> no DWARF), which is exactly what we exercised. Re-running the type-level
> detectors against oneDAL with its real public headers is the recommended
> follow-up on the actual build machine (see §7).

**Corpus.** Real binaries only — nothing synthetic:

* **oneDAL / `daal` PyPI wheels** at three versions — `2024.7.0` (SONAME
  `.so.2`), `2025.11.0` (`.so.3`), `2026.0.0` (`.so.4`). These are real,
  production, manylinux release binaries (`libonedal_core.so` is 105 MB /
  66,266 exported functions, not stripped of the symbol table but carrying **no
  DWARF**). This is the user's headline target.
* **100 distinct system shared libraries** from `/usr/lib/x86_64-linux-gnu`
  (stratified: 20 smallest, 20 largest, 60 random-middle), incl. `libLLVM`,
  `libclang`, `liblldb`, `libpoppler`, `libicu`, `libabsl_*`, `libgallium`.

**Procedures.**

* **Self-comparison sweep** (`selfsweep.py`): each library compared against
  itself. Ground truth = identical bytes ⇒ no real ABI change. *Any* non-clean
  verdict, breaking finding, crash, or traceback is a defect. 100 libraries.
* **Cross-version oneDAL** (`harness.py`): `compare` over every matched
  library across version pairs (adjacent `.so.3→.so.4`, mid `.so.2→.so.3`, far
  `.so.2→.so.4`), capturing verdict, summary counts, change-kind histogram,
  timing, and stderr warnings. 14 library pairs.
* **`compare-release`**: the full multi-library release workflow over the two
  release `lib/` directories (2025.11.0 vs 2026.0.0).
* **Output ergonomics**: rendered `markdown` reports and inspected the
  human-facing surface.

---

## 3. Correctness & robustness results

### 3.1 Self-comparison sweep (100 real libraries)

```json
{ "total": 100, "nonzero_rc": 2, "tracebacks": 0, "no_output": 2,
  "non_compatible_verdict": 0, "fp_breaks": 0,
  "kind_histogram": { "visibility_leak": 46 } }
```

* **0 tracebacks, 0 wrong verdicts, 0 false-positive breaks.** Deterministic and
  precise on identical input — strong result on a heterogeneous 477 MB corpus.
* **46/100** libraries flagged `visibility_leak` (a *quality* finding, not a
  diff) — expected; many distro libs do leak internal symbols.
* **The 2 "failures" are correct rejections, not bugs:** `libncurses.so` and
  `libncursesw.so` are 31-byte **GNU ld linker scripts**
  (`INPUT(libncurses.so.6 -ltinfo)`), not ELF. abicheck correctly emits
  "Cannot detect format" + exit 2. See §5.4 for the usability angle.

### 3.2 Cross-version oneDAL verdicts (all 14 pairs)

| Pair | Library | Verdict | Total changes | Breaking | Time |
|---|---|---|---:|---:|---:|
| 2025.11→2026.0 | libonedal | COMPATIBLE_WITH_RISK | 151 | 0 | 2.5 s |
| 2025.11→2026.0 | libonedal_core | **BREAKING** | 10,483 | 1,709 | 12.7 s |
| 2025.11→2026.0 | libonedal_parameters | COMPATIBLE | 5 | 0 | 0.3 s |
| 2025.11→2026.0 | libonedal_thread | **BREAKING** | 40 | 4 | 0.3 s |
| 2024.7→2026.0 | libonedal | BREAKING | 18,998 | 4,420 | 6.8 s |
| 2024.7→2026.0 | libonedal_core | BREAKING | 109,136 | 26,457 | 84.9 s |
| 2024.7→2026.0 | libonedal_parameters | BREAKING | 220 | 44 | 0.3 s |
| 2024.7→2026.0 | libonedal_thread | BREAKING | 5,437 | 202 | 2.1 s |
| 2024.7→2025.11 | libonedal_core | BREAKING | 101,408 | 24,772 | 72.4 s |
| 2024.7→2025.11 | libonedal_dpc | BREAKING | 64,799 | 14,793 | 25.5 s |
| … | (6 more) | … | … | … | … |

**Verdict-level correctness is good.** oneDAL bumps the major SONAME every
release (`.so.2`→`.so.3`→`.so.4`), declaring intentional ABI breaks; abicheck
reports BREAKING and recommends a major bump everywhere a real break exists, and
COMPATIBLE/COMPATIBLE_WITH_RISK where a sub-library only gained symbols.

### 3.3 `compare-release` (full release directories)

`compare-release 2025.11.0/lib 2026.0.0/lib` → verdict **BREAKING** (exit 4),
16 s total. It correctly:

* matched all 4 common libraries **across the `.so.3`→`.so.4` SONAME bump**;
* reported the 2 **removed** libraries (`libonedal_dpc.so.3`,
  `libonedal_parameters_dpc.so.3`) as `unmatched_old` — a real packaging-level
  break for DPC++/SYCL consumers;
* aggregated per-library verdicts into one release verdict.

---

## 4. Real-world ABI insights abicheck surfaced (the good)

These are genuine, useful product outputs worth highlighting as wins:

1. **Dependency-symbol leakage, with attribution and a fix.** The
   `symbol_leaked_from_dependency_changed` messages identify the *source*
   library and give actionable advice, e.g.:
   > Symbol `_ZTIN6oneapi3dal…spmd_policy_base…` was added but appears to
   > originate from `libstdc++.so.6` … the library is leaking dependency
   > symbols into its public ABI surface. Consider applying `-fvisibility=hidden`.
2. **`soname_bump_unnecessary`.** For the `libonedal` umbrella library, abicheck
   detected the SONAME bumped but found *no* binary-incompatible change in that
   library, and flagged the bump as potentially unnecessary — a genuinely
   valuable release-engineering insight. (Caveat in §5.5.)
3. **Whole-library removal detection** via `compare-release` `unmatched_old`.
4. **`abi_surface_explosion`** fired on the far comparison — a useful
   high-level signal that the surface changed wholesale.
5. **Release recommendations** (`version_bump`, `soname_action`) were
   self-consistent with the findings in every case.

---

## 5. Findings: false positives, noise, and usability gaps

### 5.1 ⭐ Internal/RTTI churn dominates the breaking count (precision)

For **2025.11→2026.0 `libonedal_core`** (1,709 breaking findings):

| Category of breaking finding | Count | Share |
|---|---:|---:|
| RTTI symbols (`_ZTI` / `_ZTS` / `_ZTV`) | 1,668 | **97%** |
| Contains `internal` namespace | 1,320 | 77% |
| RTTI **or** `internal` **or** `detail` | 1,676 | **98%** |
| Genuinely public, non-RTTI | ~33 | ~2% |

The 4 breaking findings in the tiny `libonedal_thread` pair are *all*
`_ZTI`/`_ZTS` typeinfo symbols for `daal::services::**internal**::EmptyDeleter`
/ `DefaultDeleter` — i.e. internal RTTI churn reported with full breaking
weight.

**Why it matters.** This is the single biggest gap for real-world C++ usage.
Libraries that don't build with `-fvisibility=hidden` (oneDAL, and ~46% of the
distro libs we sampled) expose thousands of internal/RTTI symbols; ordinary
refactors then generate thousands of "breaking" findings that drown the few
that consumers actually care about. abicheck already *knows* these are internal
(it raises `visibility_leak`) but doesn't act on that knowledge in scoring.

**Improvement directions** (for planning):
* Treat `_ZTI`/`_ZTS`/`_ZTV` as **coupled to their underlying type**: collapse
  the typeinfo/typeinfo-name/vtable trio into one finding per type instead of
  three independent breaking entries, and de-duplicate against the type-level
  change when DWARF is present.
* When `visibility_leak` is detected, offer (or auto-apply with a flag) an
  **internal-namespace heuristic scope** (`internal` / `detail` / `impl`
  namespace components, anonymous namespaces) and report a split:
  *"public-surface breaking: N, internal-churn breaking: M (suppressed)."*
* Surface a one-line **"of X breaking findings, Y are internal/RTTI churn"**
  banner so users immediately understand the real blast radius.

### 5.2 ⭐ No demangling in human-facing output (usability)

`markdown`/`text` reports print raw mangled names everywhere a symbol is named
(Additions, `visibility_leak` lists, leaked-dependency lines). Example actual
output line:

```
- New public function: _ZN6oneapi3dal10covariance2v113compute_inputINS1_4task2v17computeEEC2ERKS7_
```

There is **no `--demangle` option** for output (the only `demangle` mention in
`compare --help` is for `--public-symbol` *input*). For a C++ library this is a
severe readability tax. `abicheck/demangle.py` already exists and is used
internally for rename detection — wiring it into the reporter (default-on for
TTY/markdown, with `--no-demangle` to keep mangled) is low-cost, high-value.

### 5.3 ⭐ Alarming default-scoping warning on the common header-less case

`--scope-public-headers` is **on by default**. With no headers (the norm for
shipped binaries), every run prints:

> Warning: --scope-public-headers could not resolve the public surface … fell
> back to the full export table. Compatibility is **UNCONFIRMED** — treat this
> result as **manual-review-required**, not a clean public surface.

It is accurate but reads like a failure, and it fires on the majority real-world
path. Suggestions: soften wording, make it a single concise note rather than a
scary multi-clause warning, and/or auto-downgrade to symbols-only mode silently
with an *info*-level line. Pairs naturally with §5.1's internal-scope heuristic
as a better default fallback than "full export table."

### 5.4 Header-less / no-DWARF runs print two warnings per file, per side

Each library emits both a `dumper.py:634 UserWarning` (Python `warnings`
channel) and a CLI `Warning:` line, ×2 sides. In `compare-release` over a real
release this produces a wall of duplicated warnings before the report. Consider
deduping and routing all advisory text through one channel.

### 5.5 `soname_bump_unnecessary` ignores cross-library ABI coupling

`libonedal` was flagged `soname_bump_unnecessary`, but oneDAL bumps **all**
sub-library SONAMEs in lockstep precisely because `libonedal_core` (which the
umbrella is coupled to) *did* break. Per-library SONAME analysis can therefore
emit a misleading "unnecessary" signal for a deliberately-coordinated bump.
`compare-release` has the whole-release view needed to suppress this when a
sibling/dependency in the same release set broke — worth wiring through.

### 5.6 Linker-script inputs fail with a generic error

Pointing at the conventional dev symlink `libfoo.so` that is actually a GNU ld
linker script (`INPUT(libfoo.so.6 …)`) yields "Cannot detect format" + exit 2.
Since `*.so` is the *natural* path a user supplies, abicheck could detect the
`INPUT(...)` directive and either follow it to the resolved `.so.N` or emit a
targeted hint.

### 5.7 Self/no-op compare is not a clean "NO_CHANGE"

Comparing a file to itself (identical SHA-256) returns verdict `COMPATIBLE`
with `total_changes: 1` (the `visibility_leak` quality finding), not a clean
"no changes." Defensible (it's a single-snapshot quality signal), but a user
sanity-checking with a self-compare may be surprised. Consider distinguishing
"diff changes" from "single-snapshot quality observations" in the summary, or
suppressing single-snapshot findings when `old == new`.

---

## 6. Performance & scale data

| Metric | Value |
|---|---|
| Self-compare median (100 libs) | **0.25 s** |
| Self-compare max | 19.9 s (`libLLVM.so.20.1`, 136 MB) |
| 477 MB / 98 libs self-compared in | 119 s total |
| oneDAL core, 10.5 k changes | 12.7 s |
| oneDAL core, 109 k changes | **84.9 s** |

**Time scales with diff size, not binary size.** libLLVM (136 MB, ~0 real
changes) self-compares in 20 s — that's parse-bound — while the 105 MB oneDAL
core with 109 k changes takes 85 s — that's diff/classify/serialize-bound. The
big-diff path (tens of thousands of findings) is the one to profile if speed
becomes a concern; it also produces 80–90 MB JSON reports, which argues for the
§5.1 grouping work on both UX and performance grounds.

---

## 7. Detector coverage observation

Only **18 of 145 `ChangeKind`s** fired across all oneDAL real-world diffs:

```
func_removed_elf_only(176723) var_removed(77101)
symbol_leaked_from_dependency_changed(68392) func_added(7433)
symbol_binding_strengthened(3427) var_added(2047) func_likely_renamed(208)
visibility_leak(15) soname_changed(14) symbol_version_required_added(12)
needed_added(8) abi_surface_explosion(7) needed_removed(6)
symbol_version_required_added_compat(5) symbol_version_required_removed(5)
inline_namespace_moved(4) symbol_size_changed(3) soname_bump_unnecessary(2)
```

This is expected: **symbols-only shipped binaries exercise only the
ELF-symbol-level detectors.** The 127 type-level detectors (struct field
changes, enum value changes, parameter type changes, vtable layout, …) require
headers or DWARF. **Recommended follow-up on the real oneDAL build machine:**
re-run with oneDAL's public headers (`-H`) and/or a debug build, to validate
the type-level detectors and the `--scope-public-headers` path against ground
truth — that is the half of the tool this corpus could not reach.

---

## 8. Prioritized recommendations (planning input)

| # | Item | Type | Impact | Rough effort |
|---|---|---|---|---|
| R1 | Group `_ZTI`/`_ZTS`/`_ZTV` per type; de-dup RTTI churn | precision | **High** | M |
| R2 | Internal-namespace scope heuristic + "public vs internal-churn" split when `visibility_leak` fires | precision | **High** | M |
| R3 | `--demangle` (default-on) for human output, via existing `demangle.py` | usability | **High** | S |
| R4 | Soften/condense the `--scope-public-headers` fallback warning; quiet, deduped advisory channel | usability | Med | S |
| R5 | `soname_bump_unnecessary`: suppress when a coupled sibling broke in the same `compare-release` set | correctness/noise | Med | M |
| R6 | Follow GNU ld `INPUT(...)` linker scripts (or targeted hint) | usability | Low | S |
| R7 | Distinguish single-snapshot quality findings from diff changes on self/no-op compares | usability | Low | S |
| R8 | Re-run on the oneDAL build machine **with headers/DWARF** to validate the 127 type-level detectors | coverage | **High** | M |

**R1–R3 are the highest-leverage:** together they convert the oneDAL core
report from "10,483 changes, 1,709 breaking, mangled" into roughly "tens of
real public breaks, demangled" — the difference between an unusable wall and an
actionable review.

---

## 9. Reproduction

```bash
pip install -e ".[dev]"
# oneDAL real binaries — download and unzip the wheels anywhere:
mkdir -p wheels && cd wheels
pip download --no-deps daal==2024.7.0 daal==2025.11.0 daal==2026.0.0
for w in daal-*.whl; do unzip -q -o "$w" -d "${w%.whl}"; done   # libs under */*.data/data/lib/*.so.*
cd ..

# harness.py auto-discovers `daal-*` dirs in the args (or cwd / /tmp/val/work);
# pass the wheel roots explicitly for reproducibility, and choose an output dir:
python docs/development/realworld-2026-06/harness.py wheels --out reports   # cross-version oneDAL
python docs/development/realworld-2026-06/selfsweep.py                      # 100-lib self-compare
```

Artifacts in [`realworld-2026-06/`](realworld-2026-06/README.md): `harness.py`,
`selfsweep.py`, `onedal_results.jsonl`, `selfsweep_results.jsonl`,
`selfsweep_summary.json`, and slimmed headline reports (`*.slim.json`).
