---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: beginner
summarizes:
  - baseline-lifecycle
lifecycle: active
generated: false
---

# Creating and Comparing a Baseline

The practical mechanics of producing an ABI baseline and comparing against
one. For what a baseline *is* and why most projects need two of them, see
[Baseline Management](baseline-management.md); for where to keep the file
once you've created it, see [Storing Baselines](baseline-storage.md).

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

See [Storing Baselines](baseline-storage.md) for where to put the file once
you've created it, including the `baseline` Action for multi-library releases.

## Comparing Against a Baseline

Once you have a baseline, comparison is the same regardless of storage:

```bash
# JSON snapshot vs new binary
abicheck compare baseline.json build/libfoo.so --header new=include/foo.h

# Two snapshots (no headers or tools needed)
abicheck compare old-baseline.json new-baseline.json
```

Snapshots are self-contained — they include all type, function, variable, and enum
information. Comparing two snapshots requires no headers, compilers, or debug info.

You can also pass **packages** (RPM, Deb, tar, conda, or wheel) straight to
`compare` — it compares all shared libraries inside them without manual
extraction:

```bash
abicheck compare libfoo-1.0.rpm libfoo-1.1.rpm
```

See [Multi-Binary Releases](multi-binary.md) for the bundle/package flags and the
[GitHub Action](github-action.md) guide for CI examples with packages.

### `scan --against` for a one-off comparison

`abicheck scan ARTIFACT` doesn't require a stored baseline at all — pass
`--against` with a previous native library or saved ABI dump (a single file,
not a directory or package -- for those use
`abicheck compare OLD_PACKAGE NEW_PACKAGE`) to compare against, and `scan`
runs its always-on audit checks plus that comparison in one pass:

```bash
abicheck scan new/libfoo.so --header new/include --against old/libfoo.so
```

`-H`/`--header` and `-I`/`--include` are side-aware: a bare value applies to
both `ARTIFACT` and the `--against` side, and an `old=`/`new=` prefix scopes
to one side. When `--against` is a **native library** (not a snapshot) and
its public headers differ from the new version, parse the old side with
**its own** headers using the `old=` prefix:

```bash
abicheck scan new/libfoo.so --against old/libfoo.so \
  --header new=new/include --header old=old/include
```

- Without an `old=`-scoped header, a native `--against` library is parsed
  with the same headers as `ARTIFACT` (correct only when the headers didn't
  change).
- A **JSON-snapshot** `--against` target already has its headers baked in, so
  side-scoped headers are unnecessary there. Prefer a pre-dumped snapshot
  baseline when you can — it's unambiguous and needs no toolchain at compare
  time.

See [Source-Scan Depth](scan-levels.md) for the full `scan` flag reference.
