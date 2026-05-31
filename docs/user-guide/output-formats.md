# Output Formats

abicheck supports multiple output formats for different use cases:

| Format | Flag | Best for |
|--------|------|----------|
| Markdown | `--format markdown` (default) | Human review, PRs, terminals |
| JSON | `--format json` | CI pipelines, machine processing |
| SARIF | `--format sarif` | GitHub Code Scanning, SAST platforms |
| HTML | `--format html` | Standalone reports, ABICC migration |
| JUnit XML | `--format junit` | GitLab CI, Jenkins, Azure DevOps test dashboards |

All five formats support the report filtering options described below.
The ABICC-compatible XML output (via `abicheck compat check`) includes
redundancy annotations but does not support `--show-only` filtering.

In addition to report formats, abicheck can emit **GitHub Actions workflow
command annotations** (`--annotate`) that appear as inline comments on PR
diffs. See [GitHub PR Annotations](annotations.md) for details.

## Redundancy filtering

When a root type change (e.g. struct size change) causes many derived changes
(e.g. 30 `FUNC_PARAMS_CHANGED` entries for functions using that struct),
abicheck automatically collapses the derived changes. The root type change is
annotated with:

- `caused_count` — number of derived changes collapsed
- `affected_symbols` — list of affected interface names

This keeps reports focused on root causes. Use `--show-redundant` to disable
filtering and see all changes.

### How it appears in each format

**Markdown**: An info note at the bottom:
```
> ℹ️ 12 redundant change(s) hidden (derived from root type changes).
> Use `--show-redundant` to show all.
```

**JSON**: A top-level `redundant_count` field, and per-change `caused_by_type`
and `caused_count` annotations on root type changes.

**SARIF**: `caused_by_type` and `caused_count` in result `properties`;
`redundant_count` in run-level properties.

**HTML**: A highlighted banner showing the redundant count.

**XML (ABICC compat)**: `<redundant_changes>` element in `<problem_summary>`,
`<caused_by>` and `<caused_count>` elements on individual problems. Both binary
and source sections include their own redundant counts.

**JUnit XML**: Redundant changes are filtered upstream before the formatter
receives them, so derived changes do not appear as test cases. No
JUnit-specific redundancy metadata is emitted.

## Public-header surface scoping

Public-header surface scoping (ADR-024) restricts findings to the *public* ABI
surface — the symbols exported **and** declared in the public headers you
supplied, plus the types reachable from them. Changes that fall outside that
surface (e.g. a layout change to an internal struct no public API references)
are **not dropped**: they are moved to an audit ledger so the "why was this
excluded" trail stays inspectable. Internal-type leaks are never filtered.

Scoping is **on by default** (ADR-024 Phase 5). When no public-header surface
can be resolved — e.g. comparing two stripped `.so` files with no header or
DWARF provenance — scoping is automatically a no-op and every finding is
reported, so the default never hides anything it cannot place. Pass
`--no-scope-public-headers` to force the unscoped report (every finding,
regardless of surface).

Use `--show-filtered` to print the ledger on the terminal.

### Widening the surface (`--public-symbol`)

Some symbols you *do* guarantee as public can't be seen by header provenance —
hand-written asm stubs, `.def` exports, `extern "C"` shims, or symbols whose
MSVC mangling castxml can't match. The **widening overlay** (ADR-024 §D6) forces
such symbols back into the public surface so their changes are reported rather
than demoted:

```bash
# Force individual symbols (repeatable), à la abi-compliance-checker -symbols-list
abicheck compare old.so new.so --scope-public-headers \
    --public-symbol my_asm_stub --public-symbol _ZN3foo3barEv

# Or from a file (one symbol per line; '#' comments and blank lines ignored)
abicheck compare old.so new.so --scope-public-headers \
    --public-symbols-list public.syms
```

Matching is on the symbol as recorded on the finding (mangled or demangled),
plus the trailing `::` segment of a qualified name. Widening only ever *keeps* a
finding — it can never hide a break — and only takes effect together with
`--scope-public-headers`. It is the counterpart to suppression, which *narrows*
the surface; the two remain separate, auditable inputs.

### How it appears in each format

Each demoted finding carries a `reason` code explaining why it was excluded:

