# abicheck × oneDAL — experiment analysis & UX findings

What broke, what confused, and what to add/fix in **abicheck** so the oneDAL
integration (and projects like it) has a good out-of-the-box experience. Every
item below was reproduced end-to-end against abicheck 0.4.0 with the real
toolchain now installed in the validation env (castxml 0.6.3, bear 3.1.3,
clang 18, Intel icx/icpx). Items marked **[fixed]** are patched in this PR.

---

## TL;DR — the one thing that hurts UX most

**Silent, misdiagnosed source-evidence (L3/L4/L5) collection.** The single
biggest friction is that when source-ABI (L4) evidence doesn't materialize, the
tool says *"not collected — supply --build-info/--compile-db or install
clang/castxml"* even when a valid `compile_commands.json`, clang, **and** castxml
are all present. The real causes are elsewhere (empty TU scope, a
public-header-roots mismatch, or a source↔binary symbol-mapping miss), and the
message points at the wrong fix. This turns a 2-minute config error into a
30-minute mystery. Partly **[fixed]** here (accurate message); the deeper
scope/matching defaults are a recommendation below.

---

## A. Bugs found & fixed in this PR

| # | Severity | Bug | Fix |
|---|----------|-----|-----|
| A1 | 🔴 High | **Action `scan` used a nonexistent flag.** `action/run.sh` forwarded the `build-config` input as `--build-config` to `abicheck scan`, which only accepts `--config` → **exit 64 hard-fail** whenever a config was passed in scan mode. | `run.sh` now passes `--config` (matches `dump`). **[fixed]** |
| A2 | 🟠 Med | **Misleading "layer not collected" warning.** `dump --max`/`--depth source` printed *"install clang/castxml"* even when L4 facts **were** embedded (or the extractor ran but linked 0 symbols). | `cli.py` now distinguishes *absent* (→ supply compile DB / install frontend) from *ran-but-linked-nothing* (→ points at coverage rows: public-roots mismatch, unseeded `--depth source`, or snapshot/source mismatch). **[fixed]** |
| A3 | 🟡 Low | **`bear` not installed by the action**, so Make projects (oneDAL, EPICS, Autotools) silently fell back to reduced-confidence `make -n` scraping for L3. | `action/install-deps.sh` now installs `bear` and prints a notice about `bear -- make`. **[fixed]** |

## B. UX gaps confirmed (recommendations for abicheck)

These are real friction points I hit; each is a suggested change to abicheck
itself (not worked around in the oneDAL package, or only partially).

### B1 — 🔴 L4 replay silently selects 0 TUs on an unseeded `--depth source`

`abicheck dump --sources . --build-info compile_commands.json --depth source`
(no `--since`/`--changed-path`) falls back to a **"headers-only"** replay scope
that, in my fixture, selected **0 translation units** → empty L4 → the (old)
misleading warning. You must pass `--max` (full scope) or a `--changed-path`/
`--since` seed to get L4. Reproduced:

```
--depth source, no seed  → L4 partial, 0 TUs selected, "not collected"
--max                    → L4 partial, 1/1 TU parsed, 0/4 symbols matched
--changed-path src/x.cpp → L4 present,  1/1 TU parsed, 4/4 symbols matched
```

**Recommendation:** when an unseeded `--depth source` selects 0 TUs, say so and
name the fix (`--max` or a seed) instead of the generic message. Better: make
`--depth source` without a seed default to the headers-covering TU set that
actually includes the public headers, or emit a one-line "selected 0 TUs; use
--max" note. (A2's fix now surfaces the right hint, but the *default* still
under-collects.)

### B2 — 🟠 Source→binary symbol matching is fragile (`0/N symbols matched`)

Even with L4 parsed, `--max` reported **0/4 symbols matched** while a seeded
`--changed-path` run on the same TU matched **4/4**. The decl→exported-symbol
linking is sensitive to scope and public-roots. For a big C++ project this is the
difference between "L4 is useful" and "L4 is noise."

