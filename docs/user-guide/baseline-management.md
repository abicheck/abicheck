# Baseline Management

ABI baselines are pre-computed snapshots of a library's ABI surface at a known-good
point (typically a release). Comparing future builds against a baseline detects
breaking changes before they ship.

## Creating a Baseline

```bash
# Basic: write to stdout
abicheck dump libfoo.so -H include/foo.h --version 2.0.0

# Write to a specific file
abicheck dump libfoo.so -H include/foo.h --version 2.0.0 -o baseline.json

# Conventionally named (see naming convention below)
abicheck dump libfoo.so -H include/foo.h --version 2.0.0 -o libfoo-2.0.0.abicheck.json
```

### Provenance Metadata

Snapshots include provenance metadata that tracks where and when they were created:

```bash
abicheck dump libfoo.so -H include/foo.h \
  --version 2.0.0 \
  --git-tag v2.0.0 \
  --build-id "$CI_RUN_ID" \
  -o libfoo-2.0.0.abicheck.json
```

This embeds in the snapshot JSON:

| Field | Source | Example |
|-------|--------|---------|
| `git_commit` | Auto-detected from `git rev-parse HEAD` | `abc1234def5678` |
| `git_tag` | `--git-tag` flag | `v2.0.0` |
| `created_at` | Auto-set (ISO 8601 UTC) | `2026-03-24T12:00:00+00:00` |
| `build_id` | `--build-id` flag | `gh-actions-1234` |

Use `--no-git` to skip automatic git commit detection (e.g., in non-git environments).

### The `.abicheck.json` Naming Convention

Name baselines `<library>-<version>.abicheck.json` (via `-o`):

| Library | Version | Output File |
|---------|---------|-------------|
| `libfoo.so.1` | `2.0.0` | `libfoo-2.0.0.abicheck.json` |
| `bar.dll` | `3.1` | `bar-3.1.abicheck.json` |
| `libqux.dylib` | `1.0` | `libqux-1.0.abicheck.json` |

This convention makes CI scripts predictable: upload with `*.abicheck.json`, download
with `--pattern '*.abicheck.json'`. The GitHub Action's `abi-baseline` input looks for
`*.abicheck.json` assets on releases.

## Storage Patterns

abicheck does not mandate where baselines are stored. Choose the pattern that fits
your team:

### Recipe A: GitHub Releases (Recommended)

Best for: open-source libraries, public API contracts.

**Release workflow** (runs when a release is published):

```yaml
name: ABI Baseline
on:
  release:
    types: [published]

jobs:
  baseline:
    runs-on: ubuntu-latest
    permissions:
      contents: write   # needed for release asset upload
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: make

      - name: Dump ABI baseline
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: dump
          new-library: build/libfoo.so
          new-header: include/foo.h
          new-version: ${{ github.ref_name }}
          output-file: libfoo-${{ github.ref_name }}.abicheck.json

      - name: Upload baseline to release
        run: gh release upload ${{ github.ref_name }} libfoo-*.abicheck.json --clobber
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

**PR workflow** (compares against latest release baseline):

```yaml
name: ABI Check
on: pull_request

jobs:
  abi:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: make

      - name: ABI compatibility check
        uses: napetrov/abicheck@v0.3.0
        with:
          abi-baseline: latest-release
          new-library: build/libfoo.so
          new-header: include/foo.h
```

The `abi-baseline: latest-release` input automatically downloads the `*.abicheck.json`
asset from the latest GitHub Release and uses it as the old library.

To pin to a specific release:

```yaml
      - name: ABI compatibility check
        uses: napetrov/abicheck@v0.3.0
        with:
          abi-baseline: v2.0.0
          new-library: build/libfoo.so
          new-header: include/foo.h
```

**CLI equivalent** (requires the `gh` CLI and `GH_TOKEN`):

```bash
abicheck dump libfoo.so -H include/foo.h \
  --version 2.0.0 --git-tag v2.0.0 \
  -o libfoo-2.0.0.abicheck.json
gh release upload v2.0.0 libfoo-2.0.0.abicheck.json --clobber
```

### Recipe B: Git-Committed Baselines

Best for: small libraries where you want baselines auditable in PR diffs.

```bash
# Developer or release CI creates/updates the baseline
abicheck dump libfoo.so -H include/foo.h \
  --version 2.0.0 -o abi/libfoo.abicheck.json
git add abi/libfoo.abicheck.json
git commit -m "Update ABI baseline for v2.0.0"
git push
```

**PR workflow:**

```yaml
      - name: ABI compatibility check
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi/libfoo.abicheck.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

No download step needed — the baseline file is in the repo.

### Recipe C: GitHub Actions Cache

