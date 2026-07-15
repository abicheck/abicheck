# ADR-004: Report Filtering, Deduplication, and Leaf-Change Mode

**Date:** 2026-03-17
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

> **2026-07 amendment:** The `_deduplicate_ast_dwarf()` / `_enrich_affected_symbols()`
> line-number citations below (`checker.py:1922` / `checker.py:1841`) are stale —
> both functions now live in `abicheck/diff_filtering.py`. Don't trust hardcoded
> line numbers in this doc; they drift. All of the mechanisms decided here
> (`--show-redundant`, `--report-mode leaf`, `--show-only`, `--stat`,
> `--show-impact`, `caused_by_type`/`caused_count` on `Change`) are implemented
> and live on the `compare` command in `abicheck/cli.py`/`abicheck/reporter.py`.
> Presentation/classification for rendering has since been centralized further:
> ADR-036 introduced `ReportModel` (`abicheck/report_model.py`) as the render-ready
> view over a `DiffResult`, and renderers (including the `--show-only` filter
> pipeline described here) are now thin projections over it rather than each
> re-deriving buckets — see ADR-036 for the current ownership of report
> presentation.

---

## Context

### Current state

abicheck already has several filtering and deduplication mechanisms:

1. **AST↔DWARF deduplication** (`_deduplicate_ast_dwarf()` in checker.py:1922):
   Two-pass dedup that collapses DWARF findings that duplicate AST findings.
   - Pass 1: exact `(kind, description)` dedup
   - Pass 2: cross-kind dedup (e.g., `STRUCT_SIZE_CHANGED` dropped when `TYPE_SIZE_CHANGED` exists for same symbol)

2. **Affected symbols enrichment** (`_enrich_affected_symbols()` in checker.py:1841):
   For type/enum changes, BFS-walks the type graph to find all exported functions
   that use the affected type (direct or transitive through struct embedding).

3. **Suppression system** (suppression.py): Post-comparison filtering by symbol, pattern,
   type, source location, change kind — with expiry dates.

4. **Policy-based classification** (checker_policy.py): Changes classified into
   BREAKING / API_BREAK / COMPATIBLE_WITH_RISK / COMPATIBLE sets per policy profile.

5. **Report sections** (reporter.py): Markdown/JSON reports already group changes by
   severity: breaking → source breaks → risk → compatible.

### Problem

Despite these mechanisms, reports for real-world libraries are often **noisy**:

- **Type propagation noise**: When `struct Config` changes, every function using
  `Config*` generates separate `FUNC_PARAMS_CHANGED` entries. A single struct change
  affecting 30 functions produces 30+ changes in the report.

- **No output filtering**: Users cannot ask for "only breaking changes" or "only
  function changes" without post-processing JSON output.

- **No summary mode**: For CI gates, users often want just "BREAKING: 3 changes"
  without the full report.

- **No leaf-change view**: The report doesn't distinguish "root cause" type changes
  from "derived" interface changes.

### Reference

libabigail addresses similar problems with `--no-redundant` (default on),
`--leaf-changes-only`, `--impacted-interfaces`, `--stat`, and per-category filters
(`--added-fns`, `--deleted-fns`, etc.). We should solve the same UX problem but
in a way that fits abicheck's architecture (post-comparison filtering on `DiffResult`,
not embedded in detector logic).

---

## Decision

### 1. Redundancy filtering (post-comparison pass)

Add `_filter_redundant()` after `_deduplicate_ast_dwarf()` in the `compare()` pipeline:

```python
def _filter_redundant(changes: list[Change]) -> tuple[list[Change], list[Change]]:
    """Identify changes that are consequences of a root type change.

    Returns (kept, redundant) — redundant changes are still available for audit.
    """
```

**Algorithm:**

```text
1. Collect root changes: all TYPE_CHANGE_KINDS where symbol is a type name
   (TYPE_SIZE_CHANGED, TYPE_FIELD_*, ENUM_MEMBER_*, TYPE_ALIGNMENT_CHANGED, etc.)

2. Build root_types: dict[type_name → Change]

3. For each non-root change:
   - If FUNC_PARAMS_CHANGED and the changed param type ∈ root_types → redundant
   - If FUNC_RETURN_CHANGED and the changed return type ∈ root_types → redundant
   - If VAR_TYPE_CHANGED and the variable type ∈ root_types → redundant
   - If TYPE_FIELD_TYPE_CHANGED and the field type ∈ root_types → redundant (nested)

4. Root change gets annotated:
   - caused_count: int (how many derived changes were collapsed)
   - derived_symbols: list[str] (mangled names of affected interfaces)
```

