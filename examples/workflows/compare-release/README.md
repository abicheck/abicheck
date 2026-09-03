# Workflow: compare two builds of one library

**Task:** "Did my next release break anything for existing consumers?"

This is the first Phase 5 slice of the [examples/catalog split]
(../../../docs/contribute/plans/examples-catalog-split.md) — a small, curated,
task-oriented example independent of the 197-case calibration catalog under
`examples/case*/` (which exists to calibrate detectors, not to teach the
CLI). See that plan's "Known gaps" table for the rest of this curated set,
not yet built.

## The project

A tiny shared library, `mathutils`, ships two releases:

| | v1 | v2 |
|---|---|---|
| `mathutils.h` | `add`, `subtract`, `multiply` | `add`, `multiply` — **`subtract` removed** |

```text
v1/mathutils.h   v1/mathutils.c
v2/mathutils.h   v2/mathutils.c
```

## Run it

```bash
cd examples/workflows/compare-release

# Build both releases as shared libraries
gcc -shared -fPIC -g v1/mathutils.c -o libmathutils_v1.so
gcc -shared -fPIC -g v2/mathutils.c -o libmathutils_v2.so

# Compare, giving abicheck each side's public header for the strongest evidence
abicheck compare libmathutils_v1.so libmathutils_v2.so \
    --header old=v1/mathutils.h --header new=v2/mathutils.h
```

## What you get

The default output is a full Markdown ABI report (evidence coverage, release
recommendation, every finding). The parts that answer the task:

```text
| **Verdict** | ❌ `BREAKING` |
| Breaking changes | 1 |
...
- **func_removed**: Public function removed: subtract (`subtract`) — v1/mathutils.h:5
  > Old binaries call a symbol that no longer exists; dynamic linker will
    refuse to load or crash at call site.
```

Exit code is `4` (ABI break) — see [Exit Codes](../../../docs/reference/exit-codes.md).

## Next steps

- No `castxml` installed? Drop both `--header` flags — since both libraries
  were built with `-g`, abicheck still catches the removed symbol from DWARF
  debug info alone (a weaker evidence tier; see
  [Evidence & Detectability](../../../docs/learn/evidence-and-detectability.md)).
- Want machine-readable output for CI? Add `--format json` or `--format sarif`
  — see [Output Formats](../../../docs/use/output-formats.md).
- Want this gating a pull request automatically? See
  [the GitHub Action](../../../docs/use/github-action.md).