Best for: ephemeral, branch-scoped comparisons (e.g., comparing HEAD~1 vs HEAD).

```yaml
      - uses: actions/cache@v4
        with:
          path: abi-baseline.json
          key: abi-baseline-${{ github.event.repository.default_branch }}-${{ github.sha }}
          restore-keys: |
            abi-baseline-${{ github.event.repository.default_branch }}-
```

### Recipe D: External Artifact Store (S3, Artifactory, GCS)

Best for: large binaries, private repos, retention policies.

```yaml
      # Release workflow
      - name: Upload baseline to S3
        run: aws s3 cp libfoo-2.0.0.abicheck.json s3://my-bucket/abi-baselines/

      # PR workflow
      - name: Download baseline from S3
        run: aws s3 cp s3://my-bucket/abi-baselines/libfoo-2.0.0.abicheck.json baseline.json

      - name: ABI check
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

## Baseline Registry

For structured baseline management with version/platform addressing and integrity
verification, use the built-in baseline registry:

```bash
# Push a baseline after a release build
abicheck dump build/libfoo.so -H include/ -o snapshot.json
abicheck baseline push libfoo \
    --version 1.0.0 \
    --platform linux-x86_64 \
    --snapshot snapshot.json

# Pull a baseline by key and compare
abicheck baseline pull libfoo:1.0.0:linux-x86_64 -o baseline.json
abicheck compare baseline.json build/libfoo.so -H include/

# List all baselines
abicheck baseline list

# Delete an old baseline
abicheck baseline delete libfoo:0.9.0:linux-x86_64
```

### Registry commands

| Command | Description |
|---------|-------------|
| `abicheck baseline push <library>` | Store a baseline snapshot |
| `abicheck baseline pull <spec>` | Retrieve a baseline by key |
| `abicheck baseline list [prefix]` | List available baselines |
| `abicheck baseline delete <spec>` | Delete a baseline |

The spec format is `library:version:platform[:variant]`.

### Registry options

| Flag | Description |
|------|-------------|
| `--version` | Version string (e.g., `1.0.0`, `main`) |
| `--platform` | Target platform (e.g., `linux-x86_64`, `macos-arm64`) |
| `--variant` | Build variant (e.g., `ssl-enabled`, `debug`) |
| `--snapshot` | Path to the ABI snapshot JSON file |
| `--registry` | Registry directory (default: `.abicheck/baselines/`) |
| `--auto-platform` | Auto-detect platform from the snapshot's binary |
| `--git-commit` | Source commit SHA to record in metadata |

### Storage layout

Baselines are stored as plain files in a directory tree:

```text
.abicheck/baselines/
├── libfoo/
│   ├── 1.0.0/
│   │   └── linux-x86_64/
│   │       ├── snapshot.json    # ABI snapshot
│   │       └── metadata.json    # Provenance + checksum
│   └── 2.0.0/
│       └── linux-x86_64/
│           ├── snapshot.json
│           └── metadata.json
└── libbar/
    └── 1.0.0/
        └── linux-x86_64/
            ├── snapshot.json
            └── metadata.json
```

### Integrity verification

Each pushed baseline includes a SHA-256 checksum in `metadata.json`. On pull,
the checksum is verified and a `BaselineIntegrityError` is raised if the
snapshot has been modified. Metadata also records:

- abicheck version that produced the snapshot
- Timestamp of creation
- Optional git commit SHA
- Optional build-context hash (when `-p` was used)

### Example: CI workflow with the registry

```yaml
jobs:
  abi-check:
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: cmake --build build/

      - name: Dump ABI snapshot
        run: abicheck dump build/libfoo.so -H include/ -o snapshot.json

      - name: Pull baseline and compare
        run: |
          abicheck baseline pull libfoo:latest:linux-x86_64 -o baseline.json \
              --registry .abicheck/baselines
          abicheck compare baseline.json snapshot.json --format sarif -o abi.sarif

      - name: Update baseline (on release tag)
        if: startsWith(github.ref, 'refs/tags/v')
        run: |
          abicheck baseline push libfoo \
            --version ${{ github.ref_name }} \
            --platform linux-x86_64 \
            --snapshot snapshot.json \
            --git-commit ${{ github.sha }}
```

## Comparing Against a Baseline

Once you have a baseline, comparison is the same regardless of storage:

```bash
# JSON snapshot vs new binary
abicheck compare baseline.json build/libfoo.so --new-header include/foo.h

# Two snapshots (no headers or tools needed)
abicheck compare old-baseline.json new-baseline.json
```

Snapshots are self-contained — they include all type, function, variable, and enum
information. Comparing two snapshots requires no headers, compilers, or debug info.
