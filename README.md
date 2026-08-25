# abicheck

[![CI](https://github.com/abicheck/abicheck/actions/workflows/ci.yml/badge.svg)](https://github.com/abicheck/abicheck/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/abicheck/abicheck/branch/main/graph/badge.svg)](https://codecov.io/gh/abicheck/abicheck)
[![PyPI version](https://img.shields.io/pypi/v/abicheck.svg)](https://pypi.org/project/abicheck/)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/abicheck.svg)](https://anaconda.org/conda-forge/abicheck)
[![Python versions](https://img.shields.io/pypi/pyversions/abicheck.svg)](https://pypi.org/project/abicheck/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

**abicheck** combines binary, debug, header, build, and (optionally) source evidence to detect the widest practical set of mechanical C/C++ ABI/API compatibility breaks — while reporting exactly which finding classes weren't checkable with the evidence you gave it, rather than silently passing. It compares two versions of a shared library — along with their public headers — and reports whether existing binaries will continue to work or break at runtime.

It catches removed or renamed symbols, changed function signatures, struct layout drift, vtable reordering, enum value reassignment, and many more — **397 ABI/API change types** in total — that cause crashes, silent data corruption, or linker failures after a library upgrade.

> **Platforms:** Linux (ELF), Windows (PE/COFF), macOS (Mach-O). Binary and header AST analysis on all platforms; debug-info cross-check uses DWARF/BTF/CTF on Linux and PDB on Windows — Mach-O has no debug-info cross-check today, only header AST when you supply `-H`. MinGW-built DLLs are validated end-to-end in CI; native MSVC+PDB verdicts are experimental (the CI lane is non-blocking until proven stable) — see [Platform Support](https://abicheck.github.io/abicheck/reference/platforms/).

**Full documentation:** **[abicheck.github.io/abicheck](https://abicheck.github.io/abicheck/)**

---

## Key features

- **Reads multiple sources of information.** abicheck doesn't rely on a single view of a library. It overlays up to **five independent, additive sources** — the compiled binary, its debug symbols, its public headers, its build-system data, and (optionally) its sources — and lets the strongest evidence win. Each source finds breaks the weaker ones are blind to, and *removes* false positives the weaker ones would raise. See [How it works](#how-it-works--multiple-sources-of-information) below.
- **Detects most of what causes ABI/API breaks.** **397 change types** across functions, variables, structs/classes, enums, unions, typedefs, templates, and platform/linker metadata — removed or renamed symbols, changed signatures and parameter lists, struct/class layout drift, field-offset shifts, vtable reordering, enum value reassignment, qualifier/`noexcept`/access changes, calling-convention and packing changes, symbol-version and SONAME drift, dependency leaks, and more. Each is classified as `BREAKING`, `API_BREAK`, `COMPATIBLE_WITH_RISK`, or `COMPATIBLE`. See the [Change Kind Reference](https://abicheck.github.io/abicheck/reference/change-kinds/).
- **Cross-platform.** Linux (ELF), Windows (PE/COFF), and macOS (Mach-O) binaries, with debug-info cross-checks from DWARF, BTF, and CTF (all Linux/ELF) and PDB (Windows) — Mach-O has no debug-info cross-check today. See [Platform Support](https://abicheck.github.io/abicheck/reference/platforms/) for what's validated in CI per platform (native MSVC+PDB verdicts are experimental).
- **Built for CI.** Deterministic [exit codes](https://abicheck.github.io/abicheck/reference/exit-codes/), SARIF/JSON/Markdown/HTML/JUnit output, snapshot-based [baselines](https://abicheck.github.io/abicheck/user-guide/baseline-management/), [policy profiles](https://abicheck.github.io/abicheck/user-guide/policies/) and [suppressions](https://abicheck.github.io/abicheck/user-guide/suppressions/), and a first-class [GitHub Action](https://abicheck.github.io/abicheck/user-guide/github-action/).
- **Public-surface scoping.** Filters findings to the library's *public* ABI surface so internal-only changes don't fail your build — fewer false positives than symbol-only tools.
- **More than one library at a time.** Compare co-versioned multi-library releases as a single bundle ([`compare` on directory/package inputs](https://abicheck.github.io/abicheck/user-guide/multi-binary/)), check whether a specific application still works ([`compare --used-by`](https://abicheck.github.io/abicheck/user-guide/appcompat/)), or validate a binary's full dependency stack across sysroots ([`deps compare`](https://abicheck.github.io/abicheck/user-guide/cli-usage/)).
- **Drop-in for existing tools.** A [`compat`](https://abicheck.github.io/abicheck/user-guide/from-abicc/) mode mirrors `abi-compliance-checker` flags, and migration guides cover [ABICC](https://abicheck.github.io/abicheck/user-guide/from-abicc/) and [libabigail](https://abicheck.github.io/abicheck/user-guide/from-libabigail/).
- **Agent- and script-friendly.** Structured JSON/SARIF output and a [Python API](#python-api) for AI-driven workflows — no separate protocol server, agents use the CLI or the typed API directly. Pure Python (3.10+), no heavyweight native toolchain required for binary-only mode. A portable [Agent Skill](https://abicheck.github.io/abicheck/use/agent-skills/) (internal candidate, not yet externally published) is generated from [`skills-src/check-abi-compatibility/`](skills-src/check-abi-compatibility/) (`python scripts/install_dev_skill.py` to materialize it locally, e.g. into `.agents/skills/`) so a coding agent can answer "will this break existing consumers?" without the user knowing abicheck exists.
- **Contract-aware decisions** (opt-in). Gate only on changes that belong to your *declared* compatibility contract — public headers, the binary's actual export table, or everything — while excluded and unresolved findings stay in an auditable report instead of silently vanishing. See [Contract-Aware Compatibility](https://abicheck.github.io/abicheck/learn/contract-aware-compatibility/).
- **Cross-compiler reconciliation.** When one target is checked under several compiler/build profiles (GCC, Clang, MSVC), `aggregate` folds the reports back together and tells you whether a break is universal or profile-specific. See [Aggregate Reports](https://abicheck.github.io/abicheck/use/aggregate-reports/).
- **Consumer impact explanations.** `compare --used-by` doesn't just say an application is affected — with source evidence on the library side, it can show the public-to-internal call chain that makes the app depend on the changed declaration. See [Application Compatibility](https://abicheck.github.io/abicheck/user-guide/appcompat/#why-does-this-consumer-depend-on-the-changed-declaration).
- **One automation model.** The CLI and the Python API resolve through the same typed request objects and compatibility semantics (native `dump` is migrating — see the [CLI/Python parity table](https://abicheck.github.io/abicheck/user-guide/python-api/#cli-python-parity)).

---

## How it works — multiple sources of information

abicheck treats compatibility analysis as a question of **evidence**: the more independent sources you give it about a library, the more it can prove — and the fewer false positives it raises. There are **five layers**, ordered from the least input to the most. Each one *adds* facts the previous cannot see; none is complete on its own.

| Layer | Source you provide | Read by | What it newly reveals |
|:-----:|--------------------|---------|------------------------|
| **L0** | **Just the binary** — a stripped `.so` / `.dll` / `.dylib` | ELF/PE/COFF/Mach-O parsers (`pyelftools`, `pefile`, `macholib`) | Exported symbols, SONAME/install-name, symbol versions, visibility, binding, `DT_NEEDED`/`LC_LOAD_DYLIB` dependencies |
| **L1** | **+ Debug symbols** — a `-g` build or sidecar debug file | DWARF, PDB, BTF, CTF | Type **layout**: struct/class sizes, field offsets, enum *values*, vtable slots, calling convention, packing/alignment |
| **L2** | **+ Public headers** — `-H include/` | castxml AST | Source-level **API**: signatures, overloads, access (`public`/`private`), `final`/`explicit`/`noexcept`, templates, default args, public/internal scoping |
| **L3** | **+ Build system data & options** — `-p build/` | compile DB / CMake / Ninja / Bazel / Make | The flags the library was *actually* built with: `-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, `-fabi-version`, toolchain/sysroot, export maps |
| **L4** | **+ Sources** — a build/source pack | per-TU source ABI replay | Facts that never reach the binary: macro/`constexpr` values, default-argument *values*, inline/template bodies, uninstantiated templates |

The layers are **independent and additive, not a fallback chain** — abicheck overlays every source you give it and computes one worst-wins verdict, under the *authority rule*: artifact-backed evidence (L0/L1/L2) is authoritative for the shipped-ABI verdict, while build/source evidence (L3/L4) *explains, localizes, scopes, or adds confidence* to a finding (and can raise its own source-/API-level findings) but never silently deletes an artifact-proven break.

> **A sixth code you may see in the docs:** `L5` is the source *reachability graph* abicheck **derives** from L3/L4 evidence — you provide five sources (L0–L4); L5 is computed, never an input. It appears in the [`scan` documentation](https://abicheck.github.io/abicheck/concepts/evidence-and-detectability/).

With less input, abicheck degrades gracefully *down the staircase* rather than failing — a stripped binary with no headers collapses toward symbol-only checking — and `abicheck dump --dry-run` reports exactly which layers it found. The best input you can give it is **old library + new library + matching public headers + debug info + build data**. See [Evidence & Detectability](https://abicheck.github.io/abicheck/concepts/evidence-and-detectability/) for what each source can and cannot see, and [Architecture](https://abicheck.github.io/abicheck/concepts/architecture/) for how the layers are reconciled.

---

## Installation

**Full installation (recommended)** — conda-forge bundles `abicheck` with `castxml` as a run dependency, so header AST analysis (L2) is available without a separate `castxml` install. The feedstock does **not** pull in a C/C++ compiler as a run dependency, and its `castxml >=0.6.3` floor is looser than abicheck's own `>=0.6.11` version gate — pin `castxml>=0.6.11` explicitly, as shown below, so a fresh environment doesn't land a `castxml` build old enough for abicheck's own gate to then reject it:

```bash
conda create -n abicheck -c conda-forge python=3.12 abicheck "castxml>=0.6.11"
conda activate abicheck
```

**Lightweight/core installation** — the PyPI package is pure Python with no native scanner dependency:

```bash
pip install abicheck
```

`pip install abicheck` does **not** install `castxml` or a compiler. Without them, abicheck still works in binary-only (L0) and, where the Python DWARF/PDB parsers apply, debug-info (L1) mode — it can also load and compare pre-built snapshots, and run every report format. For header AST analysis (L2) on a pip install, point `abicheck` at a separately managed, modern `castxml`/direct-Clang toolchain — **don't** `pip install castxml`: that installs the unmaintained legacy PyPI distribution (last released 0.4.5 in September 2022, with no bundled-Clang metadata at all), which abicheck's version gate rejects by default for an authoritative L2 scan.

See [Getting Started](https://abicheck.github.io/abicheck/getting-started/) for per-platform setup and cross-compilation.

> **Naming note:** the PyPI/conda-forge package (`abicheck`) is distinct from the older SourceForge `abicheck` that is still packaged by some Linux distributions, and from similarly named ABI tools such as `abi-compliance-checker` wrappers or Fedora's `libabigail-tools`. Run `abicheck --version` to confirm — it should print `abicheck X.Y.Z (abicheck/abicheck)`. If there is a conflict, invoke via `python -m abicheck`.

---

## Quick start

Compare two library versions:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=include/v1/foo.h --header new=include/v2/foo.h
```

Save a baseline snapshot at release time, then compare every new build against it:

```bash
abicheck dump libfoo.so -H include/foo.h --version 1.0 -o baseline.json
abicheck compare baseline.json ./build/libfoo.so --header new=include/foo.h
```

Supported output formats: `markdown` (default), `json`, `sarif`, `html`, and `junit`.

```bash
abicheck compare old.so new.so -H foo.h --format sarif -o report.sarif
```

See [Getting Started](https://abicheck.github.io/abicheck/getting-started/) for the full tutorial and [CLI Usage](https://abicheck.github.io/abicheck/user-guide/cli-usage/) for the complete command reference.

---

## Which command do I need?

abicheck's whole CLI is 7 root commands: `dump`, `compare`, `scan`, `deps`, `compat`, `aggregate`, `project`. The last two are workflow-composition/advanced-integration commands, not binary analysis — most single-library projects only ever need the first five.

| I want to… | Use |
|------------|-----|
| Check whether a library upgrade breaks existing consumers | [`abicheck compare`](https://abicheck.github.io/abicheck/user-guide/cli-usage/) |
| Compare **a multi-library release** (a co-versioned bundle, e.g. oneDAL) as a single bundle | [`abicheck compare`](https://abicheck.github.io/abicheck/user-guide/multi-binary/) |
| Check whether **my application** breaks with a new library version | [`abicheck compare --used-by APP`](https://abicheck.github.io/abicheck/user-guide/appcompat/) |
| Check whether a **plugin** still satisfies its host's required entrypoints | [`abicheck compare --required-symbol SYM`](https://abicheck.github.io/abicheck/user-guide/plugin-systems/) |
| Run a deterministic source-intelligence scan (classify → audit → optional compare) | [`abicheck scan ARTIFACT`](https://abicheck.github.io/abicheck/user-guide/scan-levels/) |
| Validate a binary's full dependency stack across two sysroots | [`abicheck deps compare`](https://abicheck.github.io/abicheck/user-guide/cli-usage/) |
| Drop-in replacement for `abi-compliance-checker` | [`abicheck compat`](https://abicheck.github.io/abicheck/user-guide/from-abicc/) |
| Save a reusable ABI snapshot | [`abicheck dump`](https://abicheck.github.io/abicheck/getting-started/) |
| Fold per-target ABI reports from a CI build matrix into one gate verdict | [`abicheck aggregate`](https://abicheck.github.io/abicheck/use/aggregate-reports/) |
| Check a multi-target/multi-build-profile **project** together (advanced, `check-project.yml`) | [`abicheck project`](https://abicheck.github.io/abicheck/reference/cli-reference/) |

`compare` is strictly binary/API comparison. Planning and validating a
declared multi-target/multi-profile topology is `project`'s job; folding
already-produced per-check reports back into one gate is `aggregate`'s —
neither of those two analyzes a binary directly.

### Migrating a multi-library project onto the declarative topology

For a co-versioned release like oneDAL — several libraries, some built under
divergent compiler flags (e.g. `-fsycl` for a subset) — there are two ways to
wire `abicheck` into CI. The one every project can use today is
`abicheck compare` on directory/package inputs (see the table above) driven
directly from your own workflow, with a release-asset baseline and a
committed digest anchor. The other is G30/ADR-047's declarative topology: a
`.abicheck.yml` `targets:`/`bundles:`/`profiles:`/`baseline:` block, validated
with `abicheck project validate`, fanned out with `abicheck project plan`,
and run by the reusable `check-project.yml`/`publish-baseline.yml`
workflows — zero project-owned Python. See the
[Project Targets Reference](https://abicheck.github.io/abicheck/reference/project-targets-schema/).

The declarative path isn't a drop-in replacement for every project yet.
Before adopting it, confirm none of these apply to you:

- **`bundles:` checks only run at `depth: binary`** — `headers`/`build`/
  `source` are rejected at `project validate`. If your bundle-level check
  needs header-scope evidence, it can't run through a `bundles:` entry today.
- **Per-target `public_headers:` is validated but not yet projected into a
  run-plan cell** — it's schema-checked, but nothing downstream reads it to
  build a `-H` argument for you.
- **Stored-facts bundle comparison (`BundleFacts`) has no run-plan/composite
  Action/`check-project.yml` wiring** — it's reachable from the Python API
  only, not from the declarative CI surface.
- **`publish-baseline.yml` expects one `build-output.json` per contract
  profile** (G30 P1.1). A build system that doesn't emit a per-profile
  manifest in that shape needs to add one first.
- **`profiles:` describes a build *lane* (compiler/flags), not a library** —
  one profile's `targets[]` can list several libraries built under it (see
  the [`build-output.json` reference](https://abicheck.github.io/abicheck/reference/build-output-schema/)),
  but a project needs one profile *per distinct build configuration*: e.g.
  oneDAL needs one profile for its SYCL-built subset and another for the
  rest, not one profile per library.

None of these block `compare`-based CI today — they're gaps in the
*declarative* topology specifically, verified directly against
`abicheck/buildsource/project_targets.py`/`run_plan.py` rather than tracked
as open punch-list items anywhere: the design history for the bundle-depth
restriction and the `build-output.json` contract lives in the
[G30 GitHub Actions integration plan](docs/contribute/plans/g30-github-actions-integration-model.md),
and for stored-facts bundle comparison in the
[G38 bundle-facts plan](docs/contribute/plans/g38-bundle-facts-model-and-multibuild-comparability.md)
— neither doc currently lists these as scheduled work, so check the code
itself, not just the docs, before relying on a gap having closed.

---

## Exit codes

Use these to gate CI pipelines.

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` | Safe — no binary ABI break |
| `2` | `API_BREAK` | Source-level break (recompile needed, binary may still work) |
| `4` | `BREAKING` | Binary ABI break (old binaries will crash or misbehave) |
| `8` | `REMOVED_LIBRARY` | Library removed in new version (multi-library compare with `--fail-on-removed-library`) |

Any active severity setting (a `--severity-*` flag or a severity value in `.abicheck.yml`) switches `compare` to a severity-based scheme where `1` means an error-level *finding* in the addition/quality categories (`0` still passes, `4` is still worst). Under `--contract` (opt-in), an orthogonal contract-coverage axis can also raise a clean `0` to `1` when the selected contract domain's evidence is incomplete — a *different* reason for exit `1` than a severity error, both foldable with `max`; see [Contract-Aware Compatibility](https://abicheck.github.io/abicheck/learn/contract-aware-compatibility/). `scan`, `deps compare`, and `compat` add per-command codes (e.g. `scan` also has a `5` for `--budget` overflow). The canonical matrix is the [exit code reference](https://abicheck.github.io/abicheck/reference/exit-codes/); how snapshots, policies, suppressions, and severity combine into the exit code is covered in [CI Gating](https://abicheck.github.io/abicheck/user-guide/ci-gating/).

---

## GitHub Action

```yaml
- uses: abicheck/abicheck@v0.5.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/foo.h
    format: sarif
    upload-sarif: true
```

The action installs Python, castxml, and abicheck automatically. Outputs: `verdict`, `exit-code`, `report-path`. See the [GitHub Action docs](https://abicheck.github.io/abicheck/user-guide/github-action/) for matrix builds, cross-compilation, and gating flags (`fail-on-breaking`, `fail-on-api-break`).

The default compare path only needs normal checkout access. Extra repository permissions are needed only for optional GitHub integrations: `pull-requests: write` for PR comments and `security-events: write` for SARIF upload.

---

## Policies and suppressions

Policies classify detected changes (`BREAKING`, `COMPATIBLE`, …); suppressions silence known or intentional changes so they don't fail CI.

```bash
abicheck compare old.so new.so -H foo.h \
  --policy sdk_vendor \
  --suppress suppressions.yaml
```

Built-in profiles: `strict_abi` (default), `sdk_vendor`, `plugin_abi`. Custom YAML policies are supported, and the ABICC compat CLI accepts `-symbols-list`/`-types-list` whitelist flags.

Full references:
- [Policy Profiles](https://abicheck.github.io/abicheck/user-guide/policies/)
- [Suppressions](https://abicheck.github.io/abicheck/user-guide/suppressions/) (YAML schema, expiry, justification)
- [Migrating from ABICC](https://abicheck.github.io/abicheck/user-guide/from-abicc/)

---

## Python API

```python
from pathlib import Path
from abicheck.service import run_compare

result = run_compare(
    old_input=Path("libfoo.so.1"),
    new_input=Path("libfoo.so.2"),
    old_headers=[Path("include/v1/foo.h")],
    new_headers=[Path("include/v2/foo.h")],
)

print(result.diff.verdict)       # e.g. Verdict.BREAKING
print(len(result.diff.changes))  # number of detected changes
print(result.old_snapshot.library, result.new_snapshot.library)
```

`run_compare` returns a `CompareResult` — `diff`, `old_snapshot`,
`new_snapshot`, and the resolved `suppression` list. It returned a bare
3-tuple before 0.6; a positional caller migrates in one line with
`result, old, new = run_compare(...).as_tuple()`.

See the [Python API guide](https://abicheck.github.io/abicheck/user-guide/python-api/)
for snapshots, custom policies, and rendering. AI-agent workflows use this
same API or the CLI's structured JSON/SARIF output — there is no separate
protocol server.

---

## Examples

The [`examples/`](examples/README.md) directory contains **197 real-world ABI/API scenarios** (192 single-library cases plus 5 multi-library bundle cases) with ground-truth verdicts:

- Most are single-library `v1`/`v2` examples with a consumer app, including cases 187–189 and 191 (a public struct/class/function gaining a dependency on an internal type, proven both as a real artifact-level break and via the L2 header-only semantic graph, built automatically at `--depth headers` and above).
- The G20 audit/cross-source cases (143–151) are single-build snapshots demonstrating intra-version cross-checks.
- A handful of L3/L4/L5 build/source-only cases (152–158, 160–162, 190, 192–193) ship hand-built evidence-model fixture pairs demonstrating failures no artifact layer can see.
- Case 164 ships a guard-annotated fixture pair demonstrating a build-context-cleared header false positive (ADR-039).
- Bundle/release-level cases use release-style layouts.

The full catalog is the development regression corpus; a smaller historical cross-tool subset is kept in the reference docs for release-to-release comparison with libabigail and ABICC.

The authoritative completeness gate is the full example matrix: compiler lanes, runtime smoke, bundle validation, and dedicated proof owners are aggregated into exactly one row per ground-truth case. A green single-library lane or a `libv1.so`/`libv2.so` pair scan is not full-catalog proof. See the [full example validation runbook](docs/contribute/examples-validation-runbook.md) for runner selection, the reproducible workflow, artifact semantics, and agent rules.

---

## Validation snapshot

The main validation target is the full **197-case catalog**. To scan it for the current checkout:

```bash
python scripts/benchmark_comparison.py --suite all
```

The command writes `benchmark_reports/benchmark_report.json` with the selected suite, abicheck version, git commit, tool versions, the `ground_truth.json` SHA-256, and per-tool accuracy. Cases that require bundle/release harnesses or unavailable compiler features are marked as unscored instead of being folded into single-library verdict accuracy.

For apples-to-apples comparison with libabigail and ABICC, release workflows also run the historical pinned cross-tool subset (`case01`-`case73` + `case26b`) and attach that report to GitHub Releases:

```bash
python scripts/benchmark_comparison.py --suite pinned74
```

### Detection by evidence source

The [five input sources of information](#how-it-works--multiple-sources-of-information) reveal breaks that weaker sources cannot detect. L5 is a derived source graph, not a sixth input source. The table below is derived from the `examples/ground_truth.json` minimum-evidence labels of the 186 compare-style catalog cases (185 excluding the one documented detector gap, `case111`). The `--evidence-tiers` mode empirically scans the runnable catalog at L0-L3; L4 source-pack measurement is tracked as a separate extension:

```bash
python scripts/benchmark_comparison.py --evidence-tiers
```

| Source you provide | Cumulative cases reaching full expected-kind coverage |
|--------------------|:------------------------------------------------------:|
| Just the binary (`L0`) | 64 / 185 (35%) |
| + Debug symbols (`L1`) | 133 / 185 (72%) |
| + Public headers (`L2`) | 157 / 185 (85%) |
| + Build data (`L3`) | 167 / 185 (90%) |
| + Sources (`L4`) | 172 / 185 (93%) |
| + Source graph (`L5`) | 185 / 185 (100%) |

More evidence also *removes* false positives (e.g. header scoping correctly dismisses internal-struct changes). This staircase is a **discoverability floor** — the minimum source that reaches every cataloged expected kind for a case, not a blind accuracy score. That's usually also the minimum source for the correct *verdict*, but not always: 4 of the 13 `L5` cases are verdict-detectable much earlier (L1 or L0) and land at `L5` only because one correlated, non-verdict-driving kind in their catalog entry needs the source graph — see the `L5` caveat in [Tool Comparison & Benchmarks](https://abicheck.github.io/abicheck/reference/tool-comparison/#which-source-discovers-what) for the specific cases. For the stricter number that also penalizes false positives across the whole catalog, see the [full-catalog benchmark](https://abicheck.github.io/abicheck/reference/tool-comparison/#full-catalog-benchmark-2026-07-18-all-195-cases) (L3-L5 scores 99.5% there, with 0 false positives). See [Evidence & Detectability](https://abicheck.github.io/abicheck/concepts/evidence-and-detectability/) for what each source reveals and [Benchmarking by evidence tier](https://abicheck.github.io/abicheck/reference/tool-comparison/#benchmarking-by-evidence-tier) for the methodology.

Per-case matrix, methodology, full-catalog notes, and the pinned cross-tool comparison table: [Tool Comparison & Benchmarks](https://abicheck.github.io/abicheck/reference/tool-comparison/).

---

## Documentation

- **Start here:** [Getting Started](https://abicheck.github.io/abicheck/getting-started/)
- **User guide:** [CLI Usage](https://abicheck.github.io/abicheck/user-guide/cli-usage/) · [Application compatibility](https://abicheck.github.io/abicheck/user-guide/appcompat/) · [Output formats](https://abicheck.github.io/abicheck/user-guide/output-formats/) · [GitHub Action](https://abicheck.github.io/abicheck/user-guide/github-action/)
- **Concepts:** [Verdicts](https://abicheck.github.io/abicheck/concepts/verdicts/) · [Architecture](https://abicheck.github.io/abicheck/concepts/architecture/) · [ABI/API Compatibility](https://abicheck.github.io/abicheck/concepts/abi-api-handling/) · [Limitations](https://abicheck.github.io/abicheck/concepts/limitations/)
- **Reference:** [Change Kinds](https://abicheck.github.io/abicheck/reference/change-kinds/) · [Exit Codes](https://abicheck.github.io/abicheck/reference/exit-codes/) · [Platforms](https://abicheck.github.io/abicheck/reference/platforms/) · [Tool Comparison](https://abicheck.github.io/abicheck/reference/tool-comparison/)
- **Troubleshooting:** [Troubleshooting guide](https://abicheck.github.io/abicheck/troubleshooting/)

### Citation and machine-readable metadata

- GitHub renders [CITATION.cff](CITATION.cff) through **Cite this repository**.
- [CodeMeta](codemeta.json) and [Zenodo deposit metadata](.zenodo.json) expose
  software identity, licensing, authorship, and dependency metadata.
- The published [versioned JSON Schemas](https://abicheck.github.io/abicheck/reference/machine-readable-metadata/)
  describe the machine-readable output contracts. Each schema's canonical `$id`
  is a resolvable HTTPS URL.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, code style, and PR workflow. Project status and roadmap: [development/goals.md](docs/contribute/goals.md). Coding agents (Claude Code, Copilot, Cursor, or otherwise): the canonical repository contract is [AGENTS.md](AGENTS.md).

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Copyright 2026 Nikolay Petrov
