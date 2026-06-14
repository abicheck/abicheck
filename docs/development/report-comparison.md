# Report Content & Quality Comparison: abicheck vs ABICC vs abidiff

> **Status:** Historical comparison, content reviewed 2026-06-07. The examples
> and side-by-side excerpts remain useful for report-quality context, but the
> priority list is no longer the active roadmap; current reporting behavior is
> documented in the user guide and enforced by report/schema tests.

Focused comparison of **what information** each tool puts in its reports,
how useful that information is, and what gaps exist — across all output formats.

**Date:** 2026-03-14 (reviewed 2026-06-07)
**Test suite:** historical 74-case benchmark subset (examples/case01..case73 + case26b)

---

## 1. Report Formats Available

| Tool       | Text/MD | JSON | HTML | SARIF | XML |
|------------|---------|------|------|-------|-----|
| abicheck   | Markdown | Yes | Yes (2 styles) | Yes (2.1.0) | No |
| ABICC      | Log text | No  | Yes (primary) | No | No (dumps only) |
| abidiff    | Plain text | No | No | No | ABI corpus (abixml) |

**Gap in abicheck:** No native XML output for interop with legacy tools.
**Gap in ABICC:** No machine-readable structured output. HTML is the only detailed format.
**Gap in abidiff:** Only plain text. No structured output at all.

---

## 2. Information Content Per Change — Side by Side

### What each tool reports for a single change (case48: struct Leaf gains field z)

#### abicheck (JSON)
```json
{
  "kind": "type_size_changed",
  "symbol": "Leaf",
  "description": "Size changed: Leaf (32 → 64 bits)",
  "old_value": "32",
  "new_value": "64"
}
```

#### abicheck (Markdown)
```
- **type_size_changed**: Size changed: Leaf (32 → 64 bits) (`32` → `64`)
```

#### abicheck (HTML)
```
| type_size_changed | Leaf | Types | Size changed: Leaf (32 → 64 bits) | 32 | 64 |
```

#### abicheck (SARIF)
```json
{
  "ruleId": "type_size_changed",
  "level": "error",
  "message": {"text": "Size changed: Leaf (32 → 64 bits) (32 → 64)"},
  "locations": [{"logicalLocations": [{"name": "Leaf", "kind": "member"}]}],
  "properties": {"symbol": "Leaf", "oldVersion": "v1", "newVersion": "v2"}
}
```

#### abidiff
```
in pointed to type 'struct Point' at v2.c:2:1:
  type size changed from 64 to 96 (in bits)
  1 data member insertion:
    'int z', at offset 64 (in bits) at v2.c:2:1
```

#### ABICC (HTML)
```
struct Point — 2 changes:
  1. Field z has been added to this type.
     This field will not be initialized by old clients.
     Size of the inclusive type has been changed.
  2. Size of this type has been changed from 8 bytes to 12 bytes.
     The fields or parameters of such data type may be incorrectly
     initialized or accessed by old client applications.

  affected symbols: 2 (100%)
    get_x(Point* p) — 1st parameter 'p' has base type 'struct Point'.
    init_point(Point* p) — 1st parameter 'p' has base type 'struct Point'.
```

---

## 3. What Information Each Tool Provides (and Doesn't)

### Per-change data fields

| Information                     | abicheck | ABICC | abidiff |
|---------------------------------|----------|-------|---------|
| Change kind/type classification | `type_size_changed` (machine-parseable enum of 250 change kinds) | Narrative text only | Narrative text |
| Affected symbol/type name       | Yes (`"symbol": "Leaf"`) | Yes (in HTML sections) | Yes (as context for function) |
| Old value                       | Yes (`"old_value": "32"`) | Yes (in text) | Yes (in text) |
| New value                       | Yes (`"new_value": "64"`) | Yes (in text) | Yes (in text) |
| Human description               | Yes (`"description": "..."`) | Yes (verbose, with impact explanation) | Yes (terse) |
| **Impact explanation**          | Yes (`"impact": "..."` — per-kind human-readable text in all formats) | Yes ("may result in crash or incorrect behavior") | Partial ("note that this is an ABI incompatible change") |
| **Affected functions list**     | Yes (`"affected_symbols": [...]` — exported functions using the type) | Yes (lists all functions whose signatures use the type) | Implicit (report is function-centric) |
| **Source file location**        | Yes (`"source_location": "header.h:42"` — in SARIF, Markdown, HTML) | Yes (header file name) | Yes (`at v2.c:2:1`) |
| **Severity level**              | Implicit via kind (BREAKING/API_BREAK/COMPATIBLE) | Yes (High/Medium/Low) | No |
| **Detector/layer that found it** | Yes (`detectors` array in JSON) | No | No |

