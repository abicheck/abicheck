# ADR Gap Analysis — Decisions Needing Formal ADRs

**Date:** 2026-03-18
**Author:** Claude Code (automated analysis)
**Purpose:** Identify implemented project decisions/assumptions that lack a formal ADR

---

## Methodology

This analysis cross-references:
1. Existing ADRs (001–008, later extended to 001–019) and their coverage
2. Implemented code in `abicheck/` (114 ChangeKinds, policy system, output formats, CLI, etc.)
3. `pyproject.toml`, CI workflows, GitHub Action, and documentation
4. `docs/development/goals.md` and `CHANGELOG.md` for stated-but-undocumented decisions

Each candidate is rated by **urgency**:
- **HIGH** — Decision is complex, non-obvious, and actively affects contributors/users
- **MEDIUM** — Decision is important but stable; documenting prevents future confusion
- **LOW** — Decision is straightforward or unlikely to be revisited

---

## Candidate ADR 1: Verdict System and Exit Code Contract

**Urgency: HIGH**

**What exists:** The 5-tier verdict system (`NO_CHANGE`, `COMPATIBLE`, `COMPATIBLE_WITH_RISK`, `API_BREAK`, `BREAKING`) and the exit code mapping (`0/2/4` for `compare`, `0/1/2` for `compat`) are implemented in `checker_policy.py` and `cli.py`.

**Key decisions embedded in code:**
- `COMPATIBLE_WITH_RISK` is a distinct tier (not folded into `COMPATIBLE` or `API_BREAK`)
- Exit code 4 (not 1) signals binary ABI break — allows bitwise OR composition
- Unknown `ChangeKind` values default to `BREAKING` (fail-safe)
- Verdict is computed on the full change set regardless of display filters
- `compat` command uses a different exit code scheme than `compare`

**Why it needs an ADR:**
- External tools and CI pipelines depend on the exit code contract
- The `COMPATIBLE_WITH_RISK` tier is a novel design choice not found in ABICC or libabigail
- The fail-safe default for unknown kinds is a safety-critical design decision
- Different exit codes for `compare` vs `compat` needs explicit justification

---

## Candidate ADR 2: Policy Profile System (strict_abi / sdk_vendor / plugin_abi)

**Urgency: HIGH**

**What exists:** Three built-in policy profiles with explicit downgrade sets in `checker_policy.py:466–509`. Custom YAML policy files in `policy_file.py`. The `policy_kind_sets()` function is declared "the single source of truth."

**Key decisions embedded in code:**
- `strict_abi` is the default (maximum severity for all changes)
- `sdk_vendor` downgrades source-level-only API_BREAK kinds to COMPATIBLE
- `plugin_abi` downgrades calling-convention BREAKING kinds to COMPATIBLE AND promotes RISK_KINDS to BREAKING
- `plugin_abi` has unique semantics: deployment-floor risks become BREAKING
- Custom YAML policies override individual `ChangeKind` severity
- Import-time assertions enforce that downgrade sets are proper subsets

**Why it needs an ADR:**
- Policy profiles fundamentally change what constitutes a "break" — users must understand the trade-offs
- The `plugin_abi` RISK→BREAKING promotion is non-obvious and needs architectural rationale
- Custom policy files create a user-facing contract (YAML schema)
- Interaction between `--policy` and `--policy-file` needs formal specification

---

## Candidate ADR 3: ABI Change Classification Taxonomy (114 ChangeKinds)

**Urgency: MEDIUM**

