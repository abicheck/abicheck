# ADR-017: GitHub Action Design

**Date:** 2026-03-18
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

---

## Context

abicheck must be easy to integrate into GitHub-based CI pipelines. GitHub
Actions is the dominant CI system for open-source projects, and a first-party
action removes the friction of manual tool installation and invocation.

### Requirements

- Zero-configuration for common use cases (compare two library versions)
- Support all abicheck modes (compare, dump, deps, stack-check)
- Enable SARIF upload to GitHub Code Scanning
- Support ABICC migration (accept Perl dump inputs)
- Work on GitHub-hosted runners without custom Docker images

### Options considered

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Composite action** | Shell steps in `action.yml`, runs in runner's environment | Fast startup, runner OS flexibility |
| B: Docker action | Custom container with all dependencies pre-installed | Reproducible but slow startup (~30s pull), Linux only |
| C: JavaScript action | Node.js wrapper calling abicheck subprocess | Extra indirection, Node.js maintenance burden |

---

## Decision

### Composite action (Option A)

The action is a composite action defined in `action.yml` that runs directly
in the GitHub-hosted runner's environment. Composite was chosen over Docker to
avoid the ~30s container pull overhead that dominates short CI jobs. For users
requiring reproducible environments, running abicheck in a custom container
is a supported alternative.

### Action flow

```text
1. Set up Python (actions/setup-python@v5)
2. Install system dependencies (castxml, gcc) — conditional on install-deps flag
3. Install abicheck (pip install from action path)
4. Run abicheck via action/run.sh with inputs as environment variables
5. Upload SARIF to Code Scanning (conditional on format and upload-sarif flag)
```

### Key inputs

| Input | Description | Default |
|-------|-------------|---------|
| `mode` | Operation: compare, dump, deps, stack-check | `compare` |
| `old-library` | Old library / JSON snapshot / ABICC Perl dump | — |
| `new-library` | New library or binary (required) | — |
| `header` / `old-header` / `new-header` | Public headers (space-separated) | — |
| `include` / `old-include` / `new-include` | Extra include directories | — |
| `lang` | castxml language mode: `c++` or `c` | `c++` |
| `gcc-path` / `gcc-prefix` / `gcc-options` | Cross-compiler configuration | — |
| `follow-deps` | Include transitive dependency graph (ELF) | `false` |
| `format` | Output: markdown, json, sarif, html | `markdown` |
| `policy` / `policy-file` | Built-in or custom policy | `strict_abi` |
| `suppress` | YAML suppression file | — |
| `fail-on-breaking` / `fail-on-api-break` | Exit code flags | — |
| `severity-preset` / `severity-addition` | Severity configuration | — |
| `extra-args` | Additional CLI arguments | `''` |
| `install-deps` | Install castxml + gcc automatically | `true` |

### Output variables

| Output | Description |
|--------|-------------|
| `verdict` | COMPATIBLE, API_BREAK, BREAKING, SEVERITY_ERROR, REMOVED_LIBRARY, ERROR (see ADR-009 for the 5-tier system). For `stack-check`: PASS, WARN, FAIL. SEVERITY_ERROR is emitted when the severity system (via `--severity-*` flags) causes a non-zero exit code 1. |
| `exit-code` | abicheck numeric exit code |
| `report-path` | Path to generated report file |

### System dependency installation

When `install-deps: 'true'` (default), the action installs:

- **castxml** via `apt-get install castxml` (Ubuntu runners)
- **gcc/g++** (usually pre-installed on GitHub runners)

This limits the action to **Linux runners** for header-based analysis.
Windows and macOS runners can use the action for binary-only comparison
(snapshot-to-snapshot or ELF-only mode) but not for castxml-based header
parsing. On non-Linux runners, set `install-deps: 'false'` and either
pre-install castxml yourself or use snapshot-based comparison.

### SARIF integration

When `format: sarif` and `upload-sarif: 'true'`:

```yaml
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: ${{ steps.abicheck.outputs.report-path }}
```

This enables ABI changes to appear as GitHub Code Scanning alerts directly
in pull request diffs.

### ABICC migration support

The action accepts ABICC Perl dump files as `old-library` input. Format
detection is automatic — Perl dumps starting with `$VAR1 = {` are parsed
as ABICC data. This allows users to keep existing ABICC baselines while
migrating to abicheck.

---

## Consequences

### Positive

- Fast startup — no Docker pull, no container build
- Uses runner's native environment (Python, gcc already available)
- Supports all abicheck modes through a single action
- SARIF integration provides PR-level ABI annotations
- ABICC migration path via Perl dump acceptance

### Negative

- Composite actions are Linux-only for full functionality (castxml via apt)
- System dependency installation adds ~10s to action runtime
- `pip install` from action path means the action version is tied to the
  repo checkout
- No environment isolation — could conflict with other Python packages in
  the workflow

---

## References

- `action.yml` — Action definition (260 lines)
- `action/run.sh` — Action execution script
- `action/install-deps.sh` — System dependency installation
- ADR-009 — Verdict system and exit code contract (verdict output values)
- ADR-014 — Output format strategy (SARIF for Code Scanning)
