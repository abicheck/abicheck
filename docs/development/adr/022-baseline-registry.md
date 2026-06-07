# ADR-022: Baseline Registry and Snapshot Distribution

**Date:** 2026-03-23
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

### Current baseline workflow

abicheck produces JSON snapshots via `abicheck dump`:

```bash
abicheck dump libfoo.so -H include/ -o baseline-v1.0.json
abicheck compare baseline-v1.0.json libfoo-new.so -H include/
```

Snapshots are versioned (`schema_version=3`, ADR-015), JSON-serializable, and
interchangeable between DWARF-derived and castxml-derived modes (ADR-003). The
comparison engine consumes snapshots identically regardless of origin.

### What's missing

**1. No standard storage/retrieval mechanism.**

Teams store baselines ad-hoc: checked into git, uploaded to S3, attached to
releases, or generated fresh each CI run. There's no `abicheck pull-baseline`
or standard location convention.

**2. No branch/release/tag mapping.**

A project typically has multiple active baselines: `main`, `release/1.x`,
`release/2.x`. There's no mechanism to associate a snapshot with a branch
or release, or to select the correct baseline for a given comparison.

**3. No integrity verification.**

Snapshots are plain JSON. There's no signature, checksum, or provenance
metadata to verify that a baseline hasn't been tampered with or that it
was produced by a specific version of abicheck.

**4. No lifecycle management.**

Baselines accumulate. Old release baselines may be irrelevant. There's no
retention policy, no way to list available baselines, no cleanup mechanism.

### Design constraints

- Must work for open-source projects (no paid infrastructure required)
- Must work offline and in air-gapped environments
- Must not require a custom server component
- Should leverage existing infrastructure (git, OCI registries, S3/GCS)
- Must be optional — abicheck should work without a registry

### Options considered

| Option | Description | Trade-off |
|--------|-------------|-----------|
| A: Git-native (branch/tag-based) | Store baselines in a dedicated git branch or as release artifacts | Works everywhere git works; no extra infra; but limited by git's blob handling |
| B: OCI registry (ORAS) | Store baselines as OCI artifacts | Modern, standard, but requires OCI-compatible registry |
| C: S3/GCS/Azure Blob | Object storage with prefix conventions | Scalable, but cloud-specific |
| **D: Pluggable with git-native default** | Registry protocol with multiple backends; git as default | Extensible; works offline; cloud backends optional |

---

## Decision

### 1. Registry protocol with pluggable backends

```python
class BaselineRegistry(Protocol):
    """Store and retrieve ABI baseline snapshots."""

    def push(self, key: BaselineKey, snapshot: AbiSnapshot, metadata: BaselineMetadata) -> str:
        """Store a snapshot. Returns a reference ID."""
        ...

    def pull(self, key: BaselineKey) -> tuple[AbiSnapshot, BaselineMetadata] | None:
        """Retrieve a snapshot by key. Returns None if not found."""
        ...

    def list(self, prefix: str | None = None) -> list[BaselineKey]:
        """List available baselines, optionally filtered by prefix."""
        ...

    def delete(self, key: BaselineKey) -> bool:
        """Delete a baseline. Returns True if deleted, False if not found."""
        ...

@dataclass
class BaselineKey:
    """Unique identifier for a baseline snapshot."""
    library: str           # Library name (e.g., "libfoo")
    version: str           # Version or branch (e.g., "1.0.0", "main")
    platform: str          # Target platform (e.g., "linux-x86_64")
    variant: str = ""      # Build variant (e.g., "debug", "ssl-enabled")

    @property
    def path(self) -> str:
        """Registry path: library/version/platform[/variant]"""
        parts = [self.library, self.version, self.platform]
        if self.variant:
            parts.append(self.variant)
        return "/".join(parts)

@dataclass
class BaselineMetadata:
    """Provenance and integrity metadata for a baseline."""
    abicheck_version: str           # Version of abicheck that produced the snapshot
    schema_version: int             # Snapshot schema version (ADR-015)
    created_at: datetime            # ISO 8601 timestamp
    build_context_hash: str | None  # Hash of compile_commands.json / flags used (ADR-020a)
    git_commit: str | None          # Source commit that produced the library
    checksum: str                   # SHA-256 of the serialized snapshot JSON
    signature: str | None           # Optional detached signature (GPG/sigstore)
```

### 2. Git-native backend (default)

The simplest backend stores baselines in a dedicated git branch:

```text
Branch: abicheck/baselines

abicheck/baselines/
├── libfoo/
│   ├── 1.0.0/
│   │   └── linux-x86_64/
│   │       ├── snapshot.json
│   │       └── metadata.json
│   ├── 1.1.0/
│   │   └── linux-x86_64/
│   │       ├── snapshot.json
│   │       └── metadata.json
│   └── main/
│       └── linux-x86_64/
│           ├── snapshot.json
│           └── metadata.json
└── libbar/
    └── 2.0.0/
        └── linux-x86_64/
            ├── snapshot.json
            └── metadata.json
```

Operations:

