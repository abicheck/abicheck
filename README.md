# abicheck

[![CI](https://github.com/napetrov/abicheck/actions/workflows/ci.yml/badge.svg)](https://github.com/napetrov/abicheck/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/napetrov/abicheck/branch/main/graph/badge.svg)](https://codecov.io/gh/napetrov/abicheck)
[![PyPI version](https://img.shields.io/pypi/v/abicheck.svg)](https://pypi.org/project/abicheck/)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/abicheck.svg)](https://anaconda.org/conda-forge/abicheck)
[![Python versions](https://img.shields.io/pypi/pyversions/abicheck.svg)](https://pypi.org/project/abicheck/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

**abicheck** detects breaking changes in C/C++ shared libraries before they reach production. It compares two versions of a shared library — along with their public headers — and reports whether existing binaries will continue to work or break at runtime.

It catches removed or renamed symbols, changed function signatures, struct layout drift, vtable reordering, enum value reassignment, and many more — **183 ABI/API change types** in total — that cause crashes, silent data corruption, or linker failures after a library upgrade.

> **Platforms:** Linux (ELF), Windows (PE/COFF), macOS (Mach-O). Binary and header AST analysis on all platforms; debug-info cross-check uses DWARF (Linux, macOS) and PDB (Windows).

**Full documentation:** **[napetrov.github.io/abicheck](https://napetrov.github.io/abicheck/)**

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

Supported output formats: `markdown` (default), `json`, `sarif`, `html`.

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
- uses: napetrov/abicheck@v1
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

The [`examples/`](examples/README.md) directory contains **121 real-world ABI/API scenarios** (116 single-library cases plus 5 multi-library bundle cases) with ground-truth verdicts. Most are single-library `v1`/`v2` examples with a consumer app; bundle/release-level cases use release-style layouts. A pinned **74-case subset** drives the reproducible validation snapshot below.

---

## Validation snapshot

Accuracy on the pinned 74-case benchmark subset (`01–73` + `26b`, drawn from the full 121-case catalog):

| Configuration | Exact verdict accuracy | Scan status |
|---|---:|---|
| `abicheck compare` | **74/74 (100%)** | 74/74 completed |
| `abicheck compat` | **71/74 (95%)** | 74/74 completed |
| `abicheck strict` | **62/74 (83%)** | 74/74 completed |
| `abidiff` | **22/73 (30%)** | 73/74 completed; `case16` hangs |
| `abidiff+headers` | **22/73 (30%)** | 73/74 completed; `case16` hangs |
| `ABICC(dump)` | **51/71 (71%)** | 71/74 scored |
| `ABICC(xml)` | **50/72 (69%)** | 72/74 scored |

These numbers are reproducible from `scripts/benchmark_comparison.py`, which
writes `benchmark_reports/benchmark_report.json` with the abicheck version, git
commit, oracle tool versions, the `ground_truth.json` SHA-256, and per-tool
accuracy. Release workflows attach that report to future GitHub Releases. To
generate a fresh report for the current checkout, run:

```bash
python scripts/benchmark_comparison.py   # writes benchmark_reports/benchmark_report.json
```

Per-case matrix, methodology, and the pinned comparison table: [Tool Comparison & Benchmarks](https://napetrov.github.io/abicheck/reference/tool-comparison/).

---

## Documentation

- **Start here:** [Getting Started](https://napetrov.github.io/abicheck/getting-started/)
- **User guide:** [CLI Usage](https://napetrov.github.io/abicheck/user-guide/cli-usage/) · [Application compatibility](https://napetrov.github.io/abicheck/user-guide/appcompat/) · [Output formats](https://napetrov.github.io/abicheck/user-guide/output-formats/) · [GitHub Action](https://napetrov.github.io/abicheck/user-guide/github-action/)
- **Concepts:** [Verdicts](https://napetrov.github.io/abicheck/concepts/verdicts/) · [Architecture](https://napetrov.github.io/abicheck/concepts/architecture/) · [ABI Breaks Explained](https://napetrov.github.io/abicheck/concepts/abi-breaks-explained/) · [Limitations](https://napetrov.github.io/abicheck/concepts/limitations/)
- **Reference:** [Change Kinds](https://napetrov.github.io/abicheck/reference/change-kinds/) · [Exit Codes](https://napetrov.github.io/abicheck/reference/exit-codes/) · [Platforms](https://napetrov.github.io/abicheck/reference/platforms/) · [Tool Comparison](https://napetrov.github.io/abicheck/reference/tool-comparison/)
- **Troubleshooting:** [Troubleshooting guide](https://napetrov.github.io/abicheck/troubleshooting/)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, code style, and PR workflow. Project status and roadmap: [development/goals.md](docs/development/goals.md).

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).

Copyright 2026 Nikolay Petrov
