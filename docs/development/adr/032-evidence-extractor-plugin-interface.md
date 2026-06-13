# ADR-032: Evidence Extractor Plugin Interface and Security Model

**Date:** 2026-06-09
**Status:** Accepted — implemented (D1–D10). The extractor interface, capability
model, action-permission ceiling, external-CLI manifest, collection modes, and
reproducibility ledger ship in `abicheck/evidence/extractor.py` +
`extractor_manifest.py`, wired into `collect`
(`--extractor-manifest` / `--allow-build-query` / `--collection-mode`). **Amended 2026-06-12** (ADR-028 source-tree model) — see Amendment below.
**Decision maker:** Nikolay Petrov

---

## Context

The evidence-pack extension (ADR-028..031) depends on several external
information sources:

- build-system query output;
- compiler command databases;
- compiler-recorded metadata;
- source ABI dumpers;
- graph engines;
- optional wrapper/interception tools.

Hard-coding every extractor into the core dumper would make abicheck
brittle. At the same time, arbitrary plugins create supply-chain, security,
privacy, and reproducibility risks — the same class of concerns ADR-021b
addressed for the MCP server. abicheck needs a narrow extractor interface
that isolates external tools from verdict policy.

---

## Decision

### D1. Evidence extractors are adapters, not verdict engines

An extractor may collect and normalize facts. It may not decide final
ABI/API verdicts.

```text
external tool / build system / compiler output
    ↓
Extractor adapter
    ↓
raw artifact + normalized facts + diagnostics + confidence
    ↓
abicheck comparison engine and policy
    ↓
verdicts and reports
```

The core engine owns: schema validation; entity matching and merging;
confidence calculation; finding classification (`ChangeKind` partition,
ADR-011); policy profile application (ADR-010); suppressions and ledgers
(ADR-013, ADR-024); and final verdicts/exit codes (ADR-009).

### D2. Python extractor interface

```python
class EvidenceExtractor(Protocol):
    name: str
    version: str
    schema_version: int

    def discover(self, context: CollectionContext) -> DiscoveryResult:
        """Report whether this extractor can run and what it can collect."""

    def collect(self, context: CollectionContext, output_dir: Path) -> CollectionResult:
        """Collect raw artifacts. Must not normalize verdicts."""

    def normalize(self, raw_artifacts: list[RawArtifact], output_dir: Path) -> NormalizationResult:
        """Convert raw artifacts into abicheck-owned schema."""

    def validate(self, normalized_artifacts: list[Path]) -> ValidationResult:
        """Schema and consistency checks."""
```

`CollectionContext`:

```python
@dataclass
class CollectionContext:
    binary_paths: list[Path]
    header_roots: list[Path]
    source_root: Path | None
    build_root: Path | None
    compile_db: Path | None
    target_selectors: list[str]
    changed_files: list[Path]
    mode: Literal["baseline", "pr", "nightly", "manual"]
    allowed_actions: set[Literal["inspect", "query_build_system", "run_compiler", "run_build", "wrap_build"]]
    redaction_policy: RedactionPolicy
    cache_dir: Path
```

### D3. External CLI extractors through a manifest

External extractors can be installed independently and invoked through a
manifest:

```yaml
name: abicheck-cmake-extractor
version_command: ["abicheck-cmake-extractor", "--version"]
capabilities:
  - build_context
  - target_graph
input_requirements:
  - build_dir
allowed_actions:
  - inspect
  - query_build_system
commands:
  discover: ["abicheck-cmake-extractor", "discover", "--json"]
  collect: ["abicheck-cmake-extractor", "collect", "--output", "{raw_dir}"]
  normalize: ["abicheck-cmake-extractor", "normalize", "--raw", "{raw_dir}", "--output", "{normalized_dir}"]
outputs:
  normalized:
    - kind: build_evidence
      path: build/build_evidence.json
```

This allows third-party integrations without importing untrusted Python
into the abicheck process: the boundary is a subprocess with declared
inputs, outputs, and allowed actions.

Manifests are trusted-by-operator, never auto-discovered. abicheck does
not scan `PATH`, the working tree, or any plugin directory for manifests;
an external extractor runs only when the operator registers it explicitly
— via a config entry (e.g. `extractors:` in the project config) or a
per-run `--extractor-manifest <path>` flag. A manifest's declared
`allowed_actions` are a ceiling, not a grant: at run time they are
intersected with the actions permitted for the run (D5), so a manifest
cannot escalate beyond what the operator enabled.

### D4. Capability model

```json
{
  "capabilities": {
    "compile_db": true,
    "target_graph": true,
    "toolchain": true,
    "link_actions": false,
    "source_abi": false,
    "source_graph_summary": false,
    "call_graph": false,
    "requires_build_execution": false,
    "requires_compiler_execution": false,
    "requires_network": false
  }
}
```

Capability reporting drives evidence coverage (ADR-028 D7) and CI policy
(ADR-033).

### D5. Collection actions are explicitly permissioned

Default allowed action: `inspect` only.

| Action | Examples | Default |
|---|---|---|
| `inspect` | read existing files, parse compile DB, parse CMake File API replies | allowed |
| `query_build_system` | `ninja -t`, `bazel cquery`/`aquery`, CMake File API query regeneration | opt-in via `--allow-build-query` |
| `run_compiler` | run Clang/castxml/LibTooling syntax-only source extraction | opt-in via source replay mode (ADR-030) |
| `run_build` | `cmake --build`, `bazel build`, `make` | denied by default |
| `wrap_build` | Bear/intercept-build/compiler wrapper | denied by default |
| `network` | download tools or dependencies | always denied unless a future explicit mode |

If an extractor requests a disallowed action, collection fails with a clear
diagnostic.

### D6. Predictable raw/normalized artifact layout

