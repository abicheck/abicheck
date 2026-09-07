# Case 199: Parameter Added to an Exported Function

**Category:** Breaking | **Verdict:** ❌ BREAKING

## Verdict and consumer impact

`chan_open()` gains a second parameter. In C the exported symbol name carries
no arity, so `chan_open` still resolves at load time and nothing fails to
link. Every caller compiled against v1 passes one argument; the v2 callee
reads a second one out of whatever the calling convention says holds it —
an uninitialised register or stack slot. Consumer source also has to change,
so this is both an ABI and an API break.

This is the **positive control** for the parameter-count mechanism. Its
negative control is
[`case200_new_entry_point_instead_of_parameter_added`](../case200_new_entry_point_instead_of_parameter_added/README.md),
which delivers the same new capability by adding a second entry point and
leaving the original arity intact. The pair proves two things about these
two fixtures: the arity change is flagged, and the additive alternative is
not.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `int chan_open(const char *name);` | `int chan_open(const char *name, int flags);` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

func_params_changed: Parameters changed: chan_open
```

## Minimum evidence

`min_evidence: L1` — the parameter list is what has to be compared, and a
`-g` build records it in DWARF (`DW_TAG_formal_parameter`). The dynamic
symbol table alone (L0) shows the same `chan_open` name on both sides and so
reports nothing; a C symbol's mangling does not encode its signature.

## Why abicheck catches it

`diff_symbols.py` joins the two sides through `SymbolIdentityIndex` and then
compares the matched functions' parameter lists element by element, so a
*count* difference is a difference in exactly the same way a *type*
difference is. Every existing signature case in the catalog changes a
parameter's type; this one changes the arity, which is the path that had no
fixture before.

## Runtime failure demonstration

**Severity: undefined behaviour — the callee reads an argument slot the
caller never wrote.**

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,. -o app
./app
# → chan_open -> 3

gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app
# → chan_open -> 3 or -1, depending on register residue
```

Recompiling the consumer against v2 fails outright
(`too few arguments to function 'chan_open'`), which is the API half of the
same break.

## Safe redesign

Add a new entry point rather than re-shaping the existing one — exactly what
case200 does — or reserve an options struct from the start so future
parameters arrive as fields rather than as arguments.

## Cross-tool comparison

`abidiff` reports the same change as a function-subtype change on
`chan_open`; ABICC reports it as a changed parameter list. All three agree
here — the case exists to give the arity path a fixture, not to separate the
tools.
