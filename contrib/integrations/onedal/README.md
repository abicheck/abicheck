# abicheck × oneDAL integration

A drop-in package for wiring [abicheck](https://github.com/abicheck/abicheck)
into [uxlfoundation/oneDAL](https://github.com/uxlfoundation/oneDAL) CI, baselined
against the **2026.0.0** release. It gives you three layers of ABI/API safety:

| Layer | Workflow | Trigger | Build cost | Gating |
|-------|----------|---------|-----------|--------|
| **Source scan** (primary) | `onedal-abicheck-pr-source-scan.yml` | every PR | **none** (buildless) | advisory (job summary) |
| **Build + collect facts** | `onedal-abicheck-build-with-plugin.yml` | PR + dispatch | oneDAL build (plugin loaded) | uploads release + facts |
| **Analysis (via Action)** | `onedal-abicheck-analysis.yml` | after the build | none (reuses artifacts) | advisory (SARIF + PR comment) |
| **Nightly binary compare** | `onedal-abicheck-nightly-compare.yml` | nightly + dispatch | full icx+MKL build | advisory (SARIF) |
| **Baseline builder** | `onedal-abicheck-baseline.yml` | dispatch (once) | full icx+MKL build | opens a PR with snapshots |

> **Build-integrated collection:** `build-with-plugin` mirrors oneDAL's
> `nightly-build.yml` and loads the **compiler plugin** during the normal compile
> so it emits source facts (`abicheck_inputs/`) with no extra parse, then uploads
> them next to `__release_lnx`. `analysis` runs the **GitHub Action** on those
> artifacts (`dump` → `merge` facts → `compare`) — the collection happens once,
> inside the build CI already runs, and the analysis reuses it. See ANALYSIS.md §D.

> **Posture:** everything ships **advisory** (report, don't block). This is a
> validation deployment — see the signal, tune the config/suppressions, then flip
> `fail-on-*` to `true` and the severities to `error` when you enforce for the
> next release. The "[Validation findings](#validation-findings--problems-to-check)"
> section is the point of this exercise.

---

## What's in here

```
contrib/integrations/onedal/
├── config/
│   ├── onedal.abicheck.yml     → drop at oneDAL repo root as .abicheck.yml
│   └── onedal.suppress.yml     → drop at oneDAL repo root as onedal.suppress.yml
├── workflows/                  → copy into oneDAL .github/workflows/
│   ├── onedal-abicheck-baseline.yml
│   ├── onedal-abicheck-pr-source-scan.yml
│   ├── onedal-abicheck-nightly-compare.yml
│   ├── onedal-abicheck-build-with-plugin.yml → build oneDAL + collect facts (plugin)
│   └── onedal-abicheck-analysis.yml       → analyze the build + facts via the Action
├── scripts/
│   └── onedal-make-baseline.sh → reproduce baselines locally
└── ANALYSIS.md                 → UX findings + abicheck backlog (read this)
```

> **`ANALYSIS.md`** is the experiment write-up: the bugs found & fixed, the
> confirmed UX gaps with recommendations for abicheck, the `bear` fix for
> make-based compile DBs, where to inject plugin data collection in oneDAL CI,
> and how to overlay abicheck on oneDAL's existing libabigail (`abidiff`) job.

## oneDAL facts this integration is built on (verified @ 2026.0.0)

- **Release tag:** `2026.0.0` (no `v` prefix); soname major `.so.4` (`MAJORBINARY=4`).
- **Public header roots:** `cpp/daal/include` (classic DAAL C++ API) and
  `cpp/oneapi/dal` (oneAPI DAL API). Internal: `detail/`, `backend/`, `test/`.
- **Shared libraries:** `libonedal_core`, `libonedal_thread`, `libonedal`,
  `libonedal_parameters` (+ `_dpc` SYCL variants). Output tree
  `__release_lnx/daal/latest/{lib/intel64,include}`.
- **Build:** `.ci/scripts/build.sh --compiler icx --optimizations avx2 --target
  {daal,oneapi_c,oneapi_dpc} --debug symbols` after `.ci/env/apt.sh {dpcpp,mkl,miniforge}`.
  `--debug symbols` means the built libraries carry DWARF, so snapshots get L1
  type detail for free.

---

## Install into your fork (5 steps)

1. **Copy the config** to the oneDAL repo root:
   ```bash
   cp contrib/integrations/onedal/config/onedal.abicheck.yml   /path/to/oneDAL/.abicheck.yml
   cp contrib/integrations/onedal/config/onedal.suppress.yml   /path/to/oneDAL/onedal.suppress.yml
   ```
2. **Copy the workflows** into `.github/workflows/`:
   ```bash
   cp contrib/integrations/onedal/workflows/*.yml /path/to/oneDAL/.github/workflows/
   ```
3. **Pin the action version.** The workflows use `abicheck/abicheck@v0.4.0` and
   `pip install abicheck==0.4.0`. Confirm that tag is published (or pin a commit
   SHA / the tag you use) — this is the one value to set before first run.
4. **Build the baseline once.** Run the *build 2026.0.0 baseline* workflow
   (Actions → Run workflow). It builds 2026.0.0, dumps per-library snapshots, and
   opens a PR adding `.abicheck/baseline-2026.0.0/*.abi.json`. Merge it.
5. **Open a test PR** touching `cpp/daal/include/**`. The *PR source scan* runs
   (buildless, ~1 min) and writes an ABI report to the job summary.

That's the loop. The build-with-plugin, analysis, and nightly-compare workflows
are opt-in from the Actions tab.

---

## How each layer works

### 1. PR source scan (buildless — the everyday gate)

`abicheck scan` needs a "binary" surface, but it accepts a **`.abi.json`
snapshot** in that slot. So the PR scan feeds it the committed 2026.0.0 snapshot
as the surface and reasons about the PR's *source* delta (`--sources .` +
`--since origin/<base>`, `--depth source`). **No oneDAL build, no MKL** — it runs
in about a minute and is safe to run on every PR. It classifies the changed
public headers, runs the compiler-free pattern checks and intra-version
cross-source checks, and writes the report to the **job summary** (+ the
uploaded JSON artifact) — see F12 for why scan doesn't post a PR comment.

### 2. Nightly binary compare (authoritative)

Builds the current tree with oneDAL's real toolchain (icx + MKL, `--debug
symbols`) and runs `abicheck compare <baseline.abi.json> <built.so>` per library
at the **binary + DWARF** level — the ground truth for removed/changed exported
symbols and layout breaks. Emits SARIF (code scanning) + a job summary. Kept off
the PR path because a full oneDAL build is far too slow for per-PR feedback.

### 3. Build + collect facts, then analyze via the Action (the plugin path)

`build-with-plugin` builds oneDAL with the **compiler plugin** loaded during the
normal compile (via a PATH-shim `icx`/`icpx`), so it emits `abicheck_inputs/`
source facts with no extra parse, and uploads them next to `__release_lnx`.
`analysis` then runs the **GitHub Action** on those artifacts: `dump` the built
library → `merge` in the facts → `compare` vs the baseline (SARIF + PR comment).
Collection rides the build CI already runs; the analysis reuses it — no second
build, no CLI in oneDAL's scripts. The three fact producers (compiler plugin /
compiler wrapper / compile-database replay) all emit the same `abicheck_inputs/`,
so the analysis is identical whichever you use. Details + the icx ABI-lock
caveat: ANALYSIS.md §D.

---

## Validation findings & problems to check

I validated the full command chain against abicheck 0.4.0 (clang front-end, no
castxml) with a synthetic C++ library reproducing oneDAL's shape (public header
tree + built `.so` + a removed method + a changed signature). Findings, most
important first:

### F1 — 🔴 Baseline **must** bake headers in (or use per-side headers), or removals hide

Comparing a built `.so` against a new build while passing the **current** PR's
headers to *both* sides reported `COMPATIBLE` even though a public method was
removed — the new header no longer declares the removed method, so header-scoped
diffing can't see it. The fix, which this integration uses by design: the
baseline is a **`.abi.json` snapshot** (headers/symbols baked in at 2026.0.0), and
`compare`/`scan` re-parse only the *new* side's headers. With the snapshot
baseline the same change is correctly `BREAKING` (exit 4, `breaking: 2`). **Never
point `-H`/`--header` at one header tree for both an old binary and a new binary.**

### F2 — 🟠 The oneAPI/DAL **SYCL headers won't parse without a SYCL front-end**

`cpp/oneapi/dal` pulls in `sycl/sycl.hpp` on the DPC++ path. Stock `clang++` (and
castxml) can't parse those, so **L2 header analysis of the oneAPI SYCL surface is
not reliable** without pointing the front-end at `icpx` (`--gcc-path icpx`, which
this repo does not assume). Consequences:
- The classic **DAAL** surface (`cpp/daal/include`, plain C++17) parses cleanly —
  full L2 there.
- For the **oneAPI** surface, rely on **symbol + DWARF (L0/L1)** from the built
  library (the nightly compare covers this well because of `--debug symbols`).
  The buildless PR scan of oneAPI headers is best-effort; treat its L2 findings as
  advisory until you wire an icx-based header parse.

### F3 — 🟠 castxml is absent by default; the clang front-end is the right call

The action installs castxml, but oneDAL's own toolchain is clang/icx. The config
pins `compile.frontend: clang` so header parsing matches how oneDAL is actually
compiled. If you leave it on `auto` and castxml is present, castxml's bundled
front-end can choke on oneDAL's AVX-512/SYCL intrinsics.

### F4 — 🟡 L4/L5 source evidence needs a compile database — the make build emits none

`abicheck dump --sources` collected L2 + **L3** cleanly in testing, but **L4
(source-ABI replay)** silently stayed empty without a well-formed
`compile_commands.json`. oneDAL's top-level `makefile` doesn't emit one. To get
L4/L5 you must supply a compile DB via **`bear -- make ...`**, **bazel aquery**,
or the **Clang plugin**. Until then, L3 (build-flag context) is what the source
layers buy you — still useful, but set expectations.

### F5 — 🟡 Internal symbols leak without a version script → suppression matters

oneDAL exports internal-namespace symbols (`daal::internal`,
`oneapi::dal::detail/backend`) unless a linker version script hides them.
Symbol-level (L0) diffing sees them and would generate churn findings.
`onedal.suppress.yml` filters these, and `scope.public: true` +
`sources.exclude` narrow the surface — but **review the first real baseline** to
confirm the patterns match your actual exported set, and tighten/loosen them.

### F6 — 🟡 DAAL `interfaceN` generation churn is expected

DAAL versions each ABI generation as `interface1`, `interface2`, … Symbol churn
*between internal generations* is normal; the stable contract is the public
umbrella (`daal::algorithms::…`). The suppress file carries a (dated, expiring)
waiver for `interfaceN` `symbol_removed` — narrow it to the generations your
release actually ships once you see a real diff.

### F7 — 🟢 `scan-mode`/`source-method` inputs are deprecated

Use the `depth` dial (`build`/`source`/`full`), not the old `scan-mode`
(`pr`/`pr-deep`/…) or `source-method` (`s0…s6`) inputs — those still work but emit
a deprecation warning. The workflows already use `depth: source`.

### F9 — 🔴 Action bug found **and fixed**: `scan` config flag was wrong

While wiring the PR scan I found the composite action passed `--build-config` to
`abicheck scan`, but scan's config flag is `--config` — so setting the action's
`build-config` input in `scan` mode **hard-failed with exit 64** (`No such option
'--build-config'`). Fixed in this branch (`action/run.sh`: scan now passes
`--config`, matching `dump`). Two consequences for you:
- If you pin an **older** action release, don't set `build-config:` on the scan
  step. The PR-scan workflow instead relies on **`.abicheck.yml` auto-discovery**
  at the `--sources` root, which works on every version. (Validated: scan picks
  up the repo-root config with no input.)
- On the fixed version, `build-config: .abicheck.yml` also works.

### F10 — 🟠 `scan` ignores the suppress file

The action forwards `--suppress` only for `compare`/`compare-release`/`appcompat`
— **not `scan`** (the scan CLI has no `--suppress`). So `onedal.suppress.yml`
applies to the nightly **compare**, but PR **scan** noise must be controlled via
`scope.public` + `sources.exclude` in `.abicheck.yml` and per-check
`--crosscheck KEY=off`. The PR-scan workflow no longer passes a (no-op)
`suppress:` input.

### F11 — 🟡 `compile.include_dirs` is existence-checked (exit 64)

A `.abicheck.yml` with `compile.include_dirs` pointing at dirs that don't exist
*relative to the config file* makes `compare` hard-fail (exit 64). The shipped
config omits `include_dirs` for that reason (the workflows pass header roots
explicitly). If you add them back, keep `.abicheck.yml` at the repo root and
ensure every listed dir exists there.

### F12 — 🟠 `scan` mode posts **no PR comment** — the channel is the job summary

The action's sticky PR comment is gated to `compare`/`compare-release`/`appcompat`
(`_maybe_post_pr_comment`), so it's a no-op in `scan` mode. The buildless PR gate
therefore reports via the **job summary + uploaded artifact**, not a comment. A
PR comment would require a `compare` run, which needs a freshly built `.so` — i.e.
the (heavy) nightly job. If you want inline PR comments per-PR, either accept the
nightly compare's cadence or add a build to the PR job (expensive). The advisory
job-summary channel is the pragmatic per-PR choice.

### F8 — 🟢 Plugin is ABI-locked to its clang major (icx caveat)

The compiler plugin must be loaded by the *same* compiler major it was built
against (ADR-038 C.5). `build-with-plugin.yml` builds it against **icx's own
LLVM** (`icpx -print-resource-dir`) so it matches. If it still can't load into
your icx, swap the PATH-shim for the **compiler wrapper** (`abicheck-cc`) or
`bear` + **compile-database replay** — the uploaded `abicheck_inputs/` is
identical, so the analysis workflow is unchanged.

---

## Recommended path to enforcement (next release)

1. **Now:** merge the baseline PR; run all four workflows advisory for a release
   cycle. Watch the PR comments and nightly SARIF.
2. **Triage:** for each recurring finding, either fix it, or add a *justified,
   dated* rule to `onedal.suppress.yml`. Confirm F5/F6 patterns against reality.
3. **Wire L4/L5 (optional):** add `bear -- make` (or bazel aquery) to the compare
   job so `compile_commands.json` exists, unlocking source-ABI replay.
4. **Enforce:** set `abi_breaking: error` (already) and flip the PR scan's
   `fail-on-api-break: true`; set `severity.potential_breaking: error` when the
   noise floor is clean. The DAAL surface can enforce before the SYCL surface.

## Local reproduction

```bash
# After a local `.ci/scripts/build.sh ... --debug symbols` build:
pip install abicheck
contrib/integrations/onedal/scripts/onedal-make-baseline.sh \
  __release_lnx/daal/latest/lib/intel64 2026.0.0
# → .abicheck/baseline-2026.0.0/libonedal_*.abi.json
```

## References

- abicheck GitHub Action: `../../../docs/user-guide/github-action.md`
- Source scans & depth dial: `../../../docs/user-guide/github-action-source-scans.md`
- `.abicheck.yml` schema: `../../../docs/reference/config-file.md`
- Suppressions: `../../../docs/user-guide/suppressions.md`
- Clang facts plugin: `../../abicheck-clang-plugin/README.md`
