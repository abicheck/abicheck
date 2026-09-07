# Case 205: Public Function Marked Deprecated

**Category:** Quality | **Verdict:** ✅ COMPATIBLE

## Verdict and consumer impact

`legacy_open()` gains `__attribute__((deprecated))`. The symbol is still
exported with an unchanged signature, so every prebuilt consumer keeps
linking and running and every existing call site still compiles. What
changed is a *scheduling* statement: the library has announced that this
entry point is on its way out, and a consumer recompiling against v2 now
gets a warning at each call site. Surfacing that in a release report is the
whole point — a deprecation nobody notices is a removal nobody is ready for.

This is the **positive control** for the deprecation-attribute mechanism.
Its negative control is
[`case206_deprecation_documented_without_attribute`](../case206_deprecation_documented_without_attribute/README.md),
which announces the same intent in a prose comment the compiler never sees.
The pair proves two things about these two fixtures: the attribute is
reported, and a comment is not mistaken for one.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `int legacy_open(const char *name);` | `__attribute__((deprecated)) int legacy_open(const char *name);` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

func_deprecated_added: Function marked deprecated: legacy_open ()
```

## Minimum evidence

`min_evidence: L2` — a deprecation attribute is a *declaration* property. It
leaves no trace in the exported symbol table (L0) and none in DWARF (L1);
only the public header AST carries it, which is why this finding is
available exactly when headers are supplied.

## Why abicheck catches it

The header-AST backends record each declaration's attributes, and
`diff_symbols.py` compares the deprecation flag between matched declarations,
emitting `func_deprecated_added` — a `COMPATIBLE` kind, since nothing about
the artifact's contract has actually changed yet. Reporting it keeps the
governance signal visible on a passing run, which is the
"record before disposing" principle applied to a non-breaking fact.

## Runtime failure demonstration

**Severity: none at runtime — a compile-time warning only.**

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,. -o app
./app
# → legacy_open -> 1

gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app
# → legacy_open -> 1   (identical output)

gcc -g app.c -I. -include v2.h -c -o /dev/null
# → warning: 'legacy_open' is deprecated [-Wdeprecated-declarations]
```

## Safe redesign

None needed — deprecating before removing is the correct sequence. What
matters is that the deprecation is machine-readable (an attribute), announced
at least one release before the removal, and paired with a working
replacement, as `learn/rollout-and-governance.md` describes.

## Cross-tool comparison

`abidiff` does not report deprecation at all (it is not an ABI fact); ABICC
reports it only when its own header dump captures the attribute. abicheck
treats it as a first-class, non-breaking finding so it survives into a
release report rather than being filtered out as "no ABI change."
