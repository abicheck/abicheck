# Project Goals

> **Context:** `abi-compliance-checker` (ABICC) is no longer actively maintained.
> `libabigail` is maintained by Red Hat but focuses on DWARF-only binary analysis.
> `abicheck` is a modern Python alternative — drop-in compatible with ABICC first, then better.

---

## Goal 1 — Drop-In Replacement for ABICC

Support everything ABICC currently does so existing users and pipelines can migrate without changes:

- Same detection coverage (C/C++ ABI breaks: symbols, types, vtables, enums, layout)
- CLI compatible with ABICC inputs (XML descriptors, headers + libs)
- JSON/HTML/Markdown reports with equivalent verdict semantics
- Support for suppression files

**Done:** 239 ChangeKinds implemented; YAML suppression files fully supported; ABICC compat CLI supports `-symbols-list` and `-types-list` whitelist flags (plain-text, one name per line); XML report generation for ABICC-compatible output; ABICC compat CLI with all major flags; auto-forwarding `abicheck compat <flags>` to `compat check`; test parity for ABICC 2.3.

---

## Goal 2 — Close Known Gaps + Extend

Fix known ABICC / libabigail limitations and add new detection capability:

- Toolchain/flag drift detection (`DW_AT_producer`, `-fshort-enums`, `-fpack-struct`)
- DWARF-aware layout analysis (calling convention, packing, RTTI/visibility boundaries)
- Header/API surface diff (AST-based, macro contracts, inline/template changes)
- Confidence/evidence tiers in output (ELF_ONLY / DWARF_AWARE / HEADER_AWARE)

**Done:** DWARF-aware struct/enum layout; calling convention, packing, toolchain flags detection; AST-DWARF deduplication; field qualifiers (const/volatile/mutable); enum/parameter rename heuristics; ELF_ONLY visibility tier used throughout detection; `Confidence` enum (high/medium/low) on `DiffResult` with `coverage_warnings` for disabled detectors (v0.2.0).

**Done:** Formalized the canonical `evidence_tier` scalar (`ELF_ONLY` / `DWARF_AWARE` / `HEADER_AWARE`, ordered by analysis depth) in the JSON output schema, alongside the existing raw `evidence_tiers` list. HEADER_AWARE is now distinct from DWARF_AWARE: the presence of a header/AST surface promotes the tier above DWARF-only debug info. See `EvidenceTier` in `checker_policy.py`.

**Backlog (MSVC end-to-end hardening):** Windows CI includes a non-blocking MSVC + PDB lane, while MinGW/native cross-platform coverage is part of the regular validation story. Promoting the MSVC lane to blocking and broadening its fixture matrix are tracked in `docs/development/backlog.md`.

---

## Goal 3 — Pass libabigail Test Suite

Run `abicheck` against libabigail's own regression test cases and reach 100% pass rate:

- Mirror libabigail's `tests/` corpus as integration examples
- Add per-case expected verdicts to CI
- Use as the compatibility regression gate before each release

**Done:** ~54 parity test functions across multiple suites (`test_abicc_parity`, `test_abicc_full_parity`, `test_abidiff_parity`, `test_xml_parity`, `test_sprint7/10` parity); 13 new ABI compatibility test cases (cases 42, 49–62); sentinel enum detection; function deletion edge-case hardening (abicc #100).

---

## Goal 4 — Agent-Friendly Design

Make the tool convenient for AI agents and automation pipelines:

- Structured JSON output (machine-readable, no scraping)
- Clear exit codes:
    - `compare` command: 0 = compatible/no_change, 2 = source break, 4 = breaking ABI change
    - `compat` command: 0 = compatible/no_change, 1 = breaking, 2 = error
- Python API (`from abicheck.service import run_compare`) — not just CLI
- `--format json/markdown` output modes
- Snapshot files for offline/async workflows (`abicheck dump` → `.abi.json`)

**Done:** JSON output, snapshot format, exit codes (0/2/4), SARIF 2.1.0 output; MCP server for AI agent integration; GitHub Action (`napetrov/abicheck@v0.3.0`) for CI; report filtering (`--show-only`, `--stat`, `--show-impact`, `--report-mode leaf`) for CI gate pipelines.

---

## Goal 5 — Compatibility Break Encyclopedia

For each break type: what it is, how it appears in the real world, and which tool detects it:

- `examples/caseXX_*/` — minimal compilable C/C++ examples
- Per-case `README.md` with: scenario → what breaks → which tools detect → severity
- Comparison table: `abicheck` vs `abicc` vs `libabigail` vs `nm`-only
- Coverage matrix showing evidence tier required (ELF-only / DWARF / Header / Runtime)

**Done:** 126 example cases with per-case `README.md`; the original 74-case subset remains the release-pinned cross-tool benchmark; gap report with coverage matrix (abicheck vs ABICC vs libabigail vs `nm`); the consolidated [ABI/API Handling & Recommendations](../concepts/abi-api-handling.md) guide plus the generated [Examples Encyclopedia](../examples/index.md); cross-platform CMake build support for all single-library example cases.

---

## Goal 6 — Distribution & Documentation

### conda-forge package

Distribute via conda-forge — `conda install -c conda-forge abicheck`.

- `castxml` declared as a conda run dependency so users get a working install with zero manual setup.
- PyPI remains available (`pip install abicheck`) for users who prefer pip, with castxml as a documented external prerequisite.
- conda-forge recipe auto-updates on each PyPI release via conda-forge bot.

### GitHub Pages documentation site

Public documentation at <https://napetrov.github.io/abicheck/>:

- Getting started / installation
- CLI reference
- ABI break catalog (rendered from `examples/`)
- Tool comparison table
- Architecture overview

**Done:** MkDocs (Material theme) site with full navigation; GitHub Actions auto-deploy to GitHub Pages on main push; docs include getting-started, CLI reference, case catalog, tool comparison, SARIF guide, ABICC compat guide, troubleshooting; published to PyPI and conda-forge; Trusted Publishing (OIDC) for PyPI; publish workflow with dry-run mode.

---

## Status summary

| Goal | Status |
|------|--------|
| G1: ABICC drop-in | Done — 239 ChangeKinds, compat CLI, suppression files, XML reports |
| G2: Known gaps | DWARF layout, toolchain flags, AST-DWARF dedup, confidence tracking, canonical evidence tier (ELF_ONLY/DWARF_AWARE/HEADER_AWARE) done |
| G3: libabigail tests | Done — ~54 parity test functions + 129 example cases |
| G4: Agent-friendly | Done — JSON, SARIF, exit codes, snapshots, MCP server, GitHub Action |
| G5: Break encyclopedia | Done — 129 example cases + consolidated ABI/API handling guide + coverage matrix |
| G6: Distribution & docs | Done — PyPI, conda-forge, MkDocs + GitHub Pages |

## Non-goals

- Runtime instrumentation or dynamic analysis — abicheck is a static offline tool.
- Source-level refactoring suggestions — it reports *what* broke, not how to fix your code.
- Support for languages other than C/C++ (Rust, Go, etc.) — out of scope for now.
- Static / import library archives (`.a`, `.lib`) — abicheck compares single
  linkable images (shared libraries and objects), not `ar` member archives. A
  static library has no runtime ABI surface (no SONAME, no dynamic symbol
  table); link-time API checking over archive members is a deliberate non-goal.
  Extract members (`ar x lib.a`) and compare the resulting objects, or the
  shared library built from them, instead. See
  [limitations](../concepts/limitations.md#static-import-library-archives-a-lib).