- `not-exported` — the symbol is known but not in the public export set.
- `non-public-type` — the type is reachable from no public API root.
- `private-header` — the declaration originates in a project header outside
  the public-header set.
- `system-header` — the declaration originates in a toolchain/system header
  (`/usr/include`, MSVC, Xcode SDK, …).

The `private-header` / `system-header` reasons are provenance-derived: they
only appear when the snapshots were produced with `--public-header` /
`--public-header-dir` (ADR-015 schema v6). Without a public-header set, every
declaration's origin is `unknown` and only the linkage/reachability reasons
above are emitted.

**Text**: With `--show-filtered`, an audit block on stderr (the reason is shown
in parentheses):
```text
Filtered as non-public ABI surface (1 finding, --scope-public-headers):
  - type_size_changed: InternalCache (non-public-type)
```

**JSON**: A top-level `surface_scope` object (present only when scoping is
active):
```json
"surface_scope": {
  "enabled": true,
  "out_of_surface_count": 1,
  "out_of_surface_changes": [
    {"kind": "type_size_changed", "symbol": "InternalCache",
     "description": "Size changed: InternalCache (64 → 128 bits)",
     "source_location": null, "reason": "non-public-type"}
  ]
}
```

**SARIF**: A `surfaceScope` object in run-level `properties` with
`outOfSurfaceCount` and `outOfSurfaceChanges` (same per-finding fields,
camelCased; `reason` included when known), present only when scoping is active.

## `--show-only` filter

Limit displayed changes by severity, element, or action (AND across dimensions,
OR within each). Does not affect the verdict or exit codes.

```bash
abicheck compare old.json new.json --show-only breaking,functions,removed
```

**Markdown / JSON / HTML**: Changes are filtered before rendering. A note shows
how many changes matched: `> Filtered by: --show-only ... (5 of 42 changes shown)`.

**SARIF**: The `show_only` parameter filters which results appear in the SARIF
output.

**JUnit XML**: The `show_only` parameter filters which test cases appear in the
output. Filtered-out changes are omitted entirely.

## `--stat` mode

One-line summary for CI gates:

```bash
$ abicheck compare old.json new.json --stat
BREAKING: 3 breaking, 1 risk (42 total) [12 redundant hidden]

$ abicheck compare old.json new.json --stat --format json
{"library": "libfoo", "verdict": "BREAKING", "summary": {...}}
```

## `--report-mode leaf`

Groups output by root type changes with affected interface lists, instead of
listing every change individually. Available in Markdown and JSON formats.

```bash
abicheck compare old.json new.json --report-mode leaf
```

## `--show-impact`

Appends an impact summary table to the report, showing root changes and how many
interfaces each affects. Available in Markdown and HTML formats.

```bash
abicheck compare old.json new.json --show-impact
```

---

## Analysis confidence and evidence tier

Every comparison reports how much evidence backed the verdict, so consumers can
calibrate trust. Three related fields appear in the Markdown "Analysis
Confidence" section and the JSON report:

| Field | Type | Meaning |
|-------|------|---------|
| `confidence` | `high` / `medium` / `low` | Overall trust level (does the available evidence corroborate the verdict, and were any detectors disabled). |
| `evidence_tier` | `elf_only` / `dwarf_aware` / `header_aware` | **Canonical, ordered analysis depth.** Key trust decisions off this scalar. |
| `evidence_tiers` | list of strings | Raw data sources that were available (`elf`, `dwarf`, `dwarf_advanced`, `header`, `pe`, `macho`). Retained for backward compatibility. |

The `evidence_tier` scalar collapses the raw sources into a single ordered label
(shallow → deep):

- **`elf_only`** — symbol-table-only. Binary export tables (ELF/PE/Mach-O) are
  present, but there is no DWARF debug info and no header/AST surface. Only
  symbol add/remove and version changes are observable; struct layout, enum
  values, and type changes are **not**.
- **`dwarf_aware`** — DWARF (or equivalent debug info) is present, enabling
  struct layout, enum, and calling-convention analysis, but no header/AST
  surface is available to cross-check declared API intent.
- **`header_aware`** — a parsed header/AST surface (functions/types/enums) is
  present. The richest tier, and the only one that can reason about
  declared-but-not-emitted API, inline/template changes, and macro contracts.

