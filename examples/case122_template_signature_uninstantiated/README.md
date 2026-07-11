# Case 122 — Uninstantiated Template Signature Change (documented gap)

**Verdict:** ⚪ NO_CHANGE (abicheck — documented limitation)
**The real change is a source/API break that abicheck cannot detect in any mode.**

## What changes

| Version | Declaration |
|---------|-------------|
| v1 | `template <typename T> T clamp(T value, int lo, int hi);` |
| v2 | `template <typename T> T clamp(T value, long lo, long hi);` |

The function template's parameter types change (`int` → `long`). The library
**instantiates nothing** — it ships the template header-only and exports only an
ordinary `library_version()` function.

## What breaks

A consumer writing `clamp<int>(x, a, b)` resolves the call against the new
parameter types and emits a *different* mangled symbol on its own side; overload
resolution and deduction can also change. For users of the template this is a
real source/ABI break.

## Why this case exists — the hard limit of binary analysis

This change is invisible to **every** abicheck mode:

- **Object / DWARF mode** — the library binary is byte-identical (no
  instantiation is emitted), so there is nothing to compare.
- **Header / castxml mode** — castxml does **not** emit uninstantiated template
  declarations into its AST output, so the signature change is not modelled.

This is the fundamental boundary of comparing *built artifacts*: code that never
becomes a symbol (uninstantiated templates, never-included inline code) leaves no
trace a binary or castxml comparison can observe. A pure source-AST tool that
diffs the headers directly (e.g. a Clang-based source comparator) *can* see it —
the two approaches are complementary. See
[Limitations → Source-only changes](../../docs/concepts/limitations.md).

## Reproduce manually
```bash
g++ -shared -fPIC -g v1.cpp -o libtpl_v1.so
g++ -shared -fPIC -g v2.cpp -o libtpl_v2.so
abicheck compare libtpl_v1.so libtpl_v2.so \
    --header old=v1.h --header new=v2.h   # → NO_CHANGE (documented gap)
```

## How to mitigate
For ABI-sensitive templates, ship **explicit instantiations**
(`template class Foo<int>;`) so the instantiation becomes a real symbol abicheck
can track (see `case17_template_abi`), or guard the public template API with a
source-level (header-diff) check in addition to the binary comparison.
