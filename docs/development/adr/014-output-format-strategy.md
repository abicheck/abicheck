# ADR-014: Output Format Strategy

**Date:** 2026-03-18
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

---

## Context

abicheck results must be consumable by:

- **Humans** reading terminal output or CI logs
- **CI systems** parsing machine-readable output for gate decisions
- **GitHub Code Scanning** ingesting SARIF for PR annotations
- **Web browsers** for standalone report viewing

No single format serves all consumers. The output format strategy defines
which formats are supported, what contract each format provides, and how
format selection works.

---

## Decision

### Five output formats

| Format | Primary consumer | CLI flag | Default? |
|--------|-----------------|----------|----------|
| **Markdown** | Humans (terminal, CI logs) | `--format markdown` | Yes |
| **JSON** | Automation, AI agents, scripts | `--format json` | No |
| **SARIF 2.1.0** | GitHub Code Scanning | `--format sarif` | No |
| **HTML** | Standalone report viewing | `--format html` | No |
| **JUnit XML** | GitLab CI, Jenkins, Azure DevOps | `--format junit` | No |

### Markdown (default)

- Rendered in monospace terminals and CI log viewers
- Sections: verdict banner, summary table, changes grouped by severity
  (breaking → source breaks → risk → compatible)
- Emoji verdict indicators: ❌ (BREAKING), ⚠️ (API_BREAK/RISK), ✅
  (COMPATIBLE/NO_CHANGE)
- Demangled symbol names for readability

Markdown is the default because it works everywhere — terminals, GitHub PR
comments, CI log viewers, README files — without requiring special rendering.

### JSON

- Machine-readable structured output
- Top-level fields: `library`, `verdict`, `summary`, `changes[]`,
  `suppressed_changes[]`, `detectors[]`
- Summary includes: `breaking_count`, `source_breaks`, `risk_count`,
  `compatible_additions`, `total_changes`, `binary_compatibility_pct`,
  `affected_pct`
- Each change includes: `kind`, `symbol`, `description`, `old_value`,
  `new_value`, `source_location`, `affected_symbols`
- Library metadata: path, SHA-256 hash, file size
- Detector results: name, changes count, enabled status, coverage gaps

JSON output uses the same `DiffResult` data as all other formats — no
format-specific data loss.

### SARIF 2.1.0

- Targets GitHub Code Scanning (upload via `github/codeql-action/upload-sarif`)
- SARIF specification: OASIS SARIF v2.1.0
- Mapping:
  - Each `ChangeKind` → SARIF rule (rule ID = `ChangeKind.value`)
  - `BREAKING` → SARIF level `error`
  - `API_BREAK` → SARIF level `warning`
  - `COMPATIBLE_WITH_RISK` → SARIF level `warning`
  - `COMPATIBLE` → SARIF level `note`
- Tool version from `importlib.metadata.version("abicheck")`
- Results include source locations (when available from headers)

### HTML

- Self-contained single file — no external CSS, JavaScript, or images
- ABICC-inspired layout for familiarity (but not format-compatible)
- Verdict banner with color coding:
  - BREAKING: red (`#b71c1c` / `#ffcdd2`)
  - COMPATIBLE_WITH_RISK: orange (`#e65100` / `#fff3e0`)
  - COMPATIBLE: green (`#1b5e20` / `#c8e6c9`)
- Binary Compatibility % metric (based on old exported symbol count)
- Sectioned change tables: Removed | Changed | Added
- Demangled names displayed, mangled names as tooltips
- Suppressed changes section (if any)

Self-contained HTML was chosen over an external-stylesheet approach to ensure
reports can be emailed, archived, or opened offline without broken rendering.

### JUnit XML

- Targets CI systems with JUnit test result dashboards (GitLab CI, Jenkins,
  Azure DevOps, CircleCI)
- Mapping:
  - Each library → `<testsuite>`
  - Each exported symbol/type → `<testcase>`
  - `classname` groups: `functions`, `variables`, `types`, `enums`, `metadata`
  - `BREAKING` / `API_BREAK` → `<failure>` element
  - `COMPATIBLE_WITH_RISK` → `<failure>` only when per-kind severity is `error`
  - `COMPATIBLE` → passing test case (no `<failure>` child)
- When old snapshot is available, unchanged symbols appear as passing tests
  for a meaningful pass-rate
- Uses `xml.etree.ElementTree` (stdlib) — no external dependency

JUnit was chosen over a proprietary CI-specific format because all major CI
platforms support JUnit natively, making it the best single format for broad
CI integration.

### Format selection

```bash
abicheck compare old.so new.so                              # Markdown (default)
abicheck compare old.so new.so --format json                 # JSON
abicheck compare old.so new.so --format sarif                # SARIF
abicheck compare old.so new.so --format html                 # HTML
abicheck compare old.so new.so --format html -o report.html  # HTML written to file
abicheck compare old.so new.so --format junit -o results.xml # JUnit XML
```

The format must be explicitly selected via `--format`. The `-o` / `--output`
flag only controls where output is written — it does not infer format from
the file extension. If `--format` is omitted, the default is `markdown`
regardless of the output filename.

### Information preservation

All five formats are generated from the same `DiffResult` object. No
format-specific information is added or lost — switching formats changes
presentation only, not content. Verdict and exit code computation is
independent of output format.

---

## Consequences

### Positive

- Every consumer has a first-class output format
- GitHub Code Scanning integration via standard SARIF — no custom tooling
- Self-contained HTML enables offline report archival
- Markdown default works everywhere with zero configuration
- No information loss between formats

### Negative

- Five formatters to maintain (reporter.py, sarif.py, html_report.py, junit_report.py)
- SARIF severity mapping is a compatibility contract with GitHub
- Self-contained HTML generates larger files than external-CSS approaches
- JSON schema evolves with the project (see ADR-015 for schema versioning)

---

## References

- `abicheck/reporter.py` — Markdown and JSON formatting
- `abicheck/sarif.py` — SARIF 2.1.0 output
- `abicheck/html_report.py` — HTML report generation
- `abicheck/junit_report.py` — JUnit XML output
- `abicheck/cli.py` — `--format` flag and output file handling
