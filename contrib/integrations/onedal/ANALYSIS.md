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
`abicheck dump` reports **L3 present** ("1 compile units").

Two ways to wire it, both config-driven (your point — for Make you declare the
build command, abicheck doesn't guess):

- **Config `build.query`** — put the exact command in `.abicheck.yml` (see the
  `build:` block); abicheck runs it (from an explicit `--config`) to produce the
  DB. Example: `query: bear --output compile_commands.json -- .ci/scripts/build.sh …`.
- **Pre-generate + `build.compile_db`** — run `bear …` in the build job, commit or
  upload the resulting `compile_commands.json`, and point `compile_db` at it.

**abicheck recommendation:** when the detected build system is `make` **and**
`bear` is on PATH, offer to run `bear -- <make target>` (an explicit,
operator-trusted opt-in) instead of the low-confidence `make -n` scrape — or at
least name `bear` in the "no compile DB found" message.

## Naming — the three ways to produce source facts

We dropped the internal "Flow A/B/C" labels. The three producers, by what they
*are* (all emit the same `abicheck_inputs/` protocol, so the analysis step is
identical):

| Name | What it does | When |
|------|--------------|------|
| **compiler plugin** | emits facts from the AST **during the normal compile** — zero extra parse | you own the build image; fastest |
| **compiler wrapper** (`abicheck-cc`) | wraps any compiler, runs the extractor as a companion | portable; any toolchain |
| **compile-database replay** | `dump --sources` re-parses TUs from a `compile_commands.json` | you already have/produce a compile DB |

## D. Where to inject plugin data collection in oneDAL CI (the build-integrated design)

Goal, per your steer: **add the plugin to the build that CI already runs, save
its output as an artifact next to the build, and reuse it in analysis** — instead
of a throwaway build or calling the CLI inside oneDAL's scripts.

Which build? oneDAL's `.github/workflows/nightly-build.yml` builds daal +
oneapi_c (icx/avx2), uploads `__release_lnx` as an artifact, and triggers on
**both `schedule` and `pull_request`** (path-filtered). So there **is** a build
on PRs — but the ABI *check* (`abi_check.sh` → abidiff) lives in the nightly
**test** side, so today PRs are built but not ABI-scanned. The fix is to collect
facts in that build and analyze on PRs:

1. **Build + collect** (`onedal-abicheck-build-with-plugin.yml`): mirrors
   nightly-build, but loads the **compiler plugin** via a PATH-shim `icx`/`icpx`
   (forwards to the real compiler + appends the plugin's cc1 args), so facts are
   emitted during the normal compile with **no makefile change**. Uploads
   `__release_lnx` **and** `abicheck_inputs/` as two artifacts. In your fork you
   merge this into nightly-build (add the shim + the two uploads), so collection
   rides the build you already run.
2. **Analyze via the Action** (`onedal-abicheck-analysis.yml`): `workflow_run`
   after the build downloads both artifacts and runs the **GitHub Action** —
   `dump` the built lib → `merge` in `abicheck_inputs/` → `compare` vs the
   2026.0.0 baseline (SARIF + PR comment). No CLI in oneDAL scripts; the facts
   collected once in the build are reused here.

The plugin is ABI-locked to icx's LLVM major, so the build job builds it against
icx's own LLVM tree (`icpx -print-resource-dir`). If it can't load into your icx,
swap the shim for the **compiler wrapper** or `bear` + **compile-database
replay** — the uploaded `abicheck_inputs/` is identical, so the analysis job is
unchanged.

## E. Coexisting with the existing libabigail (abidiff) job

oneDAL already gates ABI with `.ci/scripts/abi_check.sh <baseline_dir>
<new_dir>` → `abidiff --suppr .github/.abignore` per library. **Keep it** — run
abicheck as an overlay, not a replacement:

- Both consume the same two release dirs. Keep abidiff as the hard gate; run the
  abicheck **analysis workflow** (above) **advisory** until its signal is triaged,
  then promote. abicheck adds SARIF (code scanning), a per-symbol PR comment,
  policy profiles, and source-level (L2/L3/L4) findings abidiff doesn't model.
- Suppressions stay separate for now: abidiff keeps `.github/.abignore`, abicheck
  uses `onedal.suppress.yml`.

**abicheck recommendation:** add native `.abignore` (libabigail suppression)
ingestion so a project already maintaining one for abidiff gets abicheck
suppressions for free — one file, both tools.

## F. Why weren't these bugs caught earlier?

Each fixed bug had a matching **test blind spot**; the fixes ship with
**generalized** guards (not one-off assertions), so the whole class is covered:

| Bug | Why it slipped | Generalized guard added |
|-----|----------------|--------------------------|
| Action `scan --build-config` (exit 64) | **No action test ran scan with a config input** — the config path was never exercised, in any mode. | `tests/test_action_run_contract.py` parses `run.sh` and asserts **every** flag it passes, **per subcommand**, is a real option of that subcommand's `--help`. Catches this and any future action↔CLI drift for *all* modes. Verified it fails when `--build-config` is reintroduced. |
| Misleading "not collected" warning | The existing test **mocked** `_missing_requested_evidence_layers` to return a list and only checked the string "not collected" — the message was never asserted against a real pack, so a present-but-empty layer's wording was untested. | `TestClassifyMissingLayers` runs the real split on a real `BuildSourcePack` (PARTIAL L4 row) and asserts the ran-but-empty branch says "collected but linked no facts", not "install clang/castxml". |

The deeper behavioural gaps (B1 unseeded `--depth source` selects 0 TUs; B2
`0/N symbols matched`) are **not** yet fixed — they're documented as
recommendations, so no test claims coverage of behaviour we haven't corrected.
They need an integration-marked fixture (a real compiled lib + compile DB) to pin
the seed requirement and the public-roots matching rule; that's the right next
step before changing the L4 scope defaults.

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