```text
evidence/
  manifest.json
  raw/
    cmake-file-api/<hash>/...
    ninja/<hash>/...
    bazel-aquery/<hash>/...
    android-header-abi/<hash>/...
    kythe/<hash>/...
    codeql/<hash>/...
  normalized/
    cmake-file-api/build_facts.json
    ninja/build_facts.json
    bazel/build_facts.json
  build/build_evidence.json
  source/source_abi.json
  graph/source_graph_summary.json
  diagnostics.json
```

Raw artifact hashes include command, working directory, relevant
environment, input file hashes, extractor version, and schema version.

### D7. Redaction is mandatory

Command lines and build-system outputs can contain absolute local paths,
usernames, source checkout paths, include paths to internal SDKs, tokens in
environment variables or compiler flags, and proprietary target names.

Add a `RedactionPolicy`:

```yaml
redaction:
  path_mode: repo_relative        # repo_relative | hash_absolute | keep_absolute
  redact_env: true
  secret_patterns:
    - '(?i)token=[^\s]+'
    - '(?i)password=[^\s]+'
    - '(?i)secret=[^\s]+'
  keep_raw_artifacts: false       # default false for public CI artifacts
  keep_command_lines: normalized  # full | normalized | redacted | hash_only
```

Reports must say when evidence has been redacted and whether this reduces
reproducibility.

### D8. Validate all normalized outputs

Each normalized artifact must pass JSON schema validation and consistency
checks:

- every referenced target exists;
- every compile unit has a source path and normalized argv hash;
- every link-unit output maps to an input binary where possible;
- every source declaration has a stable ID;
- every graph edge references existing nodes;
- unknown enum values are rejected unless explicitly allowed under
  forward-compat mode.

Invalid extractor output is ignored or downgraded according to the
collection mode; it never crashes the core compare unless strict mode is
enabled.

### D9. Failure modes

| Mode | Behavior |
|---|---|
| `permissive` | Missing/failed extractors are reported as reduced coverage; the core ABI compare continues. Default for PR CI. |
| `strict` | Requested evidence must be collected and valid, otherwise the command exits non-zero. Useful for baseline generation. |
| `audit` | Preserve raw artifacts and full diagnostics for debugging extractor behavior. |

These modes affect collection only; compare exit codes keep their ADR-009
contract.

### D10. Tool version and reproducibility ledger

Every pack records:

```json
{
  "extractors": [
    {
      "name": "cmake-file-api",
      "version": "4.3.3",
      "command": "cmake-file-api-reader --...",
      "command_hash": "sha256:...",
      "capabilities": [],
      "started_at": "...",
      "finished_at": "...",
      "status": "success|partial|failed|skipped",
      "diagnostics": []
    }
  ]
}
```

This ledger is included in JSON/SARIF output (ADR-014) for traceability.

---

## Options considered

| Option | Description | Decision |
|---|---|---|
| Built-in only | Implement all extractors inside abicheck | Rejected; slow to evolve and impossible to cover every build system. |
| Arbitrary Python plugins | Maximum flexibility | Rejected for default; too much supply-chain and runtime risk. |
| External CLI adapter contract | Stable process boundary and language independence | **Accepted.** |
| Raw external formats as stable API | Store `.sdump`, `.kzip`, CodeQL DB references directly as official schema | Rejected; raw formats are unstable and/or too large (ADR-028 D4). |

---

## Consequences

### Positive

- Lets abicheck reuse existing tools without adopting their internal
  schemas.
- Keeps core verdict policy consistent and testable.
- Supports third-party adapters for specialized build systems.
- Makes security and redaction explicit instead of accidental.
- Enables gradual rollout: core adapters first, external graph plugins
  later.

### Negative / risks

- More moving parts in CI.
- External CLI adapters need packaging/version compatibility tests.
- Redaction can make reproduction harder if raw artifacts are not retained.
- Capability and confidence reporting must be accurate to avoid misleading
  users.

---

## Implementation plan

| Phase | Scope | Output |
|---|---|---|
| 1 | Define the extractor manifest and `CollectionContext` | Internal API and docs |
| 2 | Built-in compile DB extractor using the same interface | First production extractor |
| 3 | CMake/Ninja/Bazel adapters (ADR-029) | `BuildEvidence` populated from adapters |
| 4 | Raw/normalized artifact layout and schema validator | Stable evidence pack format |
| 5 | Redaction policy | Safe CI artifacts |
| 6 | External CLI adapter support | Third-party extractor path |
| 7 | Strict/permissive/audit modes | CI and baseline policy control |

---

## References

- ADR-014 — Output Format Strategy
  ([014-output-format-strategy.md](014-output-format-strategy.md))
- ADR-015 — Snapshot Serialization and Schema Versioning
  ([015-snapshot-serialization.md](015-snapshot-serialization.md))
- ADR-017 — GitHub Action Design ([017-github-action.md](017-github-action.md))
- ADR-021b — MCP Security Model ([021-mcp-security-model.md](021-mcp-security-model.md))
- ADR-028 — Evidence Pack Architecture
  ([028-source-build-evidence-pack.md](028-source-build-evidence-pack.md))


## Amendment (2026-06-12): build-tool query config (see ADR-028)

A per-project `.abicheck.yml` `build:` block (`system:` + a `query:` command, or
`--build-config`) lets abicheck recover exact ABI-affecting flags and generated
headers from a source checkout — disambiguating multi-build-system projects
(e.g. oneDAL's Make *and* Bazel). This is a direct application of the D5 action
ceiling and adds no new capability: `inspect` (read existing outputs) is the
default; running the configured query command is the opt-in `query_build_system`
action (`--allow-build-query`); `run_build`/`wrap_build` stay denied — abicheck
never performs a full project build.