**Recommendation:** treat `matched 0/N` as a first-class diagnostic (it already
prints in the coverage row) and surface it at WARNING level with the likely
cause; document the `public-header-roots` = *resolved include path* rule (the
Clang plugin already has an excellent version of this — port that diagnostic to
the `dump`/`scan` L4 path).

### B3 — 🟠 The SYCL/oneAPI surface needs an explicit SYCL frontend

`cpp/oneapi/dal` includes `sycl/sycl.hpp`; stock clang/castxml can't parse it, so
L2 for that surface silently degrades. It works once you point the frontend at
`icpx` (now installed) via `--gcc-path`, but nothing tells the user that a parse
failure is *SYCL-not-found* vs a real error.

**Recommendation:** detect `#include <sycl/sycl.hpp>` (or a `-fsycl` in the
compile DB) and, on parse failure, emit "this looks like a SYCL TU; pass
`--gcc-path icpx` / a SYCL-capable clang" instead of a raw parse error.

### B4 — 🟠 `scan` ignores `--suppress`; PR comments are compare-only

Two mode-asymmetries that surprised me: (1) the action forwards `--suppress` only
for `compare`/`compare-release`/`appcompat`, so a scan silently ignores a
suppress file; (2) the sticky PR comment is also compare/appcompat-only, so
`scan` reports only via the job summary. Both are defensible but undocumented.

**Recommendation:** either honour `--suppress` in `scan` (even if only to filter
the report) or make the action **warn** that the input is a no-op in scan mode;
document the PR-comment/mode matrix in the Action reference.

### B5 — 🟡 `compile.include_dirs` hard-errors (exit 64) on a missing dir

A `.abicheck.yml` whose `compile.include_dirs` names a dir that doesn't exist
*relative to the config file* aborts `compare` with exit 64. That's brittle for a
config meant to be committed once and run from varying CWDs.

**Recommendation:** downgrade a missing `include_dirs` entry to a warning (skip
it) rather than a hard usage error; it's config, not a per-run flag.

### B6 — 🟡 Unknown `change_kind` in a suppress file is a hard error

A typo'd/legacy `change_kind` (e.g. `symbol_removed`, which isn't a kind — it's
`func_removed`/`func_deleted`) fails the whole run with exit 64. Correct to be
strict, but the 300+-value valid list dumped to stderr is a lot to scan.

**Recommendation:** on an unknown `change_kind`, suggest the closest valid
kind(s) (edit-distance) instead of printing the entire enum.

## C. "There's no make / it doesn't emit a compile DB" — solved with `bear`

The friction: oneDAL's release libraries are built by the top-level `makefile`
(`make -f makefile daal …`), and **make does not emit `compile_commands.json`**,
so abicheck's zero-config path falls back to scraping `make -B -n -k -w` at
*reduced confidence* — which on a makefile as complex as oneDAL's is unreliable.

**The fix is `bear`** (now installed by the action, C/A3). Wrapping the existing
build produces an authoritative compile DB with no change to oneDAL's build:

```bash
bear --output compile_commands.json -- \
  .ci/scripts/build.sh --compiler icx --optimizations avx2 --target daal --debug symbols
# then abicheck sees L3 (and can attempt L4/L5):
abicheck dump <lib>.so --sources . --build-info compile_commands.json --depth build -o base.json
```

Validated on the fixture: `bear -- make` → `compile_commands.json` (1 entry) →
`abicheck dump` reports **L3 present** ("1 compile units"). This is wired into
`onedal-abicheck-libabigail.yml` (bear wraps the real build) and documented in
the config's `build:` block.

**abicheck recommendation:** when the detected build system is `make` **and**
`bear` is on PATH, offer to run `bear -- <make target>` (an explicit,
operator-trusted opt-in) instead of the low-confidence `make -n` scrape — or at
least name `bear` in the "no compile DB found" message (the message already
mentions it generically; make it the headline for make projects).

