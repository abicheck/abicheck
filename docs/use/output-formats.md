---
doc_type: reference
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - output-formats
lifecycle: active
generated: false
---

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

In addition to report formats, the composite GitHub Action can emit
**GitHub Actions workflow command annotations** (`annotate: true`) that
appear as inline comments on PR diffs, rendered from the persisted
`annotations` report field. See [GitHub PR Annotations](annotations.md) for
details.

## Redundancy filtering

When a root type change (e.g. struct size change) causes many derived changes
(e.g. 30 `FUNC_PARAMS_CHANGED` entries for functions using that struct),
abicheck automatically collapses the derived changes. The root type change is
annotated with:

- `caused_count` — number of derived changes collapsed
- `affected_symbols` — list of affected interface names

This keeps reports focused on root causes. Use `scope.show_redundant: true` to disable
filtering and see all changes.

### How it appears in each format

**Markdown**: An info note at the bottom:
```
> ℹ️ 12 redundant change(s) hidden (derived from root type changes).
> Use `scope.show_redundant: true` to show all.
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

### Widening the surface (`scope.public_symbols`)

Some symbols you *do* guarantee as public can't be seen by header provenance —
hand-written asm stubs, `.def` exports, `extern "C"` shims, or symbols whose
MSVC mangling castxml can't match. The **widening overlay** (ADR-024 §D6) forces
such symbols back into the public surface so their changes are reported rather
than demoted:

```bash
# Force individual symbols (repeatable), à la abi-compliance-checker -symbols-list
abicheck compare old.so new.so --scope-public-headers \
    scope.public_symbols my_asm_stub scope.public_symbols _ZN3foo3barEv

# Or from a file (one symbol per line; '#' comments and blank lines ignored)
abicheck compare old.so new.so --scope-public-headers \
    scope.public_symbols public.syms
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
- `no-provenance` — a type demoted by reachability while provenance *was*
  available for the snapshot but not for this type, so the demotion is
  reachability-based rather than provenance-confirmed (reduced confidence).

The `private-header` / `system-header` reasons are provenance-derived: they
only appear when the snapshots were produced with a `-H`/`--header`
public-header set (ADR-015) -- `dump` derives provenance from it directly, and
`scan` additionally accepts `--public-header-dir`. Provenance is supported for
ELF, PE (provenance from PDB `LF_UDT_SRC_LINE`), and Mach-O inputs. Without a
public-header set, every declaration's origin is `unknown` and only the
linkage/reachability reasons above are emitted.

### Scope-resolution confidence

The ledger also carries a structured **confidence** in the surface resolution
itself (ADR-024 §D5.3), distinct from the overall verdict confidence:

- `confidence`: `"high"` (a clean header-scoped run) or `"reduced"`.
- `notes`: structured codes explaining any reduction —
  `mangling-fallback` / `header-backend-unavailable` (header scoping was requested on a
  PE/Mach-O binary but fell back to the export table; recorded on the snapshot
  as `scope_fallback`), or `no-provenance` (the surface resolved without any
  declaration provenance).

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
  "confidence": "high",
  "notes": [],
  "out_of_surface_count": 1,
  "out_of_surface_changes": [
    {"kind": "type_size_changed", "symbol": "InternalCache",
     "description": "Size changed: InternalCache (64 → 128 bits)",
     "source_location": null, "reason": "non-public-type"}
  ]
}
```

**SARIF**: A `surfaceScope` object in run-level `properties` with
`confidence`, `notes`, `outOfSurfaceCount`, and `outOfSurfaceChanges` (same
per-finding fields, camelCased; `reason` included when known), present only
when scoping is active.

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

## One-line summary (`--profile quick`)

`--stat` was removed (CLI cleanup phase two, PR 1). For a compact one-line
summary in a CI log, use the built-in `quick` profile instead:

```bash
$ abicheck compare old.json new.json --profile quick
BREAKING: 3 breaking, 1 risk (42 total) [12 redundant hidden]
```

For a machine-readable summary, use plain `--format json` and read the
`summary` object — it is already present in the full JSON report alongside
`changes`, so there is no separate summary-only shape to ask for:

```bash
$ abicheck compare old.json new.json --format json
{"library": "libfoo", "verdict": "BREAKING", "summary": {...}, "changes": [...]}
```

An explicit `--format` on the command line always overrides `--profile
quick`'s own one-line default (`--profile quick --format json` returns the
full JSON report above, not the one-line text) — the profile only supplies a
default when nothing else asked for a specific format.

## `--report-mode leaf`

Groups output by root type changes with affected interface lists, instead of
listing every change individually. Available in Markdown and JSON formats.

```bash
abicheck compare old.json new.json --report-mode leaf
```

## `--report-mode root-cause`

Groups findings that share a root cause under one entry, instead of listing
every change individually — e.g. an internal helper's `func_removed` finding
and the `internal_symbol_required_by_public_api` overlay finding that names
it both land in the same group. Supported for `--format json`/`markdown`
(the default rendered text output), and `sarif` (as additive `properties`,
see below); `junit` still renders as `full` (no testsuite grouping
equivalent yet — JUnit's `<testcase>` model already groups by symbol, not
by finding). This is a
first slice reusing the existing `Change.caused_by_type` field (see
[ADR-052](../contribute/adr/052-unified-impact-assessment-model.md));
a future slice (G29 Phase 6) will additionally correlate consumer-overlay
findings that don't share a `caused_by_type` today.

```bash
abicheck compare old.json new.json --report-mode root-cause --format json
```

```json
{
  "root_causes": [
    {
      "root_cause_id": "ad544909f783ad0d",
      "root": "ns::internal::helper",
      "finding_count": 2,
      "findings": ["... the two grouped Change entries ..."]
    }
  ],
  "root_cause_count": 1,
  "changes": ["... the same findings, flat, for backward compatibility ..."]
}
```

The Markdown/text rendering groups the same way, one `### root` heading per
group instead of `--report-mode full`'s severity-bucketed sections:

