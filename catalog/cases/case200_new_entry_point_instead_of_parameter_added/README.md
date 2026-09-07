# Case 200: New Entry Point Instead of a Changed Arity

**Category:** Addition | **Verdict:** ✅ COMPATIBLE

## Verdict and consumer impact

The library needs to accept a `flags` argument it did not accept before. It
delivers that as a *new* entry point, `chan_open_ex()`, and leaves
`chan_open()`'s own signature untouched. Every already-compiled caller keeps
resolving and calling the same one-argument function; every existing call
site still compiles. Consumers opt in to the new capability by calling the
new name.

This is the **negative control** paired with
[`case199_public_function_parameter_added`](../case199_public_function_parameter_added/README.md),
which makes the same capability change by editing the existing arity. The
pair proves two things about these two fixtures: the arity change is flagged
(case199), and its nearest safe alternative is not (this case).

## Old/new diff

| v1.h | v2.h |
|------|------|
| `int chan_open(const char *name);` | `int chan_open(const char *name);`<br>`int chan_open_ex(const char *name, int flags);` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

func_added: New public function: chan_open_ex
```

`func_params_changed` is expected *not* to fire: no existing function's
parameter list changed. That absence is the point of the case.

## Minimum evidence

`min_evidence: L0` — a newly exported symbol is visible in the dynamic symbol
table alone, with no debug info or headers needed. abicheck still reads the
headers here so that the *absence* of a signature change is established on
the same evidence tier case199's presence is.

## Why abicheck catches it

`diff_symbols.py` reports an export present only on the new side as
`func_added`, a `COMPATIBLE` kind in the change registry. Because
`chan_open` matched an identically-shaped counterpart, the parameter
comparison that fires in case199 has nothing to report.

## Runtime failure demonstration

**Severity: none — verified no observable effect.**

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,. -o app
./app
# → chan_open -> 3

gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app
# → chan_open -> 3   (identical output)
```

## Safe redesign

None needed — this *is* the safe redesign that case199's own "Safe redesign"
section recommends. The cost is a permanently wider export surface, which is
the trade-off `learn/surface-growth.md` describes.

## Cross-tool comparison

`abidiff` and ABICC both report only the added function here. The case is
kept because a corpus that contains only breaking transformations cannot
demonstrate that a detector stays quiet on the adjacent safe one.
