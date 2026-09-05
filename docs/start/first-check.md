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

Start with the `compare-release` **workflow example** — a small,
purpose-built library (`mathutils`) that ships two releases, the second of
which quietly drops an exported function:

```bash
cd examples/workflows/compare-release
```

```bash
# Build both releases as shared libraries
gcc -shared -fPIC -g v1/mathutils.c -o libmathutils_v1.so
gcc -shared -fPIC -g v2/mathutils.c -o libmathutils_v2.so
```

```bash
# Compare, giving abicheck each side's public header for the strongest evidence
abicheck compare libmathutils_v1.so libmathutils_v2.so \
    --header old=v1/mathutils.h --header new=v2/mathutils.h
# Verdict: BREAKING (func_removed: subtract)
```

`examples/workflows/compare-release/README.md` walks through the same run in
more detail, and CI executes those exact commands on every change
(`validation/scripts/run_workflow_examples.py`), so what you read is what
runs.

> **Looking for a catalogue rather than a tutorial?** The repository also
> carries 197 calibration cases under `examples/case*/` — one per
> compatibility mechanism, used to calibrate the detectors rather than to
> teach the CLI. Browse them in the
> [Compatibility Catalog](../reference/examples/index.md), which indexes them
> by rule, scenario kind, ecosystem, operation, evidence level, language,
> and verdict.

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