```bash
# Push baseline (creates/updates entry on abicheck/baselines branch)
abicheck baseline push libfoo --version 1.0.0 --platform linux-x86_64 \
    --snapshot baseline.json

# Pull baseline
abicheck baseline pull libfoo --version 1.0.0 --platform linux-x86_64 \
    -o baseline.json

# Compare against registry baseline
abicheck compare \
    --baseline libfoo:1.0.0:linux-x86_64 \
    libfoo-new.so -H include/

# List baselines
abicheck baseline list libfoo
# Output:
#   libfoo/1.0.0/linux-x86_64   2026-03-01  abicheck-0.2.0  abc1234
#   libfoo/1.1.0/linux-x86_64   2026-03-15  abicheck-0.2.0  def5678
#   libfoo/main/linux-x86_64    2026-03-23  abicheck-0.2.0  ghi9012

# Delete old baseline
abicheck baseline delete libfoo --version 0.9.0 --platform linux-x86_64

# Auto-detect: push baseline for current branch
abicheck baseline push libfoo --auto
# Detects: version from git tag or branch, platform from binary
```

Implementation: uses `git worktree` or `git checkout --orphan` to manipulate the
baselines branch without affecting the working tree. Commits are atomic.

### 3. OCI backend (optional)

For teams using container registries, store baselines as OCI artifacts via ORAS
conventions:

```bash
abicheck baseline push libfoo --version 1.0.0 --platform linux-x86_64 \
    --snapshot baseline.json \
    --registry oci://ghcr.io/myorg/abi-baselines

abicheck baseline pull libfoo --version 1.0.0 --platform linux-x86_64 \
    --registry oci://ghcr.io/myorg/abi-baselines \
    -o baseline.json
```

Media type: `application/vnd.abicheck.snapshot.v3+json`

### 4. Filesystem backend (air-gapped / simple)

Plain directory structure on local or network filesystem:

```bash
abicheck baseline push libfoo --version 1.0.0 \
    --snapshot baseline.json \
    --registry file:///shared/abi-baselines

abicheck baseline pull libfoo --version 1.0.0 \
    --registry file:///shared/abi-baselines \
    -o baseline.json
```

### 5. Integrity verification

Every pushed baseline includes a SHA-256 checksum in its metadata. On pull, the
checksum is verified before the snapshot is used.

Optional GPG or sigstore signing:

```bash
# Push with signing
abicheck baseline push libfoo --version 1.0.0 \
    --snapshot baseline.json --sign

# Pull with verification
abicheck baseline pull libfoo --version 1.0.0 \
    -o baseline.json --verify
```

### 6. CI integration

Typical CI workflow:

```yaml
# .github/workflows/abi-check.yml
jobs:
  abi-check:
    steps:
      - uses: actions/checkout@v4

      - name: Build
        run: cmake --build build/

      - name: ABI check against baseline
        uses: abicheck/action@v1
        with:
          mode: compare
          baseline: "libfoo:latest-release:linux-x86_64"
          new-binary: build/libfoo.so
          headers: include/

      - name: Update baseline (on release)
        if: startsWith(github.ref, 'refs/tags/v')
        run: |
          abicheck baseline push libfoo \
            --version ${{ github.ref_name }} \
            --platform linux-x86_64 \
            --snapshot build/abi-snapshot.json
```

### 7. Registry configuration

```yaml
# .abicheck.yml (project root)
registry:
  backend: git           # "git", "oci", "filesystem"
  # OCI-specific:
  # url: oci://ghcr.io/myorg/abi-baselines
  # Filesystem-specific:
  # path: /shared/abi-baselines

baselines:
  auto_platform: true    # Detect platform from binary
  auto_version: true     # Detect version from git tag/branch
  retention:
    keep_releases: 10    # Keep last N release baselines
    keep_branches: 5     # Keep last N branch baselines
    max_age_days: 365    # Delete baselines older than this
```

---

## Consequences

### Positive
- Standard workflow for baseline storage and retrieval
- Git-native default requires no extra infrastructure
- Pluggable backends support diverse environments (cloud, air-gapped, container registries)
- Integrity verification catches tampering or corruption
- Branch/version/platform addressing enables multi-target projects
- CI integration via `--baseline` flag simplifies workflows
- Retention policy prevents baseline accumulation

### Negative
- Git-native backend adds commits to the repository (on a separate branch)
- Large snapshots in git may bloat repository over time (mitigated by retention)
- OCI backend adds optional dependency on ORAS client library
- Signing infrastructure (GPG keys, sigstore) adds operational complexity
- Multiple backends mean more code to maintain and test
- Registry configuration file is a new concept to learn

---

## Implementation Plan

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | `BaselineRegistry` protocol + `BaselineKey` + `BaselineMetadata` models | 2-3 days |
| 2 | Filesystem backend (simplest, for testing and air-gapped) | 2-3 days |
| 3 | Git-native backend (orphan branch, atomic commits) | 3-5 days |
| 4 | CLI: `abicheck baseline push/pull/list/delete` commands | 2-3 days |
| 5 | `--baseline` flag for `compare` command (pull + compare) | 1-2 days |
| 6 | Integrity: SHA-256 checksum generation and verification | 1 day |
| 7 | `.abicheck.yml` configuration file support | 2-3 days |
| 8 | Auto-detection (version from git, platform from binary) | 1-2 days |
| 9 | OCI backend (optional, via ORAS) | 3-5 days |
| 10 | Signing support (GPG / sigstore, optional) | 3-5 days |
| 11 | Retention policy + `baseline gc` command | 2-3 days |
| 12 | GitHub Action integration updates | 1-2 days |
