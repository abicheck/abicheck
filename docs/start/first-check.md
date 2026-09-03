---
doc_type: tutorial
audience:
  - library-maintainer
level: beginner
summarizes:
  - evidence-model
lifecycle: active
generated: false
---

# Run your first check

**Best first run:** compare two shared libraries with their public headers — it
gives abicheck the most evidence to work with (see
[how much evidence you need](#how-much-evidence-do-you-need) below).

The repo includes 197 ABI scenario examples. Most are single-library cases with
paired `v1`/`v2` sources and headers; the L3/L4/L5 build/source-only cases
(152–164) ship hand-built evidence-model fixture pairs; bundle/release-level
cases use release-style layouts.
Browse the generated single-library pages in the
[Examples & Case Encyclopedia](../reference/examples/index.md), or pick one and run it locally
(`examples/workflows/compare-release/` walks through the exact steps below
against a small, purpose-built project instead of a calibration case, if
you'd rather start there):

```bash
cd examples/case01_symbol_removal
```

```bash
# Build v1 and v2 shared libraries
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
```

```bash
# Compare (header-aware — needs castxml; see Requirements in Install)
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
# Verdict: BREAKING (symbol 'helper' was removed)
```

> **No `castxml`?** The command above will fail with `castxml not found`. Either
> [install castxml](install.md#requirements), or run the same comparison
> without headers by dropping the header flags — since these libraries were
> built with `-g`, abicheck still picks up their DWARF debug info and catches
> the removed symbol from that (falling back further to symbols-only only if
> no debug info is present):
>
> ```bash
> abicheck compare libv1.so libv2.so   # headerless, DWARF-aware fallback, no castxml needed
> ```

For your own library:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=include/v1/foo.h --header new=include/v2/foo.h
```

If the header is the same for both versions:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h
```

You can also pass a header **directory** (recursive scan for `*.h`, `*.hpp`, ...):

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H include/
```

If no headers are provided for ELF inputs, abicheck uses **DWARF debug info if
available** (e.g. libraries built with `-g`, like the ones above), falling back to
**symbols-only** mode only when no debug info can be found either — either way it
prints a warning, since less evidence means a weaker analysis that may miss
type/signature ABI breaks. See [How much evidence do you need?](#how-much-evidence-do-you-need)
below and [Evidence & Detectability](../learn/evidence-and-detectability.md) for the
full L0–L5 model.

## How much evidence do you need?

Binary-only detects exported-symbol changes (add/remove, SONAME, visibility).
Adding debug info catches layout and calling-convention breaks; adding headers
adds the full public API surface and scopes out internal types; adding build
and source context catches the facts that never reach the binary at all
(macros, default-argument values, uninstantiated templates). Each source is
additive — more evidence only ever finds more, never hides an artifact-proven
break. Run `abicheck dump libfoo.so --dry-run` to see which layers abicheck
found for a binary. For the full model, the exact `L0`–`L5` layer table, and a
worked example, see [Evidence & Detectability](../learn/evidence-and-detectability.md)
and [What Each Level Sees](../learn/what-each-level-sees.md).

## Next

➡️ **[Understand your first report](first-report.md)**
