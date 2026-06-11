# abicheck

[![CI](https://github.com/napetrov/abicheck/actions/workflows/ci.yml/badge.svg)](https://github.com/napetrov/abicheck/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/napetrov/abicheck/branch/main/graph/badge.svg)](https://codecov.io/gh/napetrov/abicheck)
[![PyPI version](https://img.shields.io/pypi/v/abicheck.svg)](https://pypi.org/project/abicheck/)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/abicheck.svg)](https://anaconda.org/conda-forge/abicheck)
[![Python versions](https://img.shields.io/pypi/pyversions/abicheck.svg)](https://pypi.org/project/abicheck/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

**abicheck** detects breaking changes in C/C++ shared libraries before they reach production. It compares two versions of a shared library — along with their public headers — and reports whether existing binaries will continue to work or break at runtime.

It catches removed or renamed symbols, changed function signatures, struct layout drift, vtable reordering, enum value reassignment, and many more — **230 ABI/API change types** in total — that cause crashes, silent data corruption, or linker failures after a library upgrade.

> **Platforms:** Linux (ELF), Windows (PE/COFF), macOS (Mach-O). Binary and header AST analysis on all platforms; debug-info cross-check uses DWARF (Linux, macOS) and PDB (Windows).

**Full documentation:** **[napetrov.github.io/abicheck](https://napetrov.github.io/abicheck/)**

---

## Key features

- **Reads multiple sources of information.** abicheck doesn't rely on a single view of a library. It overlays up to **five independent, additive sources** — the compiled binary, its debug symbols, its public headers, its build-system data, and (optionally) its sources — and lets the strongest evidence win. Each source finds breaks the weaker ones are blind to, and *removes* false positives the weaker ones would raise. See [How it works](#how-it-works--multiple-sources-of-information) below.
- **Detects most of what causes ABI/API breaks.** **230 change types** across functions, variables, structs/classes, enums, unions, typedefs, templates, and platform/linker metadata — removed or renamed symbols, changed signatures and parameter lists, struct/class layout drift, field-offset shifts, vtable reordering, enum value reassignment, qualifier/`noexcept`/access changes, calling-convention and packing changes, symbol-version and SONAME drift, dependency leaks, and more. Each is classified as `BREAKING`, `API_BREAK`, `COMPATIBLE_WITH_RISK`, or `COMPATIBLE`. See the [Change Kind Reference](https://napetrov.github.io/abicheck/reference/change-kinds/).
- **Cross-platform.** Linux (ELF), Windows (PE/COFF), and macOS (Mach-O) binaries, with debug-info cross-checks from DWARF, PDB, BTF, and CTF.
- **Built for CI.** Deterministic [exit codes](https://napetrov.github.io/abicheck/reference/exit-codes/), SARIF/JSON/Markdown/HTML/JUnit output, snapshot-based [baselines](https://napetrov.github.io/abicheck/user-guide/baseline-management/), [policy profiles](https://napetrov.github.io/abicheck/user-guide/policies/) and [suppressions](https://napetrov.github.io/abicheck/user-guide/suppressions/), and a first-class [GitHub Action](https://napetrov.github.io/abicheck/user-guide/github-action/).
- **Public-surface scoping.** Filters findings to the library's *public* ABI surface so internal-only changes don't fail your build — fewer false positives than symbol-only tools.
- **More than one library at a time.** Compare co-versioned multi-library releases as a single bundle ([`compare-release`](https://napetrov.github.io/abicheck/user-guide/multi-binary/)), check whether a specific application still works ([`appcompat`](https://napetrov.github.io/abicheck/user-guide/appcompat/)), or validate a binary's full dependency stack across sysroots ([`stack-check`](https://napetrov.github.io/abicheck/user-guide/cli-usage/)).
- **Drop-in for existing tools.** A [`compat`](https://napetrov.github.io/abicheck/user-guide/from-abicc/) mode mirrors `abi-compliance-checker` flags, and migration guides cover [ABICC](https://napetrov.github.io/abicheck/user-guide/from-abicc/) and [libabigail](https://napetrov.github.io/abicheck/user-guide/from-libabigail/).
- **Agent- and script-friendly.** Structured JSON, a [Python API](#python-api), and an [MCP server](https://napetrov.github.io/abicheck/user-guide/mcp-integration/) for AI-driven workflows. Pure Python (3.10+), no heavyweight native toolchain required for binary-only mode.

---

## How it works — multiple sources of information

abicheck treats compatibility analysis as a question of **evidence**: the more independent sources you give it about a library, the more it can prove — and the fewer false positives it raises. There are **five layers**, ordered from the least input to the most. Each one *adds* facts the previous cannot see; none is complete on its own.

| Layer | Source you provide | Read by | What it newly reveals |
|:-----:|--------------------|---------|------------------------|
| **L0** | **Just the binary** — a stripped `.so` / `.dll` / `.dylib` | ELF/PE/COFF/Mach-O parsers (`pyelftools`, `pefile`, `macholib`) | Exported symbols, SONAME/install-name, symbol versions, visibility, binding, `DT_NEEDED`/`LC_LOAD_DYLIB` dependencies |
| **L1** | **+ Debug symbols** — a `-g` build or sidecar debug file | DWARF, PDB, BTF, CTF | Type **layout**: struct/class sizes, field offsets, enum *values*, vtable slots, calling convention, packing/alignment |
| **L2** | **+ Public headers** — `-H include/` | castxml AST | Source-level **API**: signatures, overloads, access (`public`/`private`), `final`/`explicit`/`noexcept`, templates, default args, public/internal scoping |
| **L3** | **+ Build system data & options** — `-p build/` | compile DB / CMake / Ninja / Bazel / Make | The flags the library was *actually* built with: `-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, `-fabi-version`, toolchain/sysroot, export maps |
| **L4** | **+ Sources** — an evidence pack | per-TU source ABI replay | Facts that never reach the binary: macro/`constexpr` values, default-argument *values*, inline/template bodies, uninstantiated templates |

The layers are **independent and additive, not a fallback chain** — abicheck overlays every source you give it and computes one worst-wins verdict, under the *authority rule*: artifact-backed evidence (L0/L1/L2) is authoritative for the shipped-ABI verdict, while build/source evidence (L3/L4) *explains, localizes, scopes, or adds confidence* to a finding (and can raise its own source-/API-level findings) but never silently deletes an artifact-proven break.

With less input, abicheck degrades gracefully *down the staircase* rather than failing — a stripped binary with no headers collapses toward symbol-only checking — and `abicheck dump --show-data-sources` reports exactly which layers it found. The best input you can give it is **old library + new library + matching public headers + debug info + build data**. See [Evidence & Detectability](https://napetrov.github.io/abicheck/concepts/evidence-and-detectability/) for what each source can and cannot see, and [Architecture](https://napetrov.github.io/abicheck/concepts/architecture/) for how the layers are reconciled.

---

## Installation

```bash
pip install abicheck
# or
conda install -c conda-forge abicheck
```

`abicheck` also needs `castxml` and a C++ compiler for header AST analysis (the conda-forge package pulls these in automatically). Without them, abicheck still works in binary-only mode. See [Getting Started](https://napetrov.github.io/abicheck/getting-started/) for per-platform setup and cross-compilation.

> **Naming note:** this project (`napetrov/abicheck` on PyPI) is distinct from distro-packaged tools with similar names (`abi-compliance-checker` wrappers in Debian `devscripts`, or `abicheck` in Fedora's `libabigail-tools`). Run `abicheck --version` to confirm — it should print `abicheck X.Y.Z (napetrov/abicheck)`. If there is a conflict, invoke via `python -m abicheck`.

---

## Quick start

Compare two library versions:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header include/v1/foo.h --new-header include/v2/foo.h
```

Save a baseline snapshot at release time, then compare every new build against it:

```bash
abicheck dump libfoo.so -H include/foo.h --version 1.0 -o baseline.json
abicheck compare baseline.json ./build/libfoo.so --new-header include/foo.h
```

Supported output formats: `markdown` (default), `json`, `sarif`, `html`, and `junit`.

```bash
abicheck compare old.so new.so -H foo.h --format sarif -o report.sarif
```

See [Getting Started](https://napetrov.github.io/abicheck/getting-started/) for the full tutorial and [CLI Usage](https://napetrov.github.io/abicheck/user-guide/cli-usage/) for the complete command reference.

---

## Which command do I need?

| I want to… | Use |
|------------|-----|
| Check whether a library upgrade breaks existing consumers | [`abicheck compare`](https://napetrov.github.io/abicheck/user-guide/cli-usage/) |
| Compare **a multi-library release** (a co-versioned bundle, e.g. oneDAL) as a single bundle | [`abicheck compare-release`](https://napetrov.github.io/abicheck/user-guide/multi-binary/) |
| Check whether **my application** breaks with a new library version | [`abicheck appcompat`](https://napetrov.github.io/abicheck/user-guide/appcompat/) |
| Validate a binary's full dependency stack across two sysroots | [`abicheck stack-check`](https://napetrov.github.io/abicheck/user-guide/cli-usage/) |
| Drop-in replacement for `abi-compliance-checker` | [`abicheck compat`](https://napetrov.github.io/abicheck/user-guide/from-abicc/) |
| Save a reusable ABI baseline snapshot | [`abicheck dump`](https://napetrov.github.io/abicheck/getting-started/) |

---

## Exit codes

Use these to gate CI pipelines.

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` | Safe — no binary ABI break |
| `1` | `SEVERITY_ERROR` | Severity-driven error (with `--severity-*` flags) |
| `2` | `API_BREAK` | Source-level break (recompile needed, binary may still work) |
| `4` | `BREAKING` | Binary ABI break (old binaries will crash or misbehave) |
| `8` | `REMOVED_LIBRARY` | Library removed in new version (`compare-release` only) |

`appcompat`, `stack-check`, and `compat` use the same scheme with per-mode additions — see the [full exit code reference](https://napetrov.github.io/abicheck/reference/exit-codes/).

---

## GitHub Action

```yaml
- uses: napetrov/abicheck@v0.3.0
  with:
    old-library: abi-baseline.json
    new-library: build/libfoo.so
    new-header: include/foo.h
    format: sarif
    upload-sarif: true
```

The action installs Python, castxml, and abicheck automatically. Outputs: `verdict`, `exit-code`, `report-path`. See the [GitHub Action docs](https://napetrov.github.io/abicheck/user-guide/github-action/) for matrix builds, cross-compilation, and gating flags (`fail-on-breaking`, `fail-on-api-break`).

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
- [Policy Profiles](https://napetrov.github.io/abicheck/user-guide/policies/)
- [Suppressions](https://napetrov.github.io/abicheck/user-guide/suppressions/) (YAML schema, expiry, justification)
- [Migrating from ABICC](https://napetrov.github.io/abicheck/user-guide/from-abicc/)

---

## Python API

```python
from pathlib import Path
from abicheck.service import run_compare

result, old_snapshot, new_snapshot = run_compare(
    old_input=Path("libfoo.so.1"),
    new_input=Path("libfoo.so.2"),
    old_headers=[Path("include/v1/foo.h")],
    new_headers=[Path("include/v2/foo.h")],
)

print(result.verdict)       # e.g. Verdict.BREAKING
print(len(result.changes))  # number of detected changes
```

See `abicheck.service` for the full signature, plus the [MCP server integration](https://napetrov.github.io/abicheck/user-guide/mcp-integration/) for AI-agent workflows.

---

## Examples

The [`examples/`](examples/README.md) directory contains **126 real-world ABI/API scenarios** (121 single-library cases plus 5 multi-library bundle cases) with ground-truth verdicts. Most are single-library `v1`/`v2` examples with a consumer app; bundle/release-level cases use release-style layouts. The full catalog is the development regression corpus; a smaller historical cross-tool subset is kept in the reference docs for release-to-release comparison with libabigail and ABICC.

---

## Validation snapshot

The main validation target is the full **125-case catalog**. To scan it for the current checkout:

```bash
python scripts/benchmark_comparison.py --suite all
```

The command writes `benchmark_reports/benchmark_report.json` with the selected suite, abicheck version, git commit, tool versions, the `ground_truth.json` SHA-256, and per-tool accuracy. Cases that require bundle/release harnesses or unavailable compiler features are marked as unscored instead of being folded into single-library verdict accuracy.

For apples-to-apples comparison with libabigail and ABICC, release workflows also run the historical pinned cross-tool subset (`case01`-`case73` + `case26b`) and attach that report to GitHub Releases:

```bash
python scripts/benchmark_comparison.py --suite pinned74
```

### Detection by evidence source

The [five sources of information](#how-it-works--multiple-sources-of-information) each find breaks the weaker sources are blind to. The `--evidence-tiers` mode scans the catalog at each level so you can measure what every source unlocks:

```bash
python scripts/benchmark_comparison.py --evidence-tiers
```

| Source you provide | Cumulative cases reaching the correct verdict |
|--------------------|:---------------------------------------------:|
| Just the binary (`L0`) | 40 / 126 (32%) |
| + Debug symbols (`L1`) | 102 / 126 (81%) |
| + Public headers (`L2`) | 125 / 126 (99%) |
| + Build data / sources (`L3`/`L4`) | 126 / 126 (100%) |

More evidence also *removes* false positives (e.g. header scoping correctly dismisses internal-struct changes). See [Evidence & Detectability](https://napetrov.github.io/abicheck/concepts/evidence-and-detectability/) for what each source reveals and [Benchmarking by evidence tier](https://napetrov.github.io/abicheck/reference/tool-comparison/#benchmarking-by-evidence-tier) for the methodology.

Per-case matrix, methodology, full-catalog notes, and the pinned cross-tool comparison table: [Tool Comparison & Benchmarks](https://napetrov.github.io/abicheck/reference/tool-comparison/).

---

## Documentation

- **Start here:** [Getting Started](https://napetrov.github.io/abicheck/getting-started/)
- **User guide:** [CLI Usage](https://napetrov.github.io/abicheck/user-guide/cli-usage/) · [Application compatibility](https://napetrov.github.io/abicheck/user-guide/appcompat/) · [Output formats](https://napetrov.github.io/abicheck/user-guide/output-formats/) · [GitHub Action](https://napetrov.github.io/abicheck/user-guide/github-action/)
- **Concepts:** [Verdicts](https://napetrov.github.io/abicheck/concepts/verdicts/) · [Architecture](https://napetrov.github.io/abicheck/concepts/architecture/) · [ABI/API Handling & Recommendations](https://napetrov.github.io/abicheck/concepts/abi-api-handling/) · [Limitations](https://napetrov.github.io/abicheck/concepts/limitations/)
- **Reference:** [Change Kinds](https://napetrov.github.io/abicheck/reference/change-kinds/) · [Exit Codes](https://napetrov.github.io/abicheck/reference/exit-codes/) · [Platforms](https://napetrov.github.io/abicheck/reference/platforms/) · [Tool Comparison](https://napetrov.github.io/abicheck/reference/tool-comparison/)
- **Troubleshooting:** [Troubleshooting guide](https://napetrov.github.io/abicheck/troubleshooting/)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, code style, and PR workflow. Project status and roadmap: [development/goals.md](docs/development/goals.md).

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).

Copyright 2026 Nikolay Petrov
