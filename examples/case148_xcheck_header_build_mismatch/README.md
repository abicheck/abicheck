# case148 — Header build-context mismatch (cross-source flagship)

**Verdict:** 🟠 API_BREAK · **Cross-check:** `header_build_context_mismatch` ·
**Mode:** single-release audit · **Evidence tier:** L3

## What it demonstrates

The clearest case that **combining two sources beats either alone**. The public
headers were parsed *without* the build's ABI-relevant flags
(`glibcxx_use_cxx11_abi`, `-DBIG_BUFFERS`). The layout abicheck recorded from the
context-free header parse is therefore **wrong** — but nothing in the header
text or the binary says so.

## Why no single source sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | a valid layout — blind to which macros produced it |
| Header AST (L2) | a layout parsed **without** `-DBIG_BUFFERS` → the *wrong* layout, reported with full confidence |
| Build flags (L3) | the project compiled the TU **with** `-DBIG_BUFFERS=1` |
| **Combination** | L2 macros ↔ L3 flags disagree → `HEADER_BUILD_CONTEXT_MISMATCH` (API_BREAK) |

Only crosschecking the L2 macro context against the L3 compile flags exposes the
divergence. This is why a clean "no change" from a context-free header parse can
be actively misleading.

## Reproduce

```bash
abicheck scan --binary libdemo.so -H include/ --build-info build/ --audit   # build/ holds compile_commands.json
```

## Fix

Parse the public headers with the same ABI-relevant flags the build uses (feed
abicheck the compile DB), so the recorded layout matches what ships.
