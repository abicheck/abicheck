# Local Build Comparison & Snapshot Workflow

This guide covers how to compare locally built libraries against baselines
using `abicheck`, and how to use snapshots for fast offline CI.

---

## Overview

abicheck supports comparing libraries using three input types:

| Input type | Example | Description |
|------------|---------|-------------|
| Binary (`.so` / `.dll` / `.dylib`) | `build/libfoo.so` | Direct library comparison |
| JSON snapshot | `baseline.json` | Pre-saved ABI snapshot (from `abicheck dump`) |
| ABICC Perl dump | `old_dump.abi.tar.gz` | Legacy ABICC dump (auto-detected) |

---

## Workflow 1: One-off — Compare Local Build vs Baseline

The simplest pattern: compare your new build against a saved baseline.

```bash
abicheck compare baseline.json build/libfoo.so \
  --new-header include/foo.h
```

Expected output:
```text
Verdict: NO_CHANGE
```

**Exit codes:**

| Code | Meaning | CI action |
|------|---------|-----------|
| `0` | Compatible / no change | Pass |
| `2` | API break (source-level) | Pass or fail (configurable) |
| `4` | Binary ABI break | Fail |

**Also works with two binaries directly:**
```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header include/v1/foo.h --new-header include/v2/foo.h
```

---

## Workflow 2: Pre-snapshot + Compare (Offline / Fast CI)

For teams that want **reproducible baselines** stored in git or an artifact registry,
and don't want CI to rebuild or re-dump on every PR.

### Step 1: Create a baseline snapshot (do this at release time)

```bash
abicheck dump libfoo.so -H include/foo.h --version 2.0.0 -o baseline.json
```

Output:
```
Snapshot saved: baseline.json
```

### Step 2: Compare local build against snapshot (fast, no network)

```bash
abicheck compare baseline.json build/libfoo.so \
  --new-header include/foo.h
```

This is **instant** — no re-dump of the baseline side. Only the new binary is processed.

---

## Workflow 3: Compare Two Snapshots

```bash
abicheck compare old-baseline.json new-baseline.json
```

Both sides use pre-saved snapshots. No headers or compilation toolchain needed.

---

## Snapshot File Format

`abicheck dump` produces a JSON file containing the full ABI surface:

```json
{
  "schema_version": 3,
  "library": "libfoo.so.1",
  "version": "2.0.0",
  "platform": "elf",
  "functions": [...],
  "variables": [...],
  "types": [...],
  "enums": [...]
}
```

Key properties:
- **Self-contained**: includes all type, function, and variable information
- **Platform-agnostic**: snapshots from ELF, PE, and Mach-O binaries use the same schema
- **Deterministic**: sets are serialized as sorted lists for reproducible output

---

## Recommended CI Integration Pattern

### Release job: save baseline

```yaml
# .github/workflows/abi-baseline.yml
name: ABI Baseline
on:
  release:
    types: [published]

jobs:
  save-baseline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build library
        run: mkdir build && cd build && cmake .. && make

      - name: Dump ABI baseline
        uses: napetrov/abicheck@v0.3.0
        with:
          mode: dump
          new-library: build/libfoo.so
          header: include/foo.h
          new-version: ${{ github.ref_name }}
          output-file: abi-baseline.json

      - name: Upload baseline as release asset
        uses: softprops/action-gh-release@v2
        with:
          files: abi-baseline.json
```

### PR job: compare against baseline

```yaml
# .github/workflows/abi-check.yml
name: ABI Compatibility Check
on: [pull_request]

jobs:
  abi-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Download baseline from latest release
        run: gh release download --pattern 'abi-baseline.json' --dir .
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Build library
        run: mkdir build && cd build && cmake .. && make

      - name: ABI check vs baseline
        uses: napetrov/abicheck@v0.3.0
        with:
          old-library: abi-baseline.json
          new-library: build/libfoo.so
          new-header: include/foo.h
```

**Why this pattern works well:**

- No network dependency in PR jobs (baseline is a release asset)
- Snapshot is versioned and reproducible
- Baseline can be audited (JSON is human-readable)
- Teams can store snapshots in artifact registries (S3, GitHub Releases, etc.)
- Works in air-gapped environments after initial snapshot

---

## Compare Packages Directly

For RPM, Deb, tar, conda, or wheel packages, use `compare-release` mode
to compare all shared libraries inside two packages without manual extraction:

```bash
abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm
```

See the [GitHub Action](github-action.md) guide for CI examples with packages.

---

## See Also

- [Getting Started](../getting-started.md) — Installation and first check
- [CLI Usage](cli-usage.md) — Full CLI reference
- [GitHub Action](github-action.md) — CI integration with the GitHub Action
- [ABICC Parity Status](../development/abicc-parity-status.md) — Coverage analysis vs ABICC/libabigail
