# ADR-013: Suppression System Design

**Date:** 2026-03-18
**Status:** Accepted — implemented. **Amendment:** the detector count and
regex-safety framing below have drifted from the current implementation —
see the notes inline.
**Decision maker:** Nikolay Petrov

---

## Context

Real-world ABI analysis produces findings that are technically correct but
operationally irrelevant: internal symbols exposed by visibility leaks,
deprecated APIs intentionally removed, known-safe type changes. Users need a
mechanism to suppress specific findings without disabling entire detectors.

### Requirements

- Suppress by symbol name (exact or pattern)
- Suppress by type name (for type-level changes only)
- Suppress by change kind
- Support temporary suppressions (expiry dates)
- Maintain an audit trail of suppressed changes
- Support ABICC skip/whitelist format for migration (Goal 1)
- Resist regex-based denial of service (ReDoS)

### Options considered

| Option | Description | Trade-off |
|--------|-------------|-----------|
| A: JSON suppression files | Structured, schema-validated | Verbose for humans |
| **B: YAML suppression files** | Human-readable, concise | YAML parsing pitfalls (Norway problem, anchors) |
| C: Inline comments in source | Co-located with code | Requires source access; doesn't work for binary-only analysis |

---

## Decision

### YAML format

```yaml
suppressions:
  - symbol: _ZN3foo3barEv
    reason: "Internal API — not part of public contract"

  - symbol_pattern: "_ZN3foo.*Internal.*"
    change_kind: func_removed
    reason: "Entire internal namespace being cleaned up"
    expires: "2026-06-01"

  - type_pattern: ".*_internal_t"
    reason: "Internal types — layout changes are expected"

  - source_location: "include/internal/*.h"
    reason: "All changes in internal headers are suppressed"
    label: "internal"
```

YAML was chosen over JSON for human readability. PyYAML is already a
dependency (used for policy files). The `defusedxml` security approach is
applied to YAML: `yaml.safe_load()` only — no arbitrary Python object
construction.

### Suppression rule model

```python
@dataclass
class SuppressionRule:
    symbol: str | None           # Exact symbol match
    symbol_pattern: str | None   # Regex (fullmatch semantics)
    type_pattern: str | None     # Regex for type-level changes only
    change_kind: str | None      # Filter by ChangeKind value
    reason: str | None           # Documentation
    label: str | None            # Grouping tag (e.g., "workaround")
    source_location: str | None  # fnmatch glob against source path
    expires: date | None         # ISO 8601 date — inactive after expiry
```

### Matching semantics

**Selector exclusivity**: Exactly one of `symbol`, `symbol_pattern`,
`type_pattern`, or `source_location` must be specified per rule. This is
validated at load time — malformed rules produce immediate errors, not silent
no-ops.

**Fullmatch semantics**: Pattern matching uses `re.fullmatch()` — the pattern
must match the entire symbol name, not a substring. This prevents
over-suppression from partial matches.

**Type pattern scoping**: `type_pattern` only matches changes whose
`ChangeKind` is in the `_TYPE_CHANGE_KINDS` set. This prevents a type
whitelist from accidentally suppressing symbol-level changes on identically
named symbols.

**Source location matching**: `source_location` uses `fnmatch` glob syntax
(not regex). The match is against the file path portion of
`change.source_location` (strips `:line[:col]` suffix).

**Conjunctive matching**: When `change_kind` is specified alongside a selector,
both must match. The change kind narrows the selector — it does not act as an
independent filter.

### Regex safety

Python's `re` module is used with `re.compile()` for pattern compilation.
Patterns are compiled eagerly at rule load time — malformed patterns produce
immediate errors. Matching uses `fullmatch()` which applies the pattern to the
complete string.

The implementation uses Python's standard backtracking `re` library — not an
RE2-style engine with a guaranteed linear-time bound. `abicheck/suppression.py`
has no complexity check or evaluation timeout on compiled patterns as of this
writing; the "resist ReDoS" requirement above is a partial mitigation (eager
`re.compile()` at load time surfaces malformed patterns immediately, and
`fullmatch()` keeps typical symbol-name patterns simple), not an absolute
guarantee. Complex patterns with pathological backtracking remain possible in
theory. For production deployments processing untrusted suppression files,
validating pattern complexity (or wrapping matching with a timeout) at load
time is still an open hardening item, not something currently implemented.