### Report-level metadata

| Information                     | abicheck | ABICC | abidiff |
|---------------------------------|----------|-------|---------|
| Library name                    | Yes | Yes | No |
| Version labels                  | Yes | Yes (if provided) | No |
| Overall verdict                 | Yes (NO_CHANGE/COMPATIBLE/API_BREAK/BREAKING) | Binary % + Source % | Counts only |
| Binary compatibility %          | Yes (in JSON and HTML) | Yes (primary metric) | No |
| Total symbol count (old lib)    | In HTML (if available) | Yes | No |
| Affected symbol %               | Yes | Yes | No |
| Change counts by severity       | In HTML summary table | Yes (High/Medium/Low) | Yes (Removed/Changed/Added) |
| Change counts by category       | In HTML (Functions/Variables/Types/Enums/ELF/DWARF) | Yes (Types/Symbols/Constants) | Yes (Functions/Variables) |
| Suppressed changes              | Yes (list + count) | No | Yes (suppression specs supported) |
| Policy/profile used             | In JSON (`"policy": "strict_abi"`) | No | No |
| Detector results breakdown      | Yes (JSON: per-detector change count + enabled + coverage_gap) | No | No |
| **Library file path**           | Yes (all formats, `old_file`/`new_file`) | No | No |
| **Library SHA-256**             | Yes (all formats) | No | No |
| **Library file size**           | Yes (all formats) | No | No |
| Tool version                    | In SARIF | Yes | Yes (via --version) |
| Architecture                    | No | Yes (x86_64, shown in report) | No |
| Compiler used for analysis      | No | Yes (GCC version) | No |
| Generation timestamp            | No | No | No |

---

## 4. Detailed Gap Analysis

### 4.1 What ABICC reports that we DON'T

#### (A) Impact Explanations — HIGH VALUE, WE MISS THIS
ABICC provides human-readable impact text for each change:
```
Field z has been added to this type.
1) This field will not be initialized by old clients.
2) Size of the inclusive type has been changed.
NOTE: this field should be accessed only from the new library functions,
      otherwise it may result in crash or incorrect behavior of applications.
```

Our report just says: `Field added: Leaf::z`. No explanation of *why* this matters
or *what could go wrong*. For someone unfamiliar with ABI breaks, our report is
much less actionable.

**Recommendation:** Add an `impact` or `explanation` field to each Change, with
a short sentence explaining the consequence. E.g., "Old callers may pass wrong
struct size to `sizeof(Leaf)`, causing buffer overruns."

#### (B) Affected Functions List — HIGH VALUE, WE MISS THIS
ABICC shows which exported functions are affected by each type change:
```
affected symbols: 2 (100%)
  get_x(Point* p) — 1st parameter 'p' has base type 'struct Point'.
  init_point(Point* p) — 1st parameter 'p' has base type 'struct Point'.
```

Our report says `type_size_changed: Leaf` but doesn't tell you which API
functions become dangerous to call. This is crucial for triage: if a changed
struct is only used by deprecated functions, the urgency is different.

**Recommendation:** For type/enum/struct changes, compute and include the list
of exported functions that use this type (as parameter, return, or member).

#### (C) Source File Locations — MEDIUM VALUE
ABICC references the header file: `v1.h`. abidiff references exact location: `at v2.c:2:1`.
Our reports don't include any source location.

We have `source_location` in our `Function` and `RecordType` models but never
include it in the report output.

**Recommendation:** Add `source_location` to Change or include it in the
description when available.

#### (D) Architecture / Compiler Info — LOW VALUE
ABICC includes `Arch: x86_64` and `GCC Version: 13`. We don't.
This is useful for reproducibility but not critical for actionability.

**Recommendation:** Optional metadata section.

#### (E) Three-level Severity (High/Medium/Low) — MEDIUM VALUE
ABICC classifies changes as High/Medium/Low severity. We only have
BREAKING/API_BREAK/COMPATIBLE (3 categories, but no sub-severity within BREAKING).

Example: ABICC distinguishes "virtual method order changed" (High) from
"type size changed in unused type" (Low). We treat both as BREAKING.

Our HTML already uses severity internally (`report_classifications.py`) for
the compat-HTML mode, but the native HTML/JSON/Markdown don't expose it.

**Recommendation:** Expose severity (High/Medium/Low) in JSON and Markdown too.

### 4.2 What abidiff reports that we DON'T

