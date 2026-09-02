# abicheck

**abicheck** detects breaking changes in C/C++ shared libraries before they reach production. Point it at two builds of a library (plus their headers), and it tells you whether existing binaries will keep working or break at runtime.

It supports ELF (Linux), PE/COFF (Windows), and Mach-O (macOS) binaries, and it's a drop-in replacement for `abi-compliance-checker`.

> **Gate ABI in CI in 5 lines.** Drop the first-class
> [GitHub Action](use/github-action.md) into any workflow — it installs
> everything, runs the comparison, sets the exit code, and can upload SARIF to
> the Security tab:
>
> ```yaml
> - uses: abicheck/abicheck@v0.5.0
>   with:
>     old-library: abi-baseline.json
>     new-library: build/libfoo.so
>     new-header: include/foo.h
> ```

## Why abicheck

- **Five-source evidence model** — abicheck overlays up to five independent, additive sources (the binary, its debug info, its public headers, its build-system data, and optionally its sources — `L0`–`L4`), cross-checks them against each other (DWARF/PDB debug info against the symbol table, header AST against build flags), and lets the strongest evidence win. Each source catches breaks the others miss. See [Evidence & Detectability](learn/evidence-and-detectability.md).
- **397 detection rules** — symbol removal, signature changes, struct/class layout drift, vtable reordering, enum value shifts, qualifier changes, calling conventions, and many more. See the [Change Kind Reference](reference/change-kinds.md).
- **Multiple output formats** — Markdown, JSON, SARIF (GitHub Code Scanning), HTML.
- **Policy profiles** — `strict_abi`, `sdk_vendor`, `plugin_abi`, or custom YAML overrides.
- **ABICC drop-in** — full flag parity for migrating from `abi-compliance-checker`.
- **CI-ready** — clear exit codes, SARIF upload, snapshot-based baselines, first-class GitHub Action.
- **Agent-friendly** — structured JSON/SARIF output and a typed [Python API](use/python-api.md) for AI-driven workflows; agents use the CLI or the API directly, no separate protocol server.

## How the documentation is organized

The docs are built from two complementary tracks, each ordered from
introductory to expert — the "Where to go next" list below routes by task/
persona instead, for whichever track (or both) a given question actually
needs:

1. **Learn the problem** — [ABI/API Compatibility](learn/abi-api-handling.md)
   is educational material that needs no abicheck knowledge: what ABI/API
   compatibility is, why libraries break their consumers, and how to design
   against it. Start at Step 1 — [ABI in Five Minutes](learn/abi-series/abi-in-5-minutes.md)
   assumes nothing, and the overview page's numbered steps take you from
   there through checking a multi-binary product in CI — and keep the
   [example encyclopedia](reference/examples/index.md) as a catalog of real breaks.
2. **Use the tool** — the [User Guide](start/getting-started.md) takes you from install
   and first check through CI integration to specialised workflows;
   [Concepts](learn/verdicts.md) explains how abicheck works — what a verdict
   means, what each evidence source (binary, debug info, headers, build data,
   sources) can and cannot see, and how the pipeline is built; and
   [Reference](reference/change-kinds.md) holds the exhaustive lookup tables
   (change kinds, exit codes, platforms, tool comparison).

## Where to go next

**New to abicheck?**

1. [Getting Started](start/getting-started.md) — install, first check, CI setup.
2. [Choose Your Workflow](start/choose-your-workflow.md) — a decision guide that maps your artifacts and CI policy to the exact command.
3. [Verdicts](learn/verdicts.md) — what each verdict means and how to react.
4. [CLI Usage](use/cli-usage.md) — every command, every flag.

**New to the ABI/API problem itself?**

- [ABI/API Compatibility](learn/abi-api-handling.md) — the consolidated guide.
- [ABI in Five Minutes](learn/abi-series/abi-in-5-minutes.md) — the series' first rung; [Part 0](learn/abi-series/00-product-contract.md) then makes compatibility a product contract, from first principles.
- [ABI Cheat Sheet](learn/abi-cheat-sheet.md) — which changes are safe, risky, or breaking, at a glance.

**Evaluating or comparing tools?**

- [Tool Comparison & Benchmarks](reference/tool-comparison.md) — abicheck vs `abidiff` vs ABICC on a pinned 74-case benchmark subset.
- [Examples & Case Encyclopedia](reference/examples/index.md) — generated pages for the single-library cases; bundle cases live under `examples/`.
- [ABI/API Compatibility](learn/abi-api-handling.md) — real-world scenarios with code, plus design patterns that prevent each break.
- [Limitations](learn/limitations.md) — what abicheck does *not* catch.

**Integrating into a release pipeline?**

- [GitHub Action](use/github-action.md) — ready-to-paste workflow.
- [Output Formats](use/output-formats.md) — SARIF, JSON, HTML.
- [Exit Codes](reference/exit-codes.md) — for gating CI.
- [Policy Profiles](use/policies.md) and [Suppressions](use/suppressions.md).

**Maintaining a public compatibility contract?**

- [Contract-Aware Compatibility](learn/contract-aware-compatibility.md) — gate only on what you actually promised (public headers, exports, or everything).
- [Contract Evaluation](use/contract-evaluation.md) — the commands and CI recipes.

**Checking multiple compilers and platforms?**

- [Scenario S17: Multiple Build and Compiler Profiles](integration/scenarios/multi-platform.md) — a worked GCC + Clang + MSVC `.abicheck.yml`.
- [Aggregate Reports](use/aggregate-reports.md) — fold the matrix back into one gate, and tell a universal break from a profile-specific one.

**Checking real applications and plugins?**

- [Application Compatibility](use/appcompat.md) — `compare --used-by`, including *why* a consumer depends on a changed declaration.
- [Plugin Systems](use/plugin-systems.md) — `compare --required-symbol`.

**Automating through Python or an agent?**

- [Python API](use/python-api.md) — typed requests, and a CLI/Python parity table.
- [Agent Skills](use/agent-skills.md) — four portable, triggerable skills a coding agent (Claude Code, Copilot, Codex, Cursor, Gemini CLI) loads to answer a compatibility question in the user's own words, no MCP server required.

**Migrating from another tool?**

- [Migrating from ABICC](use/from-abicc.md)
- [Migrating from libabigail](use/from-libabigail.md)

**Contributing or extending abicheck?**

- [Codebase Overview](contribute/codebase-overview.md)
- [Testing Strategy](contribute/testing.md)
- [Architecture Decision Records](contribute/adr/index.md)
- [Project Goals & Status](contribute/goals.md)

## Status

[![CI](https://github.com/abicheck/abicheck/actions/workflows/ci.yml/badge.svg)](https://github.com/abicheck/abicheck/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/abicheck.svg)](https://pypi.org/project/abicheck/)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/abicheck.svg)](https://anaconda.org/conda-forge/abicheck)