### Expiry mechanism

```python
def is_expired(self, today: date | None = None) -> bool:
    if self.expires is None:
        return False
    return (today or date.today()) > self.expires
```

- Expired rules never match — they are silently skipped during filtering
- `expired_rules()` method returns a list of expired rules for warning
  generation
- YAML loader handles both ISO 8601 strings (`"2026-06-01"`) and native
  YAML date values
- `datetime` objects are converted to `date` to avoid `TypeError` in
  comparison

### Pipeline ordering

Suppression is applied at a specific point in the `compare()` pipeline:

```text
[detector suite — 56 registered detectors as of this writing, see
 detector_registry.registry]
  → _deduplicate_ast_dwarf(changes)     # AST↔DWARF dedup
  → suppress.filter(changes)            # ← Suppression applied here
  → _filter_redundant(unsuppressed)     # Redundancy filtering (ADR-004)
  → _enrich_affected_symbols(kept)      # Symbol enrichment
  → compute_verdict(kept + redundant)   # Verdict on unsuppressed only
```

**Critical design choice**: Suppression runs before redundancy filtering.
This ensures that a suppressed change never contributes to the verdict —
whether it would have been classified as a root change or a redundant
derived change. See ADR-004 for the complete pipeline design including
redundancy filtering and leaf-change mode.

### Audit trail

Suppressed changes are preserved in `DiffResult.suppressed_changes`:

```python
@dataclass
class DiffResult:
    changes: list[Change]
    suppressed_changes: list[Change]  # filtered by suppression
    suppressed_count: int
```

Reports include a suppression summary section showing how many changes were
suppressed and which rules matched. This ensures suppressions are visible
and auditable.

### ABICC format support

For ABICC migration (ADR-012), the compat layer converts ABICC skip/whitelist
files to native `SuppressionRule` objects:

- `-skip-symbols` plain-text file → `SuppressionRule(symbol=...)` or
  `SuppressionRule(symbol_pattern=...)` depending on regex character detection
- `-skip-types` plain-text file → `SuppressionRule(symbol=..., change_kind=...)`
  scoped to type-level changes
- Unmangled C function names get an automatic Itanium mangling pattern
  fallback: `_Z\d+{name}.*`

### Validation

- Unknown keys in suppression entries are rejected (not silently ignored)
- `change_kind` values are validated against `_VALID_CHANGE_KINDS` frozenset
- Missing required selector (none of symbol/pattern/type_pattern/source_location)
  produces an error at load time

Example error for malformed rules:

```text
# Invalid: no selector specified
- change_kind: func_removed
  reason: "example"
→ Error: suppression rule 1: must specify exactly one of symbol,
  symbol_pattern, type_pattern, or source_location

# Invalid: multiple selectors
- symbol: _ZN3foo
  symbol_pattern: "_ZN3foo.*"
→ Error: suppression rule 2: only one of symbol/symbol_pattern/
  type_pattern/source_location allowed
```

---

## Consequences

### Positive

- Fine-grained control over which findings appear in reports
- Expiry dates prevent stale suppressions from hiding real regressions
- Audit trail ensures suppressions are visible and reviewable
- ABICC skip file compatibility enables smooth migration
- Pipeline ordering guarantees suppressions affect verdicts correctly

### Negative

- YAML has well-known pitfalls (Norway problem, implicit type coercion) —
  mitigated by `safe_load()` and explicit validation
- Two suppression formats (YAML + ABICC text) adds maintenance burden
- `fullmatch` semantics may surprise users expecting substring matching
- Regex patterns in YAML require quoting to avoid YAML syntax conflicts

---

## References

- `abicheck/suppression.py` — `SuppressionRule`, `SuppressionList`, matching
  logic
- `abicheck/compat/cli.py` — ABICC skip list conversion
- `abicheck/checker.py` — Pipeline ordering (suppression before redundancy)
- ADR-004 — Report filtering and deduplication (redundancy filtering stage)
