# ADR-010: Policy Profile System

**Date:** 2026-03-18
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

> **2026-07 amendment (verified against current code):**
>
> - **"Single source of truth" needs a caveat.** `policy_kind_sets()` in
>   `checker_policy.py` remains the sole source of truth for the **verdict
>   axis** (BREAKING/API_BREAK/COMPATIBLE/RISK, which drives exit codes) — that
>   part still holds. But ADR-036 (report view-model) later formalized that
>   report *presentation* has two more, deliberately independent classification
>   axes that do NOT derive from `policy_kind_sets()`: display severity
>   (HIGH/MEDIUM/LOW, hardcoded in `report_classifications.py` as
>   `HIGH_SEVERITY_KINDS`/`MEDIUM_SEVERITY_KINDS` for ABICC-compat output) and
>   origin (rtti/internal/public, in `report_summary.py`). See ADR-036 for why
>   these are kept separate rather than collapsed into policy.
> - **Unknown-ChangeKind-slug handling was wrong.** §3 below claims "Unknown
>   ChangeKind slugs or severity values are rejected at load time." In
>   `policy_file.py`'s `_parse_overrides()`, unknown **severity** values do
>   raise `PolicyError` (as stated) — but unknown **ChangeKind slugs** are only
>   logged as a warning and silently skipped, not rejected.
> - **"Unknown policy names silently fall back to `strict_abi`" is stale for
>   the CLI.** `policy_kind_sets(policy)` itself still has that fallback
>   internally, but it's unreachable from the documented CLI surface today:
>   `--policy` is a `click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"])`
>   and `--policy-file` uses `PolicyFileParam`, both of which raise a clear
>   CLI error (`click.BadParameter`/usage error) on an unrecognized name rather
>   than falling back silently.

---

## Context

Different consumers of ABI compatibility results have different definitions of
"breaking." A distro maintainer upgrading glibc cares about every binary ABI
change. An SDK vendor shipping a new release cares about binary ABI but may
accept source-level renames. A plugin host rebuilt with its plugins from the
same toolchain can tolerate calling convention changes.

A single fixed classification does not serve all use cases. ABICC has no
policy system — every change is either "compatible" or "incompatible."

### Requirements

- Default must be strictest (fail-safe)
- Named profiles for common use cases
- Custom per-kind overrides via file
- Policy must be the single source of truth for kind → severity mapping
- Integrity constraints must be enforced at import time (not runtime)

---

## Decision

### 1. Three built-in policy profiles

| Profile | Use case | Behavior |
|---------|----------|----------|
| **`strict_abi`** (default) | Distro/system libraries | Maximum severity for all changes |
| **`sdk_vendor`** | SDK/library releases | Source-level-only API_BREAK kinds downgraded to COMPATIBLE |
| **`plugin_abi`** | Host/plugin rebuilt together | Calling-convention BREAKING kinds downgraded to COMPATIBLE; RISK_KINDS promoted to BREAKING |

### 2. Profile mechanics

Each profile produces four disjoint kind sets via `policy_kind_sets(policy)`:

```python
def policy_kind_sets(policy: str) -> tuple[
    frozenset[ChangeKind],  # breaking
    frozenset[ChangeKind],  # api_break
    frozenset[ChangeKind],  # compatible
    frozenset[ChangeKind],  # risk
]: ...
```

**`strict_abi`** (default):
```
breaking   = BREAKING_KINDS
api_break  = API_BREAK_KINDS
compatible = COMPATIBLE_KINDS
risk       = RISK_KINDS
```

**`sdk_vendor`**: Moves source-level-only kinds from API_BREAK to COMPATIBLE.
```
breaking   = BREAKING_KINDS                              (unchanged)
api_break  = API_BREAK_KINDS - SDK_VENDOR_COMPAT_KINDS   (narrower)
compatible = COMPATIBLE_KINDS | SDK_VENDOR_COMPAT_KINDS  (wider)
risk       = RISK_KINDS                                  (unchanged)
```