**What is NOT redundant** (always shown):
- `FUNC_REMOVED` / `FUNC_ADDED` — symbol presence is always independent
- `VAR_REMOVED` / `VAR_ADDED`
- ELF-level changes (`SONAME_CHANGED`, `SYMBOL_*`, `NEEDED_*`)
- Changes where the function signature changed independently of the type

**Matching heuristic**: A derived change `c` is redundant if:
- `c.old_value` or `c.new_value` contains the root type name, OR
- `c.description` references the root type, OR
- `c.symbol` (mangled name) demangles to a function whose params include the root type

This is conservative — false negatives (showing too much) are safer than false
positives (hiding real changes).

**Default behavior**: Redundancy filtering **ON**. Flag `--show-redundant` disables it.

### 2. Report format: leaf-change mode

New `--report-mode` option with two values:

| Mode | Description | Default? |
|------|-------------|----------|
| `full` | All changes listed individually (current behavior + redundancy filter) | Yes |
| `leaf` | Root type changes with impact lists | No |

**Leaf mode Markdown output:**

```markdown
## Breaking Changes

### struct Config — size changed (64 → 72 bytes)
Field `timeout_ms` added at offset 64

**Affected interfaces (12):**
- `config_init(struct Config*)`
- `config_load(const char*, struct Config*)`
- ... (10 more)

### enum ErrorCode — member removed
`ERR_LEGACY` removed (was value 5)

**Affected interfaces (8):**
- `get_error_string(enum ErrorCode)`
- ...
```

**Leaf mode JSON output:**

```json
{
  "leaf_changes": [
    {
      "kind": "type_size_changed",
      "symbol": "Config",
      "description": "size changed from 64 to 72 bytes",
      "affected_count": 12,
      "affected_symbols": ["config_init", "config_load", "..."]
    }
  ],
  "non_type_changes": [
    {"kind": "func_removed", "symbol": "legacy_api", "...": "..."}
  ]
}
```

Implementation: `to_markdown()` and `to_json()` in reporter.py check `report_mode`
and reorganize output. No changes to `compare()` or detectors.

### 3. Output filters (`--show-only`)

New `--show-only` flag that accepts a comma-separated list of filter tokens:

```bash
abicheck compare old.so new.so --show-only breaking
abicheck compare old.so new.so --show-only functions,removed
abicheck compare old.so new.so --show-only types,changed
abicheck compare old.so new.so --show-only elf
```

| Token | Filters to |
|-------|-----------|
| `breaking` | Changes in BREAKING set |
| `api-break` | Changes in API_BREAK set |
| `risk` | Changes in COMPATIBLE_WITH_RISK set |
| `compatible` | Changes in COMPATIBLE set |
| `functions` | Changes with `FUNC_*` kind |
| `variables` | Changes with `VAR_*` kind |
| `types` | Changes with `TYPE_*` / `STRUCT_*` kind |
| `enums` | Changes with `ENUM_*` kind |
| `elf` | Changes with `SONAME_*` / `NEEDED_*` / `SYMBOL_*` kind |
| `added` | Changes with `*_ADDED` kind |
| `removed` | Changes with `*_REMOVED` / `*_DELETED` kind |
| `changed` | Changes that are not added/removed |

#### Filter evaluation rules

Tokens fall into three **dimensions**:

| Dimension | Tokens |
|-----------|--------|
| **Severity** | `breaking`, `api-break`, `risk`, `compatible` |
| **Element** | `functions`, `variables`, `types`, `enums`, `elf` |
| **Action** | `added`, `removed`, `changed` |

A change matches the filter if it satisfies **all** specified dimensions (AND
across dimensions). Within a single dimension, multiple tokens are combined with
OR (e.g., `breaking,api-break` = severity is BREAKING or API_BREAK).

**Formal rule**: `match = (severity_ok OR no severity token) AND (element_ok OR no element token) AND (action_ok OR no action token)`

**Examples:**

| Flag | Interpretation | Result |
|------|---------------|--------|
| `--show-only breaking` | severity=BREAKING | All breaking changes regardless of element/action |
| `--show-only functions,removed` | element=functions AND action=removed | Only `FUNC_REMOVED` / `FUNC_DELETED` |
| `--show-only breaking,functions` | severity=BREAKING AND element=functions | Breaking changes that are function-related |
| `--show-only breaking,api-break` | severity=(BREAKING OR API_BREAK) | Both severity levels, any element |
| `--show-only types,enums,changed` | element=(types OR enums) AND action=changed | Type/enum modifications only |