```json
{
  "verdict": "BREAKING",
  "confidence": "high",
  "evidence_tier": "header_aware",
  "evidence_tiers": ["elf", "dwarf", "header"]
}
```

---

## JSON schema and stability guarantees

The `compare --format json` document is a **stable, machine-readable contract**.
It is described by a versioned [JSON Schema](https://json-schema.org/) (draft
2020-12) that ships inside the package at
`abicheck/schemas/compare_report.schema.json` and is importable:

```python
from abicheck.schemas import (
    REPORT_SCHEMA_VERSION,        # e.g. "1.0"
    COMPARE_REPORT_SCHEMA_PATH,   # pathlib.Path to the .schema.json
    load_compare_report_schema,   # -> dict
)
```

Every JSON report carries a top-level `report_schema_version` field
(`MAJOR.MINOR`) so consumers can detect the contract version they are reading:

```json
{
  "report_schema_version": "1.0",
  "library": "libfoo.so.1",
  "verdict": "BREAKING"
}
```

**Stability policy:**

- **Additive** changes — new optional keys, new enum members, relaxing a
  constraint — bump the **MINOR** component. Existing consumers keep working.
- **Breaking** changes — removing or renaming a key, tightening a type, or
  removing an enum member — bump the **MAJOR** component.

Consumers should accept any report whose `report_schema_version` shares their
expected MAJOR component and **ignore unknown keys** (the schema sets
`additionalProperties: true` precisely so that MINOR additions never break
validation). Validating with the bundled schema requires the optional
`jsonschema` package:

```python
import json, jsonschema
from abicheck.schemas import load_compare_report_schema

report = json.loads(open("report.json").read())
jsonschema.validate(report, load_compare_report_schema())
```

---

## Release recommendation (`--recommend`)

Translates the verdict into the maintainer's actual question — *what version do
I release, and do I need to bump the SONAME?* — as a recommended semantic-version
bump (`major`/`minor`/`patch`/`none`) plus a SONAME action.

```bash
abicheck compare old.so new.so -H include/ --recommend
```

The recommendation is **policy-aware** (it honours `--policy` and
`--policy-file`):

| Verdict | Bump | SONAME |
|---------|------|--------|
| `NO_CHANGE` | none | no bump needed |
| `BREAKING` | major | bump required (or `bump_missing`/`bump_performed` if abicheck observed the soname) |
| `API_BREAK` | major | no bump needed (binary stays loadable) |
| `COMPATIBLE_WITH_RISK` | minor/patch | no bump needed |
| `COMPATIBLE` (additions) | minor | no bump needed |
| `COMPATIBLE` (quality only) | patch | no bump needed |

In **JSON** output the recommendation is always present (no flag needed) under
the `release_recommendation` key, so CI and agents can gate on it directly:

```bash
abicheck compare old.so new.so -H include/ --format json \
  | jq -r '.release_recommendation | "\(.version_bump) (\(.soname_action))"'
# major (bump_required)
```

---

## SARIF Output

abicheck supports [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/) output for integration with GitHub Code Scanning and other SAST platforms.

### Usage

```bash
abicheck compare old.json new.json --format sarif -o results.sarif
```

### GitHub Code Scanning integration

```yaml
# .github/workflows/abi-check.yml
name: ABI Check

on: [pull_request]

jobs:
  abi-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install abicheck
        run: |
          sudo apt-get update && sudo apt-get install -y castxml g++
          pip install abicheck

      - name: Dump ABI (baseline)
        run: |
          abicheck dump lib/libfoo.so.1 -H include/foo.h \
            --version ${{ github.base_ref }} -o old.json

      - name: Dump ABI (PR)
        run: |
          abicheck dump lib/libfoo.so.2 -H include/foo.h \
            --version ${{ github.head_ref }} -o new.json

      - name: Compare ABI
        run: |
          abicheck compare old.json new.json --format sarif -o abi.sarif
        continue-on-error: true

      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: abi.sarif
```

### Severity mapping

| ABI Change | SARIF Level |
|-----------|-------------|
| Function/variable removed | `error` |
| Type size/layout changed | `error` |
| Return/parameter type changed | `error` |
| Function/variable added | `warning` |

### SARIF document structure

