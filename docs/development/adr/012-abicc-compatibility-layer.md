# ADR-012: ABICC Drop-In Compatibility Layer

**Date:** 2026-03-18
**Status:** Accepted — implemented. **Amendment:** this ADR predates the
Tier-2 service boundary introduced by ADR-037 (D10.1, enforced by the
`cli-contract` AI-readiness check). `abicheck/compat/cli.py` still calls
`checker.compare()` directly rather than routing through
`service.run_compare` / `service.compare_snapshots`, consistent with its
"thin adapter" design below — the automated `cli-contract` gate scans
root-level `cli*.py` and does not currently cover the `compat/` subpackage,
so this is not enforced.
**Decision maker:** Nikolay Petrov

---

## Context

abi-compliance-checker (ABICC) is no longer actively maintained but remains
widely deployed in CI pipelines, distro build systems, and SDK validation
workflows. Users need a migration path that does not require rewriting their
automation.

### Requirements

- Accept ABICC-style inputs (XML descriptors, Perl dump files, skip lists)
- Produce ABICC-compatible output (XML reports, exit codes)
- Coexist with the native `compare` command — not replace it
- Minimize maintenance burden of the compatibility surface

### Options considered

| Option | Description | Trade-off |
|--------|-------------|-----------|
| A: Extend `compare` with ABICC flags | Single command, `--abicc-mode` flag | Clutters the native CLI; hard to maintain flag interactions |
| **B: Separate `compat` subcommand** | Dedicated command with ABICC semantics | Clean separation; two CLIs to document |
| C: Standalone `abicc-compat` binary | Completely separate entry point | Distribution complexity; code duplication |

---

## Decision

### Option B: Separate `compat` subcommand

```bash
# ABICC-compatible invocation
abicheck compat check -old old.xml -new new.xml

# With ABICC suppression files
abicheck compat check -old old.xml -new new.xml \
    -skip-symbols skip.txt -skip-types skip_types.txt

# Dump mode (ABICC-compatible)
abicheck compat dump -d descriptor.xml -o output/
```

### Architecture

```text
abicheck/compat/
├── cli.py          # Click CLI: compat check, compat dump
├── xml_report.py   # ABICC-format XML report generation
└── (reuses)        # checker.py, dumper.py, suppression.py
```

The compatibility layer is a **thin adapter** over the native pipeline:

1. **Input translation**: Parse ABICC XML descriptors → extract library path,
   header directories, version string, GCC options
2. **Suppression translation**: Convert ABICC skip lists (plain-text
   `skip_symbols`, `skip_types`, `skip_headers`) → native `SuppressionRule`
   objects. Heuristic: names containing regex characters (`*?.[`) become
   `symbol_pattern`; plain names become exact `symbol` matches with C++
   demangling fallback (`_Z\d+{name}.*`)
3. **Analysis**: Delegate to native `compare()` pipeline — the full detector
   suite (covering all 352 `ChangeKind` values) runs identically
4. **Output translation**: Convert `DiffResult` → ABICC XML report format
5. **Exit code translation**: Map native verdicts to ABICC exit codes

### Input format support

| Format | Detection | Handling |
|--------|-----------|---------|
| ABICC XML descriptor | `<version>` + `<headers>` tags | Parse → library path + headers + version |
| ABICC Perl dump | `$VAR1 = {` prefix | Auto-detected; parsed as pre-existing ABICC analysis |
| Native JSON snapshot | `"schema_version"` key | Passed directly to `compare()` |
| Raw binary (ELF/PE/Mach-O) | Magic bytes | Passed directly to `compare()` |

### Exit code contract

The `compat` command uses ABICC's exit code scheme for backward compatibility
(see ADR-009 for the full exit code design):

| Exit code | Normal mode | With `-strict` |
|-----------|-------------|----------------|
| 0 | NO_CHANGE, COMPATIBLE, COMPATIBLE_WITH_RISK | NO_CHANGE only |
| 1 | BREAKING | BREAKING, COMPATIBLE, COMPATIBLE_WITH_RISK, API_BREAK |
| 2 | API_BREAK | (promoted to 1) |
| 3 | Missing external tool (castxml, gcc) |
| 4 | File access error |
| 5 | Header compilation/parsing failure |
| 6 | Invalid descriptor/config/suppression input |
| 7 | Write failure (report output) |
| 8 | Analysis pipeline failure |
| 10 | Internal error (fallback) |
| 11 | Interrupted (KeyboardInterrupt) |

Error exit codes (3–11) are mapped via `_classify_compat_error_exit_code()`
which inspects exception types to match ABICC's conventions.

### XML report format

`compat/xml_report.py` generates ABICC-compatible XML output:

```xml
<report>
  <library>libfoo.so</library>
  <version1>1.0</version1>
  <version2>2.0</version2>
  <verdict>incompatible</verdict>
  <affected>3</affected>
  <problems>
    <problem>
      <symbol>foo_init</symbol>
      <change_type>Removed_Symbol</change_type>
      ...
    </problem>
  </problems>
</report>
```

The XML schema maps abicheck `ChangeKind` values to ABICC problem types
(e.g., `func_removed` → `Removed_Symbol`).

### When to use `compare` vs `compat`

- Use **`compare`** for new integrations — it offers richer exit codes (0/2/4),
  the full 5-tier verdict system, and SARIF output for GitHub Code Scanning.
- Use **`compat`** only as a drop-in replacement for ABICC in existing CI
  scripts. Once migrated, consider transitioning to `compare` for better
  granularity.
- Mixing both commands in the same pipeline is not recommended — their exit
  code schemes differ (ADR-009).

### What is NOT compatible

- ABICC's interactive HTML report format (we generate our own HTML — see
  ADR-014)
- ABICC's internal Perl data structures (we parse but don't replicate them)
- ABICC's `-app` flag (application compatibility is a separate feature —
  see ADR-005)

---

## Consequences

### Positive

- Existing ABICC users can migrate by changing one command in their CI scripts
- Native pipeline benefits (the full detector suite, policy profiles, SARIF
  output) are available through the compat entry point
- Clean separation prevents compat concerns from cluttering the native CLI
- ABICC suppression files work without modification

### Negative

- Two CLI surfaces (`compare` vs `compat`) to document and maintain
- XML report format is a backward-compatibility contract
- ABICC Perl dump parsing is fragile (undocumented format)
- Different exit codes between `compare` (0/2/4) and `compat` (0/1/2) may
  confuse users who use both

---

## References

- `abicheck/compat/cli.py` — ABICC-compatible CLI
- `abicheck/compat/xml_report.py` — ABICC XML report generation
- ADR-009 — Exit code contract (covers both `compare` and `compat` schemes)
- ADR-011 — ABI change classification taxonomy (all 352 ChangeKinds used in
  compat reports)
- ADR-037 — Tier-2 service boundary (`service.run_compare` /
  `compare_snapshots`); see the amendment above for how `compat/cli.py`
  relates to it
- Goal 1 in `docs/development/goals.md` — "Drop-In Replacement for ABICC"
