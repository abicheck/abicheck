# abicheck

**abicheck** detects breaking changes in C/C++ shared libraries before they reach production. Point it at two builds of a library (plus their headers), and it tells you whether existing binaries will keep working or break at runtime.

It supports ELF (Linux), PE/COFF (Windows), and Mach-O (macOS) binaries, and it's a drop-in replacement for `abi-compliance-checker`.

## Why abicheck

- **Three-layer analysis** — ELF/PE/Mach-O symbol tables + Clang AST (via castxml) + DWARF/PDB cross-check. Each layer catches things the others miss.
- **183 detection rules** — symbol removal, signature changes, struct/class layout drift, vtable reordering, enum value shifts, qualifier changes, calling conventions, and many more. See the [Change Kind Reference](reference/change-kinds.md).
- **Multiple output formats** — Markdown, JSON, SARIF (GitHub Code Scanning), HTML.
- **Policy profiles** — `strict_abi`, `sdk_vendor`, `plugin_abi`, or custom YAML overrides.
- **ABICC drop-in** — full flag parity for migrating from `abi-compliance-checker`.
- **CI-ready** — clear exit codes, SARIF upload, snapshot-based baselines, first-class GitHub Action.
- **Agent-friendly** — structured JSON, Python API, and an [MCP server](user-guide/mcp-integration.md) for AI-driven workflows.

## Where to go next

**New to abicheck?**

1. [Getting Started](getting-started.md) — install, first check, CI setup.
2. [Verdicts](concepts/verdicts.md) — what each verdict means and how to react.
3. [CLI Usage](user-guide/cli-usage.md) — every command, every flag.

**Evaluating or comparing tools?**

- [Tool Comparison & Benchmarks](reference/tool-comparison.md) — abicheck vs `abidiff` vs ABICC on a pinned 74-case benchmark subset.
- [Examples & Case Encyclopedia](examples/index.md) — all 116 documented cases with code, runtime demo, and fixes.
- [ABI Breaks Explained](concepts/abi-breaks-explained.md) — real-world scenarios with code.
- [Limitations](concepts/limitations.md) — what abicheck does *not* catch.

**Integrating into a release pipeline?**

- [GitHub Action](user-guide/github-action.md) — ready-to-paste workflow.
- [Output Formats](user-guide/output-formats.md) — SARIF, JSON, HTML.
- [Exit Codes](reference/exit-codes.md) — for gating CI.
- [Policy Profiles](user-guide/policies.md) and [Suppressions](user-guide/suppressions.md).

**Migrating from another tool?**

- [Migrating from ABICC](user-guide/from-abicc.md)
- [Migrating from libabigail](user-guide/from-libabigail.md)

**Contributing or extending abicheck?**

- [Codebase Overview](development/codebase-overview.md)
- [Testing Strategy](development/testing.md)
- [Architecture Decision Records](development/adr/index.md)
- [Project Goals & Status](development/goals.md)

## Status

[![CI](https://github.com/napetrov/abicheck/actions/workflows/ci.yml/badge.svg)](https://github.com/napetrov/abicheck/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/abicheck.svg)](https://pypi.org/project/abicheck/)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/abicheck.svg)](https://anaconda.org/conda-forge/abicheck)