```bash
abicheck compare old.json new.json --report-mode root-cause
```

```markdown
## Root Causes (1)

### `ns::internal::helper` (2 findings)

- **func_removed**: helper removed
- **internal_symbol_required_by_public_api**: required
```

SARIF keeps its normal one-result-per-finding shape (so every existing
SARIF/code-scanning consumer keeps working unchanged) but adds
`properties.rootCauseId`/`properties.rootCause` to every result — group them
yourself by `rootCauseId` if you want the same buckets JSON/markdown show:

```bash
abicheck compare old.so new.so --report-mode root-cause --format sarif
```

```json
{
  "ruleId": "internal_symbol_required_by_public_api",
  "properties": {
    "rootCauseId": "ad544909f783ad0d",
    "rootCause": "ns::internal::helper"
  }
}
```

## `--report-mode impact`

Renders the `full` report plus an impact summary table showing root changes and
how many interfaces each affects. Available in Markdown and HTML formats.

```bash
abicheck compare old.json new.json --report-mode impact
```

## A second output format from the same run (`--write`)

`compare` computes its comparison once; `--write FORMAT=PATH` renders that
same result into a second format/file, instead of requiring a second
`abicheck compare` invocation to get a different format:

```bash
# A markdown report for humans, plus a JSON artifact for tooling —
# one comparison, two outputs.
abicheck compare old.json new.json \
  --format markdown \
  --write json=report.json
```

- One `FORMAT=PATH` operand: the format and its destination are stated
  together, so neither half can be given without the other.
- `PATH` must point at a different file than `--output`/`-o` — otherwise the
  secondary render would silently overwrite the primary report.
- The secondary render always emits the full, unfiltered report: it ignores
  `--show-only`, which describes only the primary format's display.
- Also works for a directory/package (release) comparison: the per-library
  fan-out renders its second format from the same already-computed
  per-library results, without re-running any library's comparison. Only
  `json`/`markdown`/`junit` are available there (the same set `--format`
  itself accepts for a release operand) — `sarif`/`html`/`review` still
  require a single-pair comparison.

The bundled GitHub Action uses this to get JSON for its sticky PR comment
without re-running the whole comparison a second time.

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
  present. The richest of the three **artifact** tiers: it can reason about
  declared-but-not-emitted API (default-argument values, `const`/`constexpr`
  constants, `final`, access, ref-qualifiers). It does **not** see macro
  contracts or inline/template **body** changes — castxml/clang's declaration
  AST doesn't model macros or bodies at all; that requires the separate L4
  source-ABI-replay layer below.

These three values correspond to the **artifact** evidence layers **L0–L2**.
The higher layers do **not** promote this scalar, and they differ in what they
produce:

- **`dump -p build/`** only bakes the build context into *how* the headers are
  parsed and records `parsed_with_build_context` on the snapshot. On its own it
  adds **no** L3 findings and **no** evidence-coverage table — a plain
  `compare old.json new.json` of two `-p`-dumped snapshots still reports only the
  L0–L2 artifact verdict.
- **Build/source build/source packs (L3/L4)** are what add build-diff/source-diff
  **findings** and the `layer_coverage` table, and only when you pass them at
  compare time via `--build-info` (or a deeper
  `--depth` over `--sources`). These findings follow the authority rule
  — L3/L4 never overrides an artifact-proven verdict.

See [Evidence & Detectability](../learn/evidence-and-detectability.md) for the
full L0–L4 model.

```json
{
  "verdict": "BREAKING",
  "confidence": "high",
  "evidence_tier": "header_aware",
  "evidence_tiers": ["elf", "dwarf", "header"]
}
```

### Per-finding epistemic status (`evidence_status`)

The three fields above describe the comparison as a whole. Each individual
finding in `changes[]` (JSON) or SARIF `results[].properties` can also carry
an `evidence_status` (JSON) / `evidenceStatus` (SARIF) label — *how* that
specific finding was proven, distinct from its `kind`/`severity` (*what* it
is):

