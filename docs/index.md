# abicheck

**abicheck** detects breaking changes in C/C++ shared libraries before they reach production. Point it at two builds of a library (plus their headers), and it tells you whether existing binaries will keep working or break at runtime.

It supports ELF (Linux), PE/COFF (Windows), and Mach-O (macOS) binaries, and it's a drop-in replacement for `abi-compliance-checker`.

> **Gate ABI in CI in 5 lines.** Drop the first-class
> [GitHub Action](user-guide/github-action.md) into any workflow — it installs
> everything, runs the comparison, sets the exit code, and can upload SARIF to
> the Security tab:
>
> ```yaml
> - uses: napetrov/abicheck@v0.3.0
>   with:
>     old-library: abi-baseline.json
>     new-library: build/libfoo.so
>     new-header: include/foo.h
> ```

## Why abicheck

- **Three-layer analysis** — ELF/PE/Mach-O symbol tables + Clang AST (via castxml) + DWARF/PDB cross-check. Each layer catches things the others miss.
- **238 detection rules** — symbol removal, signature changes, struct/class layout drift, vtable reordering, enum value shifts, qualifier changes, calling conventions, and many more. See the [Change Kind Reference](reference/change-kinds.md).
- **Multiple output formats** — Markdown, JSON, SARIF (GitHub Code Scanning), HTML.
- **Policy profiles** — `strict_abi`, `sdk_vendor`, `plugin_abi`, or custom YAML overrides.
- **ABICC drop-in** — full flag parity for migrating from `abi-compliance-checker`.
- **CI-ready** — clear exit codes, SARIF upload, snapshot-based baselines, first-class GitHub Action.
- **Agent-friendly** — structured JSON, Python API, and an [MCP server](user-guide/mcp-integration.md) for AI-driven workflows.

## How the documentation is organized

The docs are two complementary tracks, each ordered from introductory to expert:

1. **Learn the problem** — [ABI/API Handling & Recommendations](concepts/abi-api-handling.md)
   is educational material that needs no abicheck knowledge: what ABI/API
   compatibility is, why libraries break their consumers, and how to design
   against it. Start with the [learning series](concepts/abi-series/00-product-contract.md)
   (part 0 assumes nothing; part 7 is expert-level design guidance) and keep the
   [129-case example encyclopedia](examples/index.md) as a catalog of real breaks.
2. **Use the tool** — the [User Guide](getting-started.md) takes you from install
   and first check through CI integration to specialised workflows;
   [Concepts](concepts/verdicts.md) explains how abicheck works — what a verdict
   means, what each evidence source (binary, debug info, headers, build data,
   sources) can and cannot see, and how the pipeline is built; and
   [Reference](reference/change-kinds.md) holds the exhaustive lookup tables
   (change kinds, exit codes, platforms, tool comparison).

## Where to go next

**New to abicheck?**

1. [Getting Started](getting-started.md) — install, first check, CI setup.
2. [Choose Your Workflow](user-guide/choose-your-workflow.md) — a decision guide that maps your artifacts and CI policy to the exact command.
3. [Verdicts](concepts/verdicts.md) — what each verdict means and how to react.
4. [CLI Usage](user-guide/cli-usage.md) — every command, every flag.

**New to the ABI/API problem itself?**

- [ABI/API Handling & Recommendations](concepts/abi-api-handling.md) — the consolidated guide.
- [Learning series, part 0](concepts/abi-series/00-product-contract.md) — compatibility as a product contract, from first principles.
- [ABI Cheat Sheet](concepts/abi-cheat-sheet.md) — which changes are safe, risky, or breaking, at a glance.

**Evaluating or comparing tools?**

- [Tool Comparison & Benchmarks](reference/tool-comparison.md) — abicheck vs `abidiff` vs ABICC on a pinned 74-case benchmark subset.
- [Examples & Case Encyclopedia](examples/index.md) — generated pages for the single-library cases; bundle cases live under `examples/`.
- [ABI/API Handling & Recommendations](concepts/abi-api-handling.md) — real-world scenarios with code, plus design patterns that prevent each break.
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
