# case145 — Unversioned export under a versioning scheme (audit, pure L0)

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Cross-check:** `unversioned_exported_symbol` ·
**Mode:** single-release audit · **Evidence tier:** L0

## What it demonstrates

The library defines a symbol-versioning scheme (`DEMO_1.0` in `.gnu.version_d`),
but a newly added export — `demo_experimental` — ships with **no version node**.
An unversioned symbol under a versioned library cannot be evolved compatibly
later: consumers bind the bare name with no version guarantee.

## Why it is a cross-check, not a plain diff

This is read from a **single artifact** by comparing two parts of the same
binary against each other — the export table vs the `.gnu.version_d` scheme:

| Source | What it sees alone |
|--------|--------------------|
| Export table | three exported symbols |
| `.gnu.version_d` | a `DEMO_1.0` scheme exists |
| **Combination** | one export sits outside the scheme → `UNVERSIONED_EXPORTED_SYMBOL` |

Pure L0 — no DWARF, no headers, no baseline.

## Reproduce

```bash
abicheck scan --binary libdemo.so --audit
```

## Fix

Add `demo_experimental` to the version script (`DEMO_1.1 { global: demo_experimental; }`)
or, if it is not public API, move it to the script's `local:` block.