#### (A) Change Propagation Path — MEDIUM VALUE
abidiff shows the full type nesting chain:
```
parameter 1 of type 'const Container*' has sub-type changes:
  in pointed to type 'const Container':
    in unqualified underlying type 'typedef Container':
      underlying type 'struct Container' changed:
        type size changed from 96 to 128 (in bits)
        2 data member changes:
          type of 'Leaf position' changed:
            underlying type 'struct Leaf' changed:
              type size changed from 32 to 64 (in bits)
```

Our report lists the same facts but as flat items, not as a tree. You can see
that both Leaf and Container changed, but not the causal chain
(Leaf grew → Container grew → Container::flags offset shifted).

**Recommendation:** Consider adding an optional `cause` or `related_changes`
field to group cascading changes.

#### (B) Vtable Offset Details — MEDIUM VALUE
abidiff reports exact vtable slot numbers:
```
the vtable offset of method Widget::resize() changed from 1 to 2
note that this is an ABI incompatible change to the vtable of class Widget
```

We report `symbol_size_changed: _ZTV6Widget (32 → 40 bytes)` — which is
correct but less specific. We detect the break via ELF symbol size, not
via individual vtable slot analysis.

**Recommendation:** If RecordType.vtable is populated, could report per-slot changes.

#### (C) Demangled Function Signatures — LOW-MEDIUM VALUE
abidiff shows full demangled signatures: `'method virtual int Widget::draw()'`.
Our reports use the mangled name or bare type name.

Our HTML does have demangled-as-display + mangled-as-tooltip support
(`_symbol_cell` in html_report.py), but JSON/Markdown don't include a
`demangled` field.

### 4.3 What WE report that others DON'T

| Our unique info | Value |
|----------------|-------|
| **Detector breakdown** (JSON: which analysis layer found what) | HIGH — unique to us; lets user know if a finding is from ELF, AST, or DWARF |
| **Policy profile** (strict_abi/sdk_vendor/plugin_abi) | HIGH — no other tool has configurable severity policies |
| **Suppression audit trail** (list of suppressed changes) | HIGH — ABICC has no suppression; abidiff supports suppression but doesn't list what was suppressed |
| **SARIF output** for GitHub Code Scanning | HIGH — neither ABICC nor abidiff produce SARIF |
| **5-level verdict** (NO_CHANGE/COMPATIBLE/COMPATIBLE_WITH_RISK/API_BREAK/BREAKING) | MEDIUM — ABICC can't distinguish NO_CHANGE from COMPATIBLE; abidiff has no verdict |
| **Binary compatibility %** in JSON | MEDIUM — ABICC has it in HTML only; abidiff doesn't have it |
| **250-kind change taxonomy** | MEDIUM — machine-parseable enum vs free-text descriptions |

---

## 5. Report Noise Analysis — What We Over-report

### 5.1 Duplicate Findings from Multiple Detectors

Case 48 reports 8 breaking changes for what is conceptually 2 changes
(Leaf gained a field + Container layout shifted). The duplication:

```
AST layer:     type_size_changed: Leaf (32 → 64 bits)
               type_alignment_changed: Leaf (16 → 32 bits)
               type_size_changed: Container (96 → 128 bits)
               type_field_offset_changed: Container::flags (64 → 96 bits)
DWARF layer:   struct_size_changed: Leaf (4 → 8 bytes)          ← repeats AST finding
               struct_size_changed: Container (12 → 16 bytes)   ← repeats AST finding
               struct_field_type_changed: Container::position    ← repeats AST finding
               struct_field_offset_changed: Container::flags     ← repeats AST finding
```

Compare ABICC for the same case: reports 4 findings (no duplication).
Compare abidiff: reports 1 structured finding (most concise).

**Impact:** Makes the report look scarier than necessary. A user sees "8 breaking
changes" and panics, when it's really 2 distinct issues.

**Recommendation:** Either (a) deduplicate by grouping AST+DWARF findings about
the same symbol, or (b) add a `"unique_issues"` count to the summary, or
(c) mark DWARF findings as "confirming" the AST findings.

### 5.2 Case 19: Duplicate Enum Finding

```
- enum_member_removed: Status::FOO (2)
- enum_member_removed: Status::FOO (2)    ← exact duplicate
```

Same change reported twice by different detectors. Pure noise.

### 5.3 JSON Detector List Bloat

Our JSON includes ALL 28 detectors even when 25 of them report 0 changes:
```json
{"name": "functions", "changes_count": 0, "enabled": true, "coverage_gap": null},
{"name": "variables", "changes_count": 0, "enabled": true, "coverage_gap": null},
{"name": "enums", "changes_count": 0, "enabled": true, "coverage_gap": null},
... (25 more zero-count entries)
```

For a simple struct change, 90% of the JSON is zero-count detector entries.