**What exists:** 85+ `ChangeKind` enum values in `checker_policy.py`, each classified into exactly one of `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, or `RISK_KINDS`. Many classifications have inline comments justifying the choice.

**Key decisions embedded in code:**
- `ENUM_MEMBER_ADDED` → COMPATIBLE (not BREAKING), because value shifts are caught separately
- `FUNC_NOEXCEPT_ADDED/REMOVED` → COMPATIBLE (not BREAKING), because Itanium ABI mangling doesn't change
- `SYMBOL_BINDING_CHANGED` (GLOBAL→WEAK) → COMPATIBLE (not BREAKING)
- `FUNC_REMOVED_ELF_ONLY` → COMPATIBLE (visibility cleanup heuristic)
- `TYPE_FIELD_ADDED` → BREAKING for polymorphic types, `TYPE_FIELD_ADDED_COMPATIBLE` for standard-layout
- `FIELD_BECAME_CONST` etc. → COMPATIBLE (field qualifiers are informational)
- `FUNC_BECAME_INLINE` → API_BREAK (not BREAKING — symbol may or may not vanish)
- `TYPEDEF_VERSION_SENTINEL` → COMPATIBLE (version-stamped typedefs are compile-time only)
- `SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED` → COMPATIBLE_WITH_RISK (dependency origin detection)

**Why it needs an ADR:**
- Some classifications diverge from ABICC/libabigail (intentionally) — documenting why prevents regressions
- The `noexcept` and `enum_member_added` decisions are contentious in the ABI community
- ADR-001 documents a few classifications inline but the full taxonomy deserves its own record
- Contributors adding new ChangeKinds need a classification framework, not just examples

**Note:** ADR-001 already contains `NEEDED_ADDED`, `SYMBOL_BINDING_STRENGTHENED`, and `SYMBOL_SIZE_CHANGED` classification rationale. A new ADR should consolidate and extend these.

---

## Candidate ADR 4: ABICC Drop-In Compatibility Layer

**Urgency: MEDIUM**

**What exists:** `abicheck/compat/cli.py` (1328 lines) and `abicheck/compat/xml_report.py` (398 lines) implement a full ABICC-compatible CLI and XML report format. The `abicheck compat` command accepts ABICC-style inputs (XML descriptors, version strings).

**Key decisions embedded in code:**
- Separate `compat` subcommand (not CLI flag on `compare`)
- ABICC XML descriptor parsing (old_descriptor.xml → library path + headers + version)
- ABICC XML report generation for backward-compatible output
- ABICC suppression file format support (skip_symbols / skip_headers / skip_types)
- Different exit codes: `compat` uses 0/1/2 vs `compare` uses 0/2/4
- Format-detection heuristic: Perl data dump files from ABICC are auto-detected

**Why it needs an ADR:**
- ABICC compatibility is a stated project goal (Goal 1) but the architecture is undocumented
- The decision to use a separate `compat` command vs extending `compare` has implications
- The XML report format is a compatibility contract
- Exit code differences between `compare` and `compat` need justification

---

## Candidate ADR 5: Suppression System Design

**Urgency: MEDIUM**

**What exists:** `suppression.py` (291 lines) implements YAML-based suppression rules with symbol/type/version/platform/scope filters, Python `re`-based pattern matching with fullmatch semantics, and expiry dates.

**Key decisions embedded in code:**
- YAML format (not JSON or TOML) for suppression files
- Python `re` module with `fullmatch()` semantics (see ADR-013 for ReDoS considerations)
- Suppression applied before redundancy filtering (ordering matters for verdicts)
- Both abicheck-native YAML and ABICC skip/whitelist formats supported
- Suppressed changes tracked in `DiffResult.suppressed_changes` for audit trail
- Entry-level validation: unknown keys rejected, expired entries warned

**Why it needs an ADR:**
- Suppression ordering relative to other pipeline stages affects correctness
- Regex engine choice is a security decision that should be formally documented
- Dual-format support (YAML + ABICC format) adds maintenance burden — needs justification
- Expiry dates on suppressions is a novel feature not in reference tools

---

## Candidate ADR 6: Output Format Strategy (Markdown / JSON / SARIF / HTML)

**Urgency: MEDIUM**

**What exists:** Five output formats (`markdown`, `json`, `sarif`, `html`, `junit`, plus a compact `review` digest) implemented across `reporter.py`, `sarif.py`, `html_report.py`, `junit_report.py`. SARIF 2.1.0 is specifically targeted for GitHub Code Scanning integration.

**Key decisions embedded in code:**
- Markdown is the default format (human-readable, CI-friendly)
- JSON output uses `AbiSnapshot` schema with `schema_version` field (currently v3)
- SARIF maps `ChangeKind` → SARIF rule IDs and severity → SARIF level
- HTML is self-contained (embedded CSS, no external dependencies)
- All formats preserve the same information (no format-specific data loss)
- JSON snapshots are interchangeable regardless of data source (DWARF vs castxml)

**Why it needs an ADR:**
- `schema_version` is a versioned contract — backward compatibility rules need definition
- SARIF mapping decisions (which severity maps to which SARIF level) affect GitHub Code Scanning UX
- The decision to embed CSS in HTML (vs external stylesheet) affects deployment

---

## Candidate ADR 7: MCP Server Integration (AI Agent Interface)

**Urgency: LOW**

**What exists:** `mcp_server.py` (750+ lines) exposes abicheck as a FastMCP server for AI agents. Separate entry point `abicheck-mcp`.

**Key decisions embedded in code:**
- MCP is an optional dependency (`pip install abicheck[mcp]`)
- FastMCP framework chosen (over raw MCP protocol implementation)
- Stdio transport by default
- Tools exposed: dump, compare, explain-change, list-change-kinds
- Graceful ImportError with helpful message if mcp package not installed

**Why it needs an ADR:**
- MCP is a relatively new protocol — commitment to it as an integration path should be documented
- The tool surface area (which operations are exposed) is a design decision
- Optional dependency strategy affects distribution and testing

---

## Candidate ADR 8: Snapshot Serialization and Schema Versioning

**Urgency: MEDIUM**

**What exists:** `serialization.py` (490 lines) with `SCHEMA_VERSION = 3`, explicit version history comments, and forward/backward compatibility handling.

**Key decisions embedded in code:**
- Schema version is an integer (not semver)
- v1 snapshots (pre-versioning) are handled transparently
- Sets are converted to sorted lists for JSON determinism
- `AbiSnapshot` is the canonical interchange format between dump/compare/report stages
- Snapshots are platform-agnostic (ELF/PE/Mach-O metadata fields are all optional)
- `dataclasses.asdict()` is used with post-processing (not custom serialization)

**Why it needs an ADR:**
- Schema version bumps are breaking changes for snapshot consumers
- The decision to use integer versioning (not semver) needs rationale
- Cross-mode snapshot comparison (DWARF snapshot vs castxml snapshot) relies on schema equivalence

---

## Candidate ADR 9: Three-Tier Visibility Model (PUBLIC / HIDDEN / ELF_ONLY)

**Urgency: MEDIUM**

**What exists:** `Visibility` enum in `model.py` with three values. `ELF_ONLY` is used throughout detection as a confidence/provenance tier.

**Key decisions embedded in code:**
- `ELF_ONLY` is not a visibility attribute but a detection confidence indicator
- `FUNC_REMOVED_ELF_ONLY` is COMPATIBLE (not BREAKING) — treats symbol-only removals as visibility cleanup
- Visibility affects how changes are classified (different ChangeKinds for ELF_ONLY vs PUBLIC)
- In DWARF-only mode, visibility is determined by intersection with ELF exported symbols

**Why it needs an ADR:**
- This is a novel concept not in ABICC or libabigail
- The conflation of "source of data" with "visibility" in a single enum is a design choice
- The verdict difference between `FUNC_REMOVED` (BREAKING) and `FUNC_REMOVED_ELF_ONLY` (COMPATIBLE) is significant

---

## Candidate ADR 10: GitHub Action Design

**Urgency: LOW**

**What exists:** `action.yml` with multiple operation modes (compare, dump, deps, stack-check), support for ABICC Perl dumps, and PR comment integration.

**Key decisions embedded in code:**
- Composite action (not Docker-based) — runs in the user's environment
- `castxml` installed via apt in the action setup step
- Multiple operation modes exposed through a single `mode` input
- Supports both native abicheck inputs and ABICC migration inputs
- PR comment posting with diff summary

**Why it needs an ADR:**
- Composite vs Docker action affects portability (Linux-only for apt)
- The castxml installation strategy affects action startup time and reliability
- Supporting ABICC inputs in the action adds complexity for migration use case

---

## Candidate ADR 11: Cross-Platform Binary Format Support Strategy

**Urgency: LOW**

**What exists:** Separate metadata modules: `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py`. Platform detection in `model.py`. PDB parser (`pdb_parser.py`, `pdb_metadata.py`) for Windows debug info.

**Key decisions embedded in code:**
- Each platform has an independent metadata module (no shared base class)
- PDB support is "minimal" — custom parser, not a full PDB implementation
- PDB metadata is adapted to DWARF structures (`StructLayout`, `FieldInfo`)
- Mach-O `compat_version` is treated as equivalent to ELF SONAME for versioning
- Platform is detected from binary content, not from file extension

**Why it needs an ADR:**
- ADR-001 lists the libraries but doesn't document the adapter pattern or PDB strategy
- The decision to write a custom PDB parser (vs depending on an external tool) is significant
- Platform-specific ChangeKinds (e.g., `COMPAT_VERSION_CHANGED`) need justification

---

## Candidate ADR 12: Testing Strategy and Parity Validation

**Urgency: LOW**

**What exists:** 120+ test files, 4 test tiers (unit, integration, parity-abicc, parity-libabigail), 126 example cases, conditional CI gates.

**Key decisions embedded in code:**
- 80% coverage threshold enforced in CI
- Parity tests are gated on file-change detection (not run on every PR)
- Example cases serve as both documentation and regression tests
- ABICC and libabigail are test-only dependencies (not runtime)
- Test markers (`@pytest.mark.integration` etc.) control what runs where

**Why it needs an ADR:**
- The parity testing strategy is central to project credibility
- The 80% threshold is a policy decision
- Conditional gating logic affects CI reliability

---

## Summary and Prioritization

> **Note:** This gap analysis was created as a point-in-time snapshot. ADRs
> 009–019 have since been written and accepted, covering candidates 1–6 and
> 8–12. Only candidate 7 (MCP Server Integration) remains undocumented.

| # | Candidate ADR | Urgency | Status |
|---|--------------|---------|--------|
| 1 | Verdict System and Exit Code Contract | **HIGH** | **Documented → ADR-009** |
| 2 | Policy Profile System | **HIGH** | **Documented → ADR-010** |
| 3 | ABI Change Classification Taxonomy | MEDIUM | **Documented → ADR-011** |
| 4 | ABICC Drop-In Compatibility Layer | MEDIUM | **Documented → ADR-012** |
| 5 | Suppression System Design | MEDIUM | **Documented → ADR-013** |
| 6 | Output Format Strategy | MEDIUM | **Documented → ADR-014** |
| 7 | MCP Server Integration | LOW | Not documented |
| 8 | Snapshot Serialization and Schema Versioning | MEDIUM | **Documented → ADR-015** |
| 9 | Three-Tier Visibility Model | MEDIUM | **Documented → ADR-016** |
| 10 | GitHub Action Design | LOW | **Documented → ADR-017** |
| 11 | Cross-Platform Binary Format Support | LOW | **Documented → ADR-018** |
| 12 | Testing Strategy and Parity Validation | LOW | **Documented → ADR-019** |

### Remaining gap

Only candidate 7 (MCP Server Integration) lacks a formal ADR. This is low
urgency since MCP is an optional feature with a small user surface.

### Existing ADR status review

| ADR | Title | Status | Assessment |
|-----|-------|--------|------------|
| 001 | Technology Stack | Accepted | Good but overloaded — contains classification decisions that belong in ADR-011 |
| 002 | Multi-binary Release Compare | Accepted | Implemented in v0.2.0 (`compare-release` command) |
| 003 | Data Source Architecture | Accepted | Implemented — DwarfSnapshotBuilder landed in v0.2.0 |
| 004 | Report Filtering and Deduplication | Accepted | Implemented in v0.2.0 (`--show-only`, `--stat`, `--report-mode leaf`) |
| 005 | Application Compatibility Checking | Accepted | Implemented in v0.2.0 (`appcompat` command) |
| 006 | Package-Level Comparison | Accepted | Implemented in v0.2.0 (RPM/Deb/tar/conda/wheel extraction) |
| 007 | BTF and CTF Debug Format Support | Proposed | Future work — well-specified |
| 008 | Full-Stack Dependency Validation | Accepted | Implemented in v0.2.0 (`deps`, `stack-check` commands) |