## D. Plugin data collection — where to inject it in oneDAL CI

Goal: persist abicheck's source-analysis data (`abicheck_inputs/` packs, or a
compile DB) as a **CI artifact** the GitHub Action reuses, so the expensive
source-fact extraction happens once, inside the build that already runs.

oneDAL's real build happens in:
- `.github/workflows/nightly-build.yml` and the Azure pipeline (`.ci/pipeline/`),
  which build via `.ci/scripts/build.sh` and **already upload the `__release_*`
  tree as an artifact** for downstream jobs.

Two injection points, cheapest first:

1. **Portable (Flow B / bear) — recommended default.** In the existing build job,
   wrap the build with `bear` (or the `abicheck-cc` wrapper) and, after it,
   `abicheck collect --compile-db compile_commands.json --headers cpp/daal/include
   -o abicheck_inputs`. Upload `abicheck_inputs/` next to the release tree. The
   Action's `dump --inputs abicheck_inputs/` (or `merge`) then folds the source
   facts in with **no compiler re-run**. This is what
   `onedal-abicheck-libabigail.yml` demonstrates.
2. **Fast (Flow C / Clang plugin).** For the icx/clang build, load the abicheck
   facts plugin during compilation (`-fplugin=…facts.so … out=abicheck_inputs
   public-roots=cpp/daal/include`) so facts are emitted from the AST the compiler
   already built — zero extra parse. Caveat (ADR-038 C.5): the plugin is
   ABI-locked to its clang major and **must be built against icx's LLVM**; verify
   load compatibility before relying on it. `onedal-abicheck-plugin-collect.yml`
   builds/loads it against stock clang as a template.

Either way the artifact is the same `abicheck_inputs/` protocol, so the
downstream Action step is identical.

## E. Integrating with the existing libabigail (abidiff) job

oneDAL already gates ABI with `.ci/scripts/abi_check.sh <baseline_dir>
<new_dir>` → `abidiff --suppr .github/.abignore` per library. **Don't replace it —
overlay abicheck in the same job** (no second build):

- The job already has the two release dirs (baseline + freshly built) and the
  `.github/.abignore` suppressions. Add an `abicheck compare "$bso" "$nso"` step
  over the same pairs, emitting **SARIF** (code scanning) + a **Markdown summary**
  + source-level (L2/L3/L4) findings abidiff doesn't model.
- Keep abidiff as the hard gate initially; run abicheck **advisory**
  (`continue-on-error`) until its signal is triaged, then promote.
- Reuse suppressions: abidiff keeps `.github/.abignore`; abicheck uses
  `onedal.suppress.yml`. (A nice future abicheck feature: **read `.abignore`
  directly**, since abicheck already targets ABICC-format suppressions — that
  would let both tools share one file.)

This is exactly what `onedal-abicheck-libabigail.yml` implements: abidiff and
abicheck run back-to-back over the identical inputs, and a bear-produced
`abicheck_inputs/` pack is uploaded for the Action to reuse (§D).

**abicheck recommendation:** add native `.abignore` (libabigail suppression)
ingestion so a project that already maintains one for abidiff gets abicheck
suppressions for free.

---

## Priority for the abicheck backlog

1. **B1/B2 + A2** — make source-evidence collection *legible*: never say "install
   tools you have"; when L4 links 0 symbols or selects 0 TUs, say why and how to
   fix. This is the difference between L3/L4/L5 being adopted or abandoned.
2. **C** — first-class `bear` support for make projects (offer to run it; headline
   it in diagnostics).
3. **B3** — SYCL-aware parse diagnostics (`--gcc-path icpx` hint).
4. **E** — ingest libabigail `.abignore` so abicheck drops into existing abidiff
   pipelines with zero new suppression files.
5. **B4/B5/B6** — smooth the sharp edges: scan/suppress + PR-comment mode
   matrix documented; `include_dirs` and `change_kind` errors become friendly.