Downgraded kinds: `ENUM_MEMBER_RENAMED`, `FIELD_RENAMED`, `PARAM_RENAMED`,
`METHOD_ACCESS_CHANGED`, `FIELD_ACCESS_CHANGED`, `SOURCE_LEVEL_KIND_CHANGED`,
`REMOVED_CONST_OVERLOAD`, `PARAM_DEFAULT_VALUE_REMOVED`.

These are all source-level concerns — already-compiled binary consumers are
unaffected.

**`plugin_abi`**: Moves calling-convention kinds from BREAKING to COMPATIBLE,
AND promotes RISK_KINDS to BREAKING.
```
breaking   = (BREAKING_KINDS - PLUGIN_ABI_DOWNGRADED_KINDS) | RISK_KINDS
api_break  = API_BREAK_KINDS                                (unchanged)
compatible = COMPATIBLE_KINDS | PLUGIN_ABI_DOWNGRADED_KINDS (wider)
risk       = ∅                                              (empty)
```

Downgraded kinds: `CALLING_CONVENTION_CHANGED`, `FRAME_REGISTER_CHANGED`,
`VALUE_ABI_TRAIT_CHANGED`.

RISK promotion rationale: In a plugin scenario, the host and plugin load into
the same process. A deployment-floor risk (e.g., new GLIBC requirement) can
prevent the plugin from loading in the host environment. This is not merely a
deployment concern — it's a functional failure. Therefore RISK → BREAKING.

### 3. Custom policy files (YAML)

Users can override individual ChangeKind severities via `--policy-file`.
The file uses a two-key schema: `base_policy` (optional, defaults to
`strict_abi`) and `overrides` (mapping of lowercase `ChangeKind.value`
slugs to severity strings):

```yaml
base_policy: strict_abi          # optional; defaults to strict_abi

overrides:
  func_removed: break            # BREAKING
  func_added: ignore             # COMPATIBLE
  type_field_added: warn         # API_BREAK
  symbol_version_required_added: risk  # COMPATIBLE_WITH_RISK
```

Valid severity values: `break` (BREAKING), `warn` (API_BREAK), `risk`
(COMPATIBLE_WITH_RISK), `ignore` (COMPATIBLE).

The `overrides` map is applied on top of the base profile's kind sets.
This allows partial overrides without redefining the entire classification.
Unknown ChangeKind slugs or severity values are rejected at load time.

### 4. Integrity constraints

Import-time assertions enforce structural invariants:

```python
# Downgrade sets must be subsets of their source sets
assert SDK_VENDOR_COMPAT_KINDS <= API_BREAK_KINDS
assert PLUGIN_ABI_DOWNGRADED_KINDS <= BREAKING_KINDS

# RISK_KINDS must be disjoint from all other sets (explicit raises, not assert)
if not RISK_KINDS.isdisjoint(BREAKING_KINDS): raise AssertionError(...)
if not RISK_KINDS.isdisjoint(COMPATIBLE_KINDS): raise AssertionError(...)
if not RISK_KINDS.isdisjoint(API_BREAK_KINDS): raise AssertionError(...)
```

These constraints use `raise` (not `assert`) for safety-critical checks to
ensure they are never stripped by `python -O`.

### 5. Single source of truth

`policy_kind_sets()` in `checker_policy.py` is the canonical function for
resolving policy → kind-set mapping. All verdict computation, report
classification, and severity display goes through this function. No other
module duplicates the classification logic.

---

## Consequences

### Positive

- Default is strictest — new users get maximum protection
- Named profiles cover the three most common deployment scenarios
- Custom YAML allows project-specific overrides without forking
- Import-time assertions catch misclassification during development
- Single source of truth prevents classification drift between components

### Negative

- `plugin_abi` RISK → BREAKING promotion is non-obvious; requires documentation
- Three profiles may not cover all use cases (e.g., kernel ABI has different rules)
- Custom YAML schema is a user-facing contract that must be maintained
- Unknown policy names silently fall back to `strict_abi` (could surprise users)

---

## References

- `abicheck/checker_policy.py` — `policy_kind_sets()`, kind sets, integrity
  assertions
- `abicheck/policy_file.py` — Custom YAML policy parsing
- `abicheck/cli.py` — `--policy` and `--policy-file` CLI flags