| Value | Set when | Means |
|-------|----------|-------|
| `artifact_proven` | the finding's kind is intrinsically a `BREAKING_KINDS` member, **and** this comparison's `evidence_tiers` confirm a real binary was examined | L0/L1/L2 artifact evidence confirms a shipped ABI break. |
| `unattributed` | the finding's kind is intrinsically a `BREAKING_KINDS` member, but this comparison's `evidence_tiers` show only `"header"` — no real binary (ELF/PE/Mach-O/DWARF) was ever examined | The kind's own classification still stands, but this specific run cannot back it with an actual artifact — e.g. a Python-API caller comparing hand-built/loaded `AbiSnapshot` objects. Not a downgrade of the *kind*, only of what *this run* can claim to have proven. |
| `source_contract` | intrinsically `API_BREAK_KINDS` | A source-level break that needs a recompile or a policy decision — not necessarily a shipped ABI break. |
| `contextual_risk` | intrinsically `RISK_KINDS` (`COMPATIBLE_WITH_RISK` under the default policy) | Build/source/deployment context suggests risk without proving a break. |
| `consumer_proven` | *(set explicitly, not derived from the finding's own classification)* | Runtime/`appcompat`/`plugin-check` evidence demonstrated that a **specific** consumer actually depends on what changed — see [Application Compatibility](appcompat.md). |
| `not_checkable` | *(the finding itself)* | The finding **is** the missing-evidence signal (`evidence_required_missing`, ADR-033 D7), not a break — the coverage gap is explicit rather than a silent gap in the report. |

`COMPATIBLE`/`NO_CHANGE` findings (additions, clean comparisons) carry no
`evidence_status` — there is no epistemic strength to qualify.

**`evidence_status` is a function of the finding's `kind`, refined by exactly
one comparison-level fact** — unlike `severity`/the gate/exit code, it
follows *no* verdict-modulation mechanism at all: not the active `--policy`
(a named policy like `plugin_abi` folds every `COMPATIBLE_WITH_RISK` kind
into its breaking set for gating; `sdk_vendor` downgrades source-level
kinds), not a `PolicyFile` kind-set override, not a `PolicyFile`
`evidence_policy` ceiling (the `build_context_drift`/`source_only_findings`/
`graph_risk_findings` knobs, ADR-033 D7), and not a per-finding
`effective_verdict` (ADR-027 A4 pattern modulation, frozen-namespace
escalation). All of those change what *fails the build*, not what evidence
actually proved — and since more than one of them share the same
`effective_verdict` field, there is no reliable way to tell "a detector
individually re-examined this one finding" apart from "an operator's
evidence-tier ceiling swept a whole bucket," so none are trusted. The one
comparison-level signal that *is* trusted is the run's own
`evidence_tiers` — not a gating decision at all, but a positive record of
what was actually examined — which is what distinguishes `artifact_proven`
from `unattributed` above. `severity` answers "does this fail the build
under the active policy?"; `evidence_status` answers "what kind of evidence
backs this finding, full stop?" — the two fields *can* disagree, and that's
by design.

```json
{
  "kind": "func_removed",
  "symbol": "_Z3foov",
  "severity": "breaking",
  "evidence_status": "artifact_proven"
}
```

### Stable finding IDs and structured operation (`finding_id`, `operation`)

Each finding in `changes[]` also carries:

- **`operation`** — a structured `"added"` / `"removed"` / `"modified"`
  classification, derived from the same kind-suffix rule `--show-only`'s
  `added`/`removed`/`changed` tokens already use. Lets a consumer group or
  filter findings by operation without hand-maintaining its own list of
  `_added`/`_removed` kind-name suffixes.
- **`finding_id`** — a stable, deterministic fingerprint (a truncated SHA-256
  hash of `kind`/`symbol`/`old_value`/`new_value`/`source_location`/
  `description`) that identifies *this finding* independent of its position
  in the `changes[]` array. Two `compare` runs over the same underlying
  change produce the same `finding_id`, so a consumer can correlate a
  finding across two report runs (e.g. tracking a waiver, or diffing which
  findings are new between two CI runs) without relying on array order or
  index, neither of which abicheck guarantees stays stable release to
  release. `description` is included specifically to disambiguate two
  otherwise-identical findings on the same symbol (e.g. the same
  pointer-depth change reported on two different parameters of one
  function). `finding_id` deliberately excludes policy-derived fields
  (`severity`, `evidence_status`) — the same underlying finding hashes
  identically regardless of the active `--policy`.

```json
{
  "kind": "func_removed",
  "symbol": "_Z3foov",
  "operation": "removed",
  "finding_id": "3f2a9c8b1d4e5f60"
}
```

### Recommended action per finding (`recommended_action`)

Each finding also carries a structured, machine-readable next step, derived
from the same effective verdict/category resolution `severity`/`operation`
already use — so it can never disagree with them for the same finding:

| `recommended_action` | When | Meaning |
|---|---|---|
| `recompile_and_relink_required` | verdict `BREAKING` | Binary ABI break — existing compiled consumers must be recompiled *and* relinked against the new library. |
| `recompile_required` | verdict `API_BREAK` | Source-level break only — existing compiled binaries keep working, but source recompiling against the new headers will fail. |
| `verify_deployment_compatibility` | verdict `COMPATIBLE_WITH_RISK` | Binary-compatible, but may fail to load in some deployment environments — needs manual verification, not a recompile. |
| `review_recommended` | verdict `COMPATIBLE`, not an addition | A quality issue (e.g. an STL type exposed by value, missing SONAME) — compatible, but worth a look. |
| `no_action_required` | verdict `COMPATIBLE`, an addition | New public API surface — purely additive, nothing to do. |

```json
{
  "kind": "func_removed",
  "symbol": "_Z3foov",
  "severity": "breaking",
  "recommended_action": "recompile_and_relink_required"
}
```

### Reviewer guidance for additions (`reviewer_action`)

`recommended_action: "no_action_required"` is accurate for the *old binary
consumer* — nothing to recompile, nothing to relink — but collapses every
addition to the same value even though a reviewer approving new public API
surface almost always has something to check. Findings with
`recommended_action: "no_action_required"` also carry a `reviewer_action`
key with that finer-grained guidance; every other finding omits the key,
since `recommended_action` itself is already reviewer-actionable there.

| `reviewer_action` | When | Meaning |
|---|---|---|
| `review_exhaustive_switches` | kind `enum_member_added` | Old binaries are unaffected, but a source consumer's exhaustive `switch`/sentinel-value pattern may silently miss the new value. |
| `document_stable_replacement` | kind `experimental_graduated` | An unstable API just became part of the stable support contract — document the change, don't just ship it. |
| `confirm_public_api_intent` | every other addition | Confirm the new export was intentional (not an accidental symbol leak) and consider a release note. |

```json
{
  "kind": "enum_member_added",
  "symbol": "Color::PURPLE",
  "severity": "compatible",
  "recommended_action": "no_action_required",
  "reviewer_action": "review_exhaustive_switches"
}
```

### Typed gate summary (`severity.blocking`, `severity.blocking_categories`)

When `--severity-*` configuration is active, the top-level `severity` object
gets two additional fields alongside the existing `config`/`categories`/
`exit_code`:

- **`blocking`** — `true` when the severity-aware exit code is non-zero
  (equivalent to `exit_code != 0`, provided as a named boolean so a consumer
  doesn't have to know the exit-code convention).
- **`blocking_categories`** — the list of category names (`abi_breaking`,
  `potential_breaking`, `quality_issues`, `addition`) that both have findings
  *and* are configured `error` — i.e. the categories actually responsible for
  the non-zero exit code, mirroring SARIF's `properties.severityGate` block.

```json
{
  "severity": {
    "config": {"abi_breaking": "error", "potential_breaking": "warning", "quality_issues": "warning", "addition": "error"},
    "categories": {"addition": {"severity": "error", "count": 1}},
    "exit_code": 1,
    "blocking": true,
    "blocking_categories": ["addition"]
  }
}
```

---

### Contract-evaluation report fields (`contract_relevance`, `contract_coverage_failures`)

Under `--contract`, two field groups appear that aren't present
in a plain `compare` report — mental model and command reference:
[Contract-Aware Compatibility](../learn/contract-aware-compatibility.md),
[Contract Evaluation](contract-evaluation.md).

**Per finding** — whether policy actually scored it, and why:

```json
{
  "kind": "func_removed",
  "symbol": "detail::internal_helper",
  "contract_relevance": "PROVEN_OUT_OF_CONTRACT",
  "contract_reason_code": "terminal_authoritative_exclusion",
  "contract_assurance": "complete",
  "compatibility_evaluation_status": "NOT_EVALUATED",
  "compatibility_decision": null,
  "gate_contribution": 0
}
```

Read this as: **the finding is real and detected — `compatibility_decision:
null` means policy never ran on it, not that it was judged compatible.**
`contract_relevance`/`contract_reason_code` say why it was excluded from
scoring; `gate_contribution: 0` confirms it moved neither the verdict nor
the exit code.

**Run level** — the separate question of whether there was enough evidence
to make *any* contract decision at all:

```json
{
  "contract_coverage_failures": [],
  "contract_coverage_exit_contribution": 0
}
```

An incomplete-evidence run looks like this instead. Compatibility itself has
no separate process exit — only the overall exit code does — so read this as:
the compatibility *verdict* stays `NO_CHANGE` (nothing evaluated found a
problem), while `contract_coverage_exit_contribution: 1` independently
floors the overall process exit at `1` by default, because the two axes are
orthogonal:

```json
{
  "verdict": "NO_CHANGE",
  "contract_coverage_failures": [
    {
      "provider": "export_table",
      "side": "old",
      "record_id": "export_table:old",
      "reason": "provider_unavailable",
      "status": "unavailable",
      "completeness": "not_started",
      "mode": "exports",
      "suppressible": false
    }
  ],
  "contract_coverage_exit_contribution": 1
}
```

`suppressible: false` is structural, not a default someone forgot to flip —
a `CoverageFailure` has no `kind`/`symbol`/`source_location` for
`--suppress` to match against, so it cannot silence one even in principle.

---

## JSON schema and stability guarantees

The `compare --format json` document is a **stable, machine-readable contract**.
It is described by a versioned [JSON Schema](https://json-schema.org/) (draft
2020-12) that ships inside the package at
`abicheck/schemas/compare_report.schema.json` and is importable:

```python
from abicheck.schemas import (
    REPORT_SCHEMA_VERSION,        # the MAJOR.MINOR this build emits
    COMPARE_REPORT_SCHEMA_PATH,   # pathlib.Path to the .schema.json
    load_compare_report_schema,   # -> dict
)
```

Every JSON report carries a top-level `report_schema_version` field
(`MAJOR.MINOR`) so consumers can detect the contract version they are reading.

> **`run_outcome`.** Every JSON report (`compare`/release, schema 2.48;
> `scan`, schema 1.24; and the not-comparable refusal document alike)
> carries an additive top-level `run_outcome` block —
> `compatibility`/`assurance`/`gate`/`operational`/`lifecycle`, the
> report's independent-axis outcome (ADR-063 D6) — alongside the unchanged
> `verdict`/`exit_code`/`severity` fields; nothing existing changes
> meaning or is removed. `gate` is an exit-code-free category
> (`none`/`addition_quality`/`potential_breaking`/`abi_breaking`).
> `operational` is an **independent** axis, not proof that no compatibility
> result exists: it is `none` when nothing operational went wrong, and any
> other value (`budget_overflow`/`not_comparable`/`evidence_contract_error`/
> `extraction_error`) flags an incomplete part of the run — but `compatibility`
> can still be non-`null` alongside it, e.g. a late budget/evidence abort
> that retains an already-completed verdict, or a release/scan set whose
> reported `compatibility` is one member's real result while a *different*
> member independently failed operationally. `compatibility` is `null`
> only when no real comparison ran at all: a resolve-baseline failure, a
> bootstrap/new-target advisory pass, or a not-comparable refusal
> (`operational: "not_comparable"`).

> **Two version numbers, two contracts.** `report_schema_version` (above)
> versions the **comparison report** emitted by `compare`. It is distinct from
> the `schema_version` integer inside a **snapshot** (`.abi.json`) produced by
> `dump` — that one versions the on-disk ABI surface, and its current value
> lives on its own fact-owner page
> ([Snapshot format](../reference/snapshot-format.md)), not here.
> `abicheck dump --format json` writes a **snapshot** (carrying `schema_version`),
> not a report, so it has no `report_schema_version`. A report and a snapshot can
> carry different version numbers at the same time; consumers should read
> whichever field belongs to the file they loaded.
>
> `scan --format json` is a **third, separate shape**: it emits a `ScanOutcome`
> object (`mode`, `level`, `risk`, `verdict`, `exit_code`, …). It carries its
> own top-level `scan_schema_version` field (`MAJOR.MINOR`, importable as
> `abicheck.schemas.SCAN_SCHEMA_VERSION`) — independent of, and not
> interchangeable with, `report_schema_version`. The typed Python
> `ScanResult.to_dict()` envelope (`abicheck.service`) stamps the same value at
> its own top level, in addition to nesting the `ScanOutcome` dict (with its
> own `scan_schema_version`) under its `report` key. There is currently no
> packaged `.schema.json` for scan output (unlike `compare`'s
> `compare_report.schema.json`); the version field is honored the same way
> (accept a shared `MAJOR`, ignore unknown keys) until one exists.
>
> `scan --against`'s baseline summary carries an optional `coverage_warnings`
> list (`scan_schema_version` 1.21), mirroring `compare`'s own top-level
> field of the same name and shape — e.g. a warning that the two compared
> binaries are byte-identical (a possible mistaken input, not a real
> "no ABI differences" result). Omitted when there is nothing to warn about.

```json
{
  "report_schema_version": "2.51",
  "library": "libfoo.so.1",
  "verdict": "BREAKING"
}
```

> **`effective_config_digest`/`effective_config_fields` (schema 2.45; the
> field set itself grew again in 2.46 -- see below).**
> Every `compare`/`compare-release` JSON report, and every `scan --against`
> report's `diff` block (`scan_schema_version` 1.19, field-set update 1.20),
> carries a
> `sha256:...` fingerprint of the resolved gate/policy/surface/contract
> configuration the comparison actually ran under, alongside the named
> field dict it was hashed from — so two reports (or a report replayed
> later) can be compared for "did the resolved configuration change"
> without a byte-for-byte diff, and a mismatch can be attributed to a
> specific field rather than read as an opaque hash. `effective_config_
> fields["_tier"]` is `"contract"` when the run resolved a full
> `CompatibilityEvaluationConfig` (whenever `--pack` selected a pack, not
> only under `--contract` — real pack identities included) or `"baseline"`
> otherwise (the policy/gate fields every comparison resolves regardless);
> the two tiers are not cross-comparable. See
> `abicheck.effective_config_digest`'s own module docstring for the full
> field set and precedence.

#### `scan --against`: the report cap and truncation (`--max-findings`)

`scan --against`'s `diff` block itemizes the comparison's gating findings
(`findings`) and any `--suppress`-silenced ones (`suppressed`), each capped at
20 entries by default so a large diff can't blow up the always-on scan output
— `compare --format json` remains the way to see every finding
unconditionally. Raise or lower the cap per run with `scan --max-findings N`
(`ScanRequest.max_findings` in the typed Python API), or globally via the
`ABICHECK_MAX_BASELINE_FINDINGS` environment variable when neither passes an
explicit value; either configures the same cap, and it only changes how much
of the diff a run itemizes — never the verdict or exit code.

When either list is actually truncated, the block sets the existing
`findings_truncated`/`suppressed_truncated` booleans (schema 1.8+) and, since
schema 1.10, also `findings_truncated_kinds`/`suppressed_truncated_kinds` — a
`ChangeKind -> count` map of what was cut from that list, so the shape of a
truncated diff (which kinds dominate) is visible without rerunning at a
higher cap. Both maps are absent when nothing was truncated.

Each entry in `suppressed` also carries `suppression_rule` (which
`--suppress` rule matched it) and `pre_suppression_bucket` (the
`breaking`/`api_break`/`risk`/`compatible` bucket the finding would have
counted as had `--suppress` not withheld it) — a suppressed finding's report
entry always says more than "suppressed", so a reader can tell a suppressed
ABI break apart from a suppressed cosmetic note. `scan --against --format
text` prints the same information: an always-present `suppressed=N` count in
the "Baseline comparison" line, and `--show-suppressed` itemizes each one.

Since schema 1.11, any `findings`/`suppressed` entry for a removal whose ELF
symbol linkage was captured also carries `symbol_binding`
(`global`/`weak`/`local`/`unique`/`other`) — the same field
`compare --format json`/SARIF emit (see `binding:` under
[Suppressions](suppressions.md)), so a `binding:`-scoped suppression's
match/no-match is auditable from `scan --against` too.

Since schema 1.13, the block also carries an always-on `additions` array —
the addition-shaped subset of the `compatible` bucket (new public-API
surface, `ChangeKind`'s `ADDITION_KINDS`), itemized the same shape as
`findings` (`"bucket": "compatible"`) regardless of whether severity policy
made any of them the run's blocking cause. Capped independently of
`findings`' own budget (the same `--max-findings`/
`ABICHECK_MAX_BASELINE_FINDINGS` cap), with `additions_truncated` set when
that cap was hit, alongside `additions_total` (the exact, untruncated
addition count — `compatible`'s own scalar mixes additions and quality
findings, so it can't answer "how many additions, exactly" on its own). All
three keys are omitted when `compatible` has no addition-shaped entry. This
is what lets a `scan --against` PR comment (see the Action's own
`pr-comment`/`pr-comment-on` inputs, [GitHub Action usage](github-action.md))
render a green "public API additions" section the same way `compare`'s own
JSON report already does via its full `changes` list.

The block also carries `quality` (plus optional `quality_truncated`/
`quality_total`) — `additions`'s exact complement, itemizing the
compatible-but-non-addition subset of `compatible` (a quality-category
change like `func_noexcept_added`, or a policy-demoted removal reclassified
compatible) the same shape and under the same cap as `additions`, so the
comment's "safe" total (which reads the full `compatible` scalar) always has
a matching set of itemized rows to show a reviewer, whichever shape those
findings take.

The block also carries `policy` — the resolved compatibility policy name
(e.g. `"strict_abi"`) that actually classified these buckets, the same fact
`compare`'s own top-level JSON report has always carried. Present on any
real comparison (absent only for the `NOT_COMPARABLE`/audit-only `diff`
shapes, which never reach policy classification).

Since schema 1.14, the block also discloses the active `--policy`
audit trail — previously a `scan --format json` reader could see a
downgraded verdict with no way to tell which rule produced it, unlike the
`compare`/report path. `policy_overrides` (a `ChangeKind -> verdict` map)
and `policy_reclassify` (the active, non-expired selector-scoped
`reclassify:` rule set — see [Suppressions](suppressions.md)) mirror
`compare`'s own JSON report byte-for-byte, and `policy_file` names the
source path when either is present. Each `findings`/`additions`/`quality`/
`suppressed` entry also gains an optional `reclassified_by` key naming
which rule actually decided that finding's verdict. All four keys are
omitted when no policy file (or no active rule) applies.

```json
{
  "scan_schema_version": "1.14",
  "diff": {
    "breaking": 25,
    "findings": ["... 20 entries ..."],
    "findings_truncated": true,
    "findings_truncated_kinds": {"func_removed": 5},
    "additions": ["... up to 20 entries ..."],
    "policy_overrides": {"func_removed": "COMPATIBLE_WITH_RISK"},
    "policy_reclassify": [{"to": "COMPATIBLE_WITH_RISK", "binding": "weak"}],
    "policy_file": "policy.yml"
  }
}
```

### Scoped vs. full-library results (`full_verdict`/`full_severity`/`full_summary`)

A `--used-by`/`--required-symbol(s)` scoped compare gates its exit code and
`verdict`/`severity`/`summary` on the *scoped* subset of changes (plus any
scoped-only synthetic findings, e.g. `consumer_required_symbol_removed`) —
that scoped result is what a CI gate should act on. When the scoped result
differs from the unscoped, full-library comparison, the original full-library
values are preserved alongside it: `full_verdict` (schema 2.1+, same enum as
`verdict`), `full_severity` (schema 2.1+, same shape as `severity`), and
`full_summary` (schema 2.9+, same shape as `summary`). All three are absent
for an unscoped compare, where `verdict`/`severity`/`summary` already
describe the whole library. `summary` itself is always recomputed from the
complete (post-scoping) `changes` array, so it never contradicts the changes
a consumer actually sees — `full_summary` exists only to preserve the
pre-scoping counts a consumer might also want.

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

### `aggregate`'s own report shape

`abicheck aggregate --format json` is a **separate document**, not a
`compare`/`scan` report — it's versioned by its own
`aggregate_schema_version` and describes a fan-in over already-produced
reports rather than one comparison. Its five independent axes
(`compatibility`/`coverage`/`gate`/`contract_coverage`/`analysis_assurance`)
and the `profile_matrix`/`finding_matrix` reconciliation blocks are
documented, with a fully annotated example, in [Aggregate
Reports](aggregate-reports.md) rather than repeated here.

---

## Release recommendation

Translates the verdict into the maintainer's actual question — *what version do
I release, and do I need to bump the SONAME?* — as a recommended semantic-version
bump (`major`/`minor`/`patch`/`none`) plus a SONAME action. Unconditional in
`json`, `markdown`, and `review` (CLI cleanup phase two, PR 1 removed the
`--recommend` opt-in flag that used to gate it): JSON always carries it under
`release_recommendation`, and Markdown/`review` always render it as a
section — no flag needed. `html` does not render it (the HTML report has no
recommendation section), and `sarif`/`junit` intentionally omit it — neither
format has a natural slot for a release verdict.

```bash
abicheck compare old.so new.so -H include/
```

The recommendation is **policy-aware** (it honours `--policy` and
`--policy`):

| Verdict | Bump | SONAME |
|---------|------|--------|
| `NO_CHANGE` | none | no bump needed |
| `BREAKING` | major | bump required (or `bump_missing`/`bump_performed` if abicheck observed the soname) |
| `API_BREAK` | major | no bump needed (binary stays loadable) |
| `COMPATIBLE_WITH_RISK` | minor/patch | no bump needed |
| `COMPATIBLE` (additions) | minor | no bump needed |
| `COMPATIBLE` (quality only) | patch | no bump needed |

In **JSON** output the recommendation is always present (no flag needed) under
the `release_recommendation` key, so CI and agents can gate on it — **check
`state` first**, before ever reading `version_bump`:

```bash
abicheck compare old.so new.so -H include/ --format json \
  | jq -r '.release_recommendation
      | if .state == "actionable" then
          "release: bump \(.version_bump), soname \(.soname_action)"
        elif .state == "review" then
          "needs human review: \(.rationale)"
        else
          "no confirmed evidence: \(.rationale)"
        end'
# release: bump major, soname bump_required
```

The `elif`/`else` branches never read `.version_bump` — for `"review"` it's
still a real value but unconfirmed against binary evidence, and for
`"unavailable"` it's `null` (schema 2.20+); either way the branch that treats
it as actionable is exactly the one this example skips.

`state` is one of `actionable` (act on `version_bump`/`soname_action`
directly), `review` (a source/API break was found but no binary evidence
confirms a SONAME action either way — `version_bump` is still a real value,
route to a human), or `unavailable` (abicheck had no binary evidence at all
to back a confident bump — **`version_bump` is `null`**, not a
plausible-looking string, so automation that reads `version_bump` without
checking `state` first can silently treat a real, unconfirmed break as "no
action needed" or crash on the unexpected `null`).

`possible_impact` is always a non-null string — the bump abicheck would
recommend if its evidence were sufficient to confirm one. It equals
`version_bump` when `state` is `actionable`; for `review`/`unavailable` it is
the machine-readable form of what `rationale` prose already says (e.g. sizing
a human-review queue), **not** a substitute for checking `state` before
acting — an automated release job must still gate on `version_bump`/`state`,
never on `possible_impact` alone. `rationale` always explains what abicheck
would still recommend even when `state` isn't `actionable`. See the
compare-report [JSON Schema](../reference/schemas/v1/compare_report.schema.json)'s
`release_recommendation` object for the full field contract.

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
    runs-on: ubuntu-24.04
    permissions:
      security-events: write  # required by the upload-sarif step below
      contents: read
    defaults:
      run:
        shell: bash -el {0}
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6

      - uses: conda-incubator/setup-miniconda@fc2d68f6413eb2d87b895e92f8584b5b94a10167  # v3
        with:
          activate-environment: abicheck

      - name: Install abicheck
        run: |
          # Avoid Ubuntu's Clang-17 CastXML build; conda-forge supplies a
          # compatible toolchain. The abicheck Action uses a checksum-pinned
          # official Superbuild instead.
          conda install -y -c conda-forge castxml
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
        uses: github/codeql-action/upload-sarif@7188fc363630916deb702c7fdcf4e481b751f97a  # v4
        with:
          sarif_file: abi.sarif
```

!!! warning "Pin every action in this job to a commit SHA"
    This job grants `security-events: write` for the upload-sarif step, so
    every `uses:` here runs with that permission's token — a repointed or
    compromised mutable tag (`@v4`, `@v3`) would run with it too. Pin to a
    full commit SHA, keeping the release tag in a trailing comment for
    auditability (as above). See [Versioning](github-action.md#versioning).

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

### Suppressed findings

A `--suppress`-silenced finding still appears in SARIF `results` — dropping
it would leave zero trace of what was withheld — but is marked via the
standard SARIF `suppressions` property (spec §3.27.24) instead of the plain
result levels used above, so a conformant consumer (GitHub Code Scanning
included) knows to keep it out of the default active-alerts view while still
recording it:

```json
{
  "ruleId": "func_removed",
  "level": "error",
  "message": { "text": "Function foo() removed" },
  "suppressions": [
    { "kind": "external", "justification": "suppressed by --suppress rule: intentional" }
  ]
}
```

`properties.suppressedCount` at the run level still reports the total, for a
consumer that only wants the count.

---

## JUnit XML Output

abicheck can produce JUnit XML reports for CI systems that display test results
in their standard dashboards — GitLab CI, Jenkins, Azure DevOps, CircleCI, and
others.

### Usage

```bash
abicheck compare old.json new.json --format junit -o results.xml
abicheck compare release-1.0/ release-2.0/ --format junit -o abi-tests.xml
```

### How it works

ABI changes are mapped to JUnit test cases:

- Each **library** in a bundle `compare` (directory/package inputs) becomes a `<testsuite>`
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

---

## Evidence coverage and metrics (build/source pack)

A compare or scan that carries build/source evidence reports what each
layer covered, alongside the findings (moved here from
[Source & Build Data](../learn/build-source-data.md), which keeps the
narrative).

Every compare run that involves a pack prints an **evidence-coverage table** so
you can tell which findings are artifact-proven vs. build-context-only:

```text
Evidence coverage:
  L0 binary metadata         present, high confidence
  L1 debug info              present, high confidence: DWARF
  L2 public header AST       present, high confidence: header-scoped
  L3 build context           present, high confidence: cmake+ninja, 142 compile units, 1 target
  L4 source ABI replay       present, high confidence: clang extractor, scope=target, parsed 142/142 TUs
  L5 source graph summary    not_collected
```

The same rows are emitted as a structured `layer_coverage` array in the
`--format json` report (schema `report_schema_version` 2.0+; the key was
`evidence_coverage` in 1.x), so machine consumers can key off layer status
and confidence.

### Evidence metrics (timing & finding split)

Alongside the coverage table, a pack-aware compare prints an **evidence-metrics
summary** (ADR-033 D6/D9) so CI can tune which evidence mode to run by cost and
signal:

```text
Evidence metrics:
  collection time            0.0142s
  findings                   artifact-backed=3, source-only=0, build-context-drift=1
```

The same numbers are emitted as a structured `evidence_metrics` object in the
`--format json` report (schema `report_schema_version` 2.1+), keyed by the D9
metric names:

```json
"evidence_metrics": {
  "extractor.duration_seconds": 0.0142,
  "coverage.build_context.present": true,
  "coverage.source_abi.mode": "not_collected",
  "coverage.graph.mode": "not_collected",
  "findings.artifact_backed.count": 3,
  "findings.source_only.count": 0,
  "findings.build_context_drift.count": 1,
  "findings.evidence_required_missing.count": 0,
  "findings.demoted_by_surface.count": 0,
  "findings.suppressed_with_reason.count": 0
}
```

`artifact-backed` findings are proven by the binary/debug/header tiers (L0–L2);
`source-only` and `build-context-drift` come from the optional L3–L5 layers and
never override an artifact-backed verdict. Both blocks are additive and present
only when build-info/source facts were involved in the compare.

### What is being checked — and what is not, and why

Right below the coverage table, every pack-aware compare prints a **capability
report** that translates the available evidence into the concrete *check
categories* it enables — and, for each disabled category, the precise reason.
This makes the cumulative picture explicit as you add inputs (binary → +debug
info → +headers → +build data → +sources):

```text
Checks enabled for this scan (and why others are not):
  [on]  Symbol presence & linkage (added/removed/SONAME) — from the binary's dynamic symbol table
  [on]  Type layout, members, vtables, signatures — from DWARF/PDB debug info
  [on]  API decls absent from the symbol table; public-surface scoping — from the public header AST
  [on]  Build-flag & toolchain drift (visibility, std, ABI flags) — from build-system data
  [off] Macros, default args, inline/template/constexpr bodies — no sources/clang: source-only API changes are not detected
  [off] Impact / call / reachability graph — no graph evidence: cross-symbol impact is not analyzed
```

Each category is gated on exactly one evidence layer, so a `[off]` line tells you
exactly which input (or tool) to add to enable it — e.g. installing clang and
passing `--sources` (L4 source-ABI replay) turns on the macro / default-argument / inline-body /
template-body / constexpr checks.

### Header parse context

`header_parse_context_drift` fires when the new side carries a public-header AST
that was **not** parsed with the build's ABI-relevant flags. To avoid this,
dump the snapshot with the build's compile database — `abicheck dump … -p build/`
records `parsed_with_build_context` on the snapshot, and a later `compare`
honors it and suppresses the drift finding.
