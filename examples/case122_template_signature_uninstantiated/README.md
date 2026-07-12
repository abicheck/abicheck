# Case 122 — Uninstantiated Template Signature Change (documented gap)

**Verdict:** ⚪ NO_CHANGE by default (object/DWARF/header comparison) — RISK
(`template_body_changed`) when built with `--sources` (L4 source-ABI replay).

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

## Why this case exists — the limit of *artifact* analysis

This change is invisible to abicheck's artifact-comparison modes:

- **Object / DWARF mode** — the library binary is byte-identical (no
  instantiation is emitted), so there is nothing to compare.
- **Header / castxml mode** — castxml does **not** emit uninstantiated template
  declarations into its AST output, so the signature change is not modelled.

This is the boundary of comparing *built artifacts*: code that never becomes a
symbol (uninstantiated templates, never-included inline code) leaves no trace a
binary or castxml comparison can observe.

It is **not**, however, invisible to abicheck as a whole. The L4 source-ABI
replay layer (`--sources`, ADR-030) walks the real clang AST — which *does*
emit uninstantiated `FunctionTemplateDecl` nodes — and hashes each template's
subtree, including its parameter types. That hash changes here, and the L4
diff (`abicheck/buildsource/source_diff.py`) reports it as
`TEMPLATE_BODY_CHANGED` (a RISK finding: "uninstantiated public template body
changed — invisible to artifact comparison, consumers pick up the new body on
recompile"). A pure source-AST tool that diffs the headers directly *can* see
this class of change — L4 is abicheck's own answer to that gap, opt-in because
it needs a real compile context (compile flags, include paths) rather than
just the two binaries. See
[Limitations → Source-only changes](../../docs/concepts/limitations.md) and
[Evidence and detectability](../../docs/concepts/evidence-and-detectability.md).

## Reproduce manually
```bash
g++ -shared -fPIC -g v1.cpp -o libtpl_v1.so
g++ -shared -fPIC -g v2.cpp -o libtpl_v2.so

# Default (object/header) comparison — documented gap, NO_CHANGE:
abicheck compare libtpl_v1.so libtpl_v2.so \
    --header old=v1.h --header new=v2.h   # → NO_CHANGE

# With source-ABI replay (L4) — the template signature change surfaces as RISK.
# --depth source (or --max) is required: compare's default depth collects no
# source evidence, so a raw --sources tree is otherwise ignored with a warning.
abicheck compare libtpl_v1.so libtpl_v2.so \
    --header old=v1.h --header new=v2.h \
    --sources old=<v1-source-tree> --sources new=<v2-source-tree> \
    --depth source
    # → COMPATIBLE_WITH_RISK, template_body_changed on clamp
```

## How to mitigate
For ABI-sensitive templates, ship **explicit instantiations**
(`template class Foo<int>;`) so the instantiation becomes a real symbol abicheck
can track (see `case17_template_abi`), or guard the public template API with a
source-level (header-diff) check in addition to the binary comparison.
