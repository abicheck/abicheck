# case146 — RTTI exported for an internal type (single-release audit)

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Cross-check:** `rtti_for_internal_type` ·
**Mode:** single-release audit · **Evidence tier:** L2

## What it demonstrates

`_ZTI12InternalNode` / `_ZTV12InternalNode` (typeinfo + vtable) are exported for
`InternalNode`, a polymorphic type declared **only in a private header**.
Consumers cannot name the type, yet its run-time type information is on the ABI
surface — bloating the export set and risking cross-module `dynamic_cast`
coupling to an internal class.

## Why no single source sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary export table (L0) | `_ZTI`/`_ZTV` symbols — could belong to any class |
| Public-header AST (L2) | `InternalNode` originates in a **private** header |
| **Combination** | exported RTTI maps to a private-header type → `RTTI_FOR_INTERNAL_TYPE` |

## Reproduce

```bash
abicheck scan --binary libdemo.so -H include/ --audit
```

## Fix

Anchor the vtable in a translation unit and hide the RTTI (a key-function +
hidden visibility on the internal class), or promote `InternalNode` to a public,
installed header if it is genuinely part of the API.