Implementation: filter applied in reporter after `compare()` returns, before
formatting. Verdict is still computed on all changes (filter is display-only).
The `--show-only` flag does not affect exit codes.

### 4. Summary mode (`--stat`)

```bash
abicheck compare old.so new.so --stat
```

Output (text, not markdown):
```text
BREAKING: 3 breaking, 2 source-level breaks, 1 risk, 12 compatible (18 total)
```

For JSON: `--stat --format json` emits only the summary object, no changes array.

For CI gates: `--stat` + exit code is often all that's needed.

### 5. Impact summary (`--show-impact`)

Appends a summary section to the end of the report:

```markdown
## Impact Summary

| Root Change | Kind | Affected Interfaces |
|-------------|------|-------------------|
| struct Config | size_changed | 12 functions, 3 variables |
| enum ErrorCode | member_removed | 8 functions |
| — | func_removed (3) | direct |
```

Works in both `full` and `leaf` report modes. Uses existing `affected_symbols` data.

### 6. Changes to the `compare()` pipeline

Updated flow:

```text
compare(old, new, suppress, policy, ...)
  → [30 detectors]
  → _deduplicate_ast_dwarf(changes)        # existing
  → suppress.filter(changes)               # existing — applied to ALL changes first
  → _filter_redundant(unsuppressed)        # NEW — produces (kept, redundant)
  → _enrich_source_locations(kept)         # existing
  → _enrich_affected_symbols(kept)         # existing
  → compute_verdict(kept + redundant)      # verdict on unsuppressed changes only
  → DiffResult(changes=kept, redundant_changes=redundant, ...)
```

**Important**: Suppression is applied **before** the redundancy split. This ensures
that a suppressed change never contributes to the verdict — whether it would have
been classified as kept or redundant. Verdict is then computed on the full set of
unsuppressed changes (kept + redundant), so exit codes are correct regardless of
display mode but respect suppressions.

### 7. Model changes

```python
@dataclass
class Change:
    kind: ChangeKind
    symbol: str
    description: str
    old_value: str | None = None
    new_value: str | None = None
    source_location: str | None = None
    affected_symbols: list[str] | None = None
    # NEW
    caused_by_type: str | None = None    # root type that makes this change redundant
    caused_count: int = 0                # number of derived changes collapsed into this root


@dataclass
class DiffResult:
    # ... existing fields ...
    # NEW
    redundant_changes: list[Change] = field(default_factory=list)  # hidden by dedup
    redundant_count: int = 0
```

### 8. CLI summary

```bash
# Default: redundancy filtered, full report
abicheck compare old.so new.so

# Show everything (disable redundancy filter)
abicheck compare old.so new.so --show-redundant

# Leaf-change mode
abicheck compare old.so new.so --report-mode leaf

# Only breaking changes
abicheck compare old.so new.so --show-only breaking

# One-line summary for CI
abicheck compare old.so new.so --stat

# Impact table
abicheck compare old.so new.so --show-impact
```

All flags work with all output formats (markdown, json, sarif, html).

## Consequences

### Positive
- Reports become 5-50× shorter for real-world libraries with shared types
- `--stat` enables minimal CI gate output
- `--show-only` replaces ad-hoc `jq` post-processing
- Leaf-change mode gives developers the "what actually changed" view
- Verdict is always computed on full changes — exit codes unaffected by display options
- Redundant changes preserved in `DiffResult.redundant_changes` for audit

### Negative
- Redundancy heuristic may occasionally misclassify — `--show-redundant` is the escape hatch
- Multiple report modes increase formatter complexity
- `--show-only` token grammar needs documentation

## Implementation Plan

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | `_filter_redundant()` in checker.py + `--show-redundant` flag | 2-3 days |
| 2 | `caused_by_type`, `caused_count` on Change model | 1 day |
| 3 | `--show-only` filter in reporter.py | 1-2 days |
| 4 | `--stat` summary mode | 1 day |
| 5 | `--report-mode leaf` in to_markdown() + to_json() | 2-3 days |
| 6 | `--show-impact` summary section | 1-2 days |
| 7 | HTML + SARIF support for new modes | 1-2 days |