**Recommendation:** Only include detectors with `changes_count > 0` by default,
or add `--verbose` flag to include all.

### 5.4 Markdown Legend Bloat

Every markdown report includes a 7-line legend explaining all 4 verdict levels.
For CI output where people see hundreds of reports, this is wasted space.

**Recommendation:** Add `--no-legend` option, or omit when piping to a file.

---

## 6. SARIF Quality Issues

### 6.1 Location Information

Our SARIF results point to the `.so` file, not to source:
```json
"physicalLocation": {
  "artifactLocation": {"uri": "libv1.so", "uriBaseId": "%SRCROOT%"}
}
```

GitHub Code Scanning uses physical locations to annotate source files. With our
current output, findings appear as file-level annotations on the .so file, which
isn't useful. If we had header file + line info, findings could be shown inline
in PRs.

**Recommendation:** Include source_location (header:line) as the physicalLocation
when available, and the .so as a secondary/related location.

### 6.2 Exit Code Mapping

```python
"exitCode": 0 if result.verdict == Verdict.NO_CHANGE else (
    1 if result.verdict == Verdict.BREAKING else 0
),
```

This maps COMPATIBLE and API_BREAK both to exit code 0, which is correct.
But BREAKING maps to exit code 1 while `exitCodeDescription` says "BREAKING".
Our CLI uses exit code 4 for BREAKING — inconsistency.

### 6.3 Missing old_value/new_value in SARIF Properties

Our SARIF `properties` include `symbol`, `oldVersion`, `newVersion` but not
`old_value`/`new_value`. These would be useful for automated analysis.

---

## 7. HTML Report Quality Issues

### 7.1 Comparison: Our HTML vs ABICC HTML

| Feature | abicheck HTML | ABICC HTML |
|---------|--------------|------------|
| Visual design | Modern, clean (Material Design palette) | Dated but functional |
| Verdict banner | Clear colored banner with icon | Percentage display |
| Navigation | Jump links (Removed/Changed/Added) | Table of contents with expand/collapse |
| Change details | Flat table (Kind/Symbol/Category/Description/Old/New) | Grouped by type, with nested affected symbols |
| **Expand/collapse** | **No** | **Yes** (per-type, per-function) |
| Severity grouping | By Removed/Changed/Added | By High/Medium/Low severity |
| Affected symbols | Not shown | Shown per type change |
| Type hierarchy | Flat list | Grouped (struct → fields → affected functions) |
| Search/filter | No | No |
| Responsive design | Yes (viewport meta) | No |
| Self-contained | Yes | Yes |

**Key gap:** ABICC groups changes by affected type and shows them hierarchically
with expand/collapse. Our HTML is a flat table, which works for small reports
but becomes hard to navigate with 20+ changes.

### 7.2 struct_field_type_changed Shows Same Type Name

```
Field type changed: Container::position Leaf(4B) → Leaf(8B) (Leaf → Leaf)
```

The old_value/new_value are both "Leaf" — the size changed, not the type name.
This is confusing in HTML where old/new columns show identical text.

---

## 8. Historical priority improvements

### HIGH priority from the March 2026 review

1. **Impact explanations per change** — ABICC's biggest advantage over us.
   Add a `reason` or `impact` field: "Old callers allocate 8 bytes but struct
   now needs 12; heap/stack corruption possible."

2. **Affected functions list** — For type/struct/enum changes, list which
   exported API functions use the affected type. Critical for triage.

3. **Deduplicate AST+DWARF findings** — 8 changes that are really 2 distinct
   issues is misleading. Group or deduplicate.

4. **SARIF source locations** — Use header file:line instead of .so path so
   GitHub Code Scanning shows findings inline in PRs.

### MEDIUM priority from the March 2026 review

5. **Source file locations in all formats** — We have the data (`source_location`
   on Function/RecordType) but don't emit it.

6. **Severity sub-levels** (High/Medium/Low) in JSON/Markdown — Our
   `report_classifications.py` already computes this but only for compat-HTML.

7. **JSON detector list** — Only include non-zero detectors by default.

8. **Change propagation chains** — Group Leaf→Container→flags as related.

### LOW priority from the March 2026 review

9. Architecture/compiler info in reports.
10. Generation timestamp.
11. `--no-legend` for Markdown.
12. Expand/collapse in HTML for large reports.
13. Demangled names in JSON output.


> Review note (2026-06-07): many items above have since been implemented or
> superseded by the report schema, impact text, affected-symbol propagation,
> filtering/deduplication, SARIF/JUnit/HTML coverage, and structural report tests.
> Treat this section as historical context rather than the canonical backlog.
