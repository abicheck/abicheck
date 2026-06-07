# Codebase Overview -- abicheck

**Last reviewed:** 2026-06-07  
**Scope:** contributor-facing map of the current implementation. Historical audit
findings from the original 2026-03 review have been retired from this page; open
work now lives in [Backlog](backlog.md), [Use-Case Coverage Evaluation](usecase-coverage-evaluation.md),
and the [implementation plans](plans/index.md).

---

## 1. Architecture overview

`abicheck` is a Python-based ABI/API compatibility checker for C/C++ shared
libraries, shared objects, platform binaries, snapshots, package extracts, and
selected workflow topologies. The implementation is intentionally layered so
ELF/PE/Mach-O metadata, debug formats, header ASTs, policy, filtering, and report
rendering can evolve independently.

| Area | Primary modules | Role |
|---|---|---|
| Data model | `model.py`, `checker_types.py`, `serialization.py`, `diff_serialization.py` | Snapshot/result dataclasses, schema-compatible round trips, compatibility loading. |
| Input resolution | `service.py`, `dumper.py`, `dumper_castxml.py`, `build_context.py`, `debug_resolver.py` | Turn binaries, snapshots, headers, build dirs, and debug artifacts into `AbiSnapshot`s. |
| Binary metadata | `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py`, `binary_utils.py` | Platform metadata, exports/imports, versioning, hardening flags, archive detection. |
| Debug metadata | `dwarf_metadata.py`, `dwarf_advanced.py`, `dwarf_unified.py`, `dwarf_snapshot.py`, `pdb_parser.py`, `pdb_metadata.py`, `btf_metadata.py`, `ctf_metadata.py`, `type_metadata.py` | DWARF/PDB/BTF/CTF type and ABI metadata. |
| Core diffing | `checker.py`, `diff_symbols.py`, `diff_types.py`, `diff_platform.py`, `diff_cpp_patterns.py`, `diff_build_config.py`, `diff_sycl.py`, `diff_templates.py`, `diff_namespaces.py`, plus smaller `diff_*` modules | Registered detectors for symbols, types, platform metadata, C++ idioms, build matrices, SYCL/plugin interfaces, and language-specific edge cases. |
| Policy and classification | `checker_policy.py`, `change_registry.py`, `severity.py`, `policy_file.py`, `report_classifications.py` | `ChangeKind` catalog, built-in/custom policies, severity mappings, verdicts, and classification summaries. |
| Post-processing/scope | `diff_filtering.py`, `post_processing.py`, `surface.py`, `surface_graph.py`, `internal_leak.py`, `idioms.py`, `elf_symbol_filter.py` | Public-surface resolution, evidence tiers, redundancy filtering, reachability, idiom recognition, and false-positive controls. |
| Workflows | `cli.py`, `cli_compare_release.py`, `cli_appcompat.py`, `cli_stack.py`, `cli_baseline.py`, `cli_plugin.py`, `cli_probe.py`, `cli_surface.py`, `package.py`, `baseline.py`, `bundle.py`, `appcompat.py`, `stack_checker.py`, `resolver.py`, `binder.py`, `debian_symbols.py`, `mcp_server.py` | User-facing commands and higher-level workflows beyond a single pairwise compare. |
| Reporting | `reporter.py`, `html_report.py`, `sarif.py`, `junit_report.py`, `stack_report.py`, `stack_html.py`, `appcompat_html.py`, `report_summary.py`, `annotations.py` | Markdown/JSON/SARIF/HTML/JUnit reports, CI annotations, and workflow-specific renderers. |
| Compatibility | `compat/` | ABICC-compatible CLI, descriptor parsing, ABICC dump import, and XML report generation. |

---

## 2. Current strengths

### 2.1 Layered evidence model

The checker can compare at several evidence depths and reports that depth:

1. **ELF/PE/Mach-O-only metadata** — exports/imports, SONAME/install names,
   symbol binding/type/size, version requirements, and hardening metadata.
2. **Debug metadata** — DWARF/PDB/BTF/CTF type layout, field offsets, enum values,
   and selected calling-convention/toolchain signals.
3. **Header-aware AST metadata** — public declarations, source-only API signals,
   default arguments, deleted/final/noexcept/inline-style source features, and
   provenance used by public-surface scoping.
4. **Workflow overlays** — build matrices, bundle relationships, app-compat
   reachability, stack/sysroot resolution, plugin host↔plugin contracts, and
   baseline registry operations.

### 2.2 Policy-first classification

`ChangeKind` classification, built-in policies (`strict_abi`, `sdk_vendor`,
`plugin_abi`), custom policy files, severity thresholds, and verdict/exit-code
behavior are centralized in the policy modules rather than duplicated in report
renderers or CLI entry points.

### 2.3 False-positive control

Public-surface resolution, source/header provenance, reachability closure,
redundancy filtering, AST-DWARF deduplication, suppression audit trails, and
confidence/evidence output are all part of the normal compare pipeline.

### 2.4 Workflow breadth

The codebase now covers more than single-library comparison: release/package
comparison, bundle-aware analysis, application compatibility, stack checking,
Debian symbols, ABICC compatibility, MCP integration, baseline registries,
plugin contracts, build-matrix probes, and surface reports all have dedicated
modules and tests.

### 2.5 Defensive parsing posture

XML/YAML parsing uses safe loaders, binary readers avoid shelling out for core
metadata, archive inputs are detected explicitly, and MCP write paths enforce
extension and sensitive-directory restrictions. Parser/fuzzer safety checks
remain a backlog item rather than an ignored risk.

---

## 3. Open work should be tracked elsewhere

This page is not the issue tracker. For current status:

- [Use-Case Coverage Evaluation](usecase-coverage-evaluation.md) lists the
  current complete/planned/by-design-excluded use cases.
- [`usecase-registry.yaml`](usecase-registry.yaml) is the machine-checked source
  of truth for use-case status and evidence paths.
- [Implementation Plans](plans/index.md) lists the remaining planned gaps and
  links completed/decided plans for history.
- [Backlog](backlog.md) holds the small set of near-term hardening tasks.
- [Testing](testing.md) explains the test layers and CI gates.