```json
{
  "$schema": "https://raw.githubusercontent.com/.../sarif-schema-2.1.0.json",
  "version": "2.1.0",
  "runs": [{
    "tool": { "driver": { "name": "abicheck", "rules": [...] } },
    "results": [{
      "ruleId": "func_removed",
      "level": "error",
      "message": { "text": "Function foo() removed" },
      "locations": [{
        "physicalLocation": { "artifactLocation": { "uri": "libfoo.so.1" } },
        "logicalLocations": [{ "name": "_Z3foov" }]
      }],
      "properties": {
        "caused_by_type": null,
        "caused_count": 0
      }
    }]
  }]
}
```

---

## JUnit XML Output

abicheck can produce JUnit XML reports for CI systems that display test results
in their standard dashboards — GitLab CI, Jenkins, Azure DevOps, CircleCI, and
others.

### Usage

```bash
abicheck compare old.json new.json --format junit -o results.xml
abicheck compare-release release-1.0/ release-2.0/ --format junit -o abi-tests.xml
```

### How it works

ABI changes are mapped to JUnit test cases:

- Each **library** in a `compare-release` becomes a `<testsuite>`
- Each **exported symbol or type** that was checked becomes a `<testcase>`
- **BREAKING** and **API_BREAK** changes produce `<failure>` elements
- **COMPATIBLE** changes (additions, no-change) are passing test cases
- **COMPATIBLE_WITH_RISK** changes pass by default (unless their per-kind
  severity is overridden to `"error"`)
- Unchanged symbols from the old library also appear as passing test cases,
  so the pass-rate is meaningful
- When a symbol has multiple breaking changes, the `<testcase>` contains
  multiple `<failure>` children (one per change)

### Severity mapping

| ABI Verdict | JUnit Outcome |
|-------------|---------------|
| BREAKING | `<failure type="BREAKING">` |
| API_BREAK | `<failure type="API_BREAK">` |
| COMPATIBLE_WITH_RISK (severity=warning) | Pass |
| COMPATIBLE | Pass |

### Classname groups

Test cases are grouped by `classname` for CI dashboards that support
hierarchical display:

| Element | classname |
|---------|-----------|
| Functions | `functions` |
| Variables | `variables` |
| Types (struct/class/union) | `types` |
| Enums | `enums` |
| ELF metadata (SONAME, etc.) | `metadata` |

### JUnit XML structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="abicheck" tests="47" failures="3" errors="0">
  <testsuite name="libfoo.so.1" tests="47" failures="3" errors="0">
    <!-- Passing: no ABI change detected -->
    <testcase name="_ZN3foo3barEv" classname="functions" />

    <!-- Failure: binary-incompatible change -->
    <testcase name="_ZN3foo3bazEi" classname="functions">
      <failure message="func_param_type_changed: parameter 1 type changed from int to long"
               type="BREAKING">
parameter 1 type changed from int to long
(int → long)
Source: include/foo.h:42
      </failure>
    </testcase>

    <!-- Failure: removed symbol -->
    <testcase name="_ZN3foo6legacyEv" classname="functions">
      <failure message="func_removed: Function foo::legacy() was removed"
               type="BREAKING">
Function foo::legacy() was removed
      </failure>
    </testcase>

    <!-- Passing: addition is compatible -->
    <testcase name="_ZN3foo9new_thingEv" classname="functions" />
  </testsuite>
</testsuites>
```

### CI integration examples

#### GitLab CI

```yaml
abi-check:
  script:
    - abicheck compare old.so new.so -H include/ --format junit -o abi-results.xml || true
  artifacts:
    when: always
    reports:
      junit: abi-results.xml
```

#### Jenkins (JUnit plugin)

```groovy
stage('ABI Check') {
    steps {
        sh 'abicheck compare old.so new.so -H include/ --format junit -o abi-results.xml'
    }
    post {
        always {
            junit 'abi-results.xml'
        }
    }
}
```

#### Azure DevOps

```yaml
- task: CmdLine@2
  inputs:
    script: |
      abicheck compare old.so new.so -H include/ --format junit -o abi-results.xml
  continueOnError: true

- task: PublishTestResults@2
  inputs:
    testResultsFiles: 'abi-results.xml'
    testResultsFormat: 'JUnit'
```
