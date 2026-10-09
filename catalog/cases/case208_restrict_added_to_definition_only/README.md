# Case 208: `restrict` Added to the Definition Only

**Category:** No Change (declared interface) | **Verdict:** ✅ NO_CHANGE — with a separate **behavioral break**

## Verdict and consumer impact

The implementation of `blend()` gains `restrict` on both pointer parameters,
but the *declaration in the public header is unchanged*. As a statement about
the **declared interface** the verdict is `NO_CHANGE`: consumers compile
against the header, which still permits overlapping buffers, and nothing a
binary/header comparison reads has changed.

**That is not a statement that v2 is safe to ship.** Top-level `restrict` is
not part of the function's type, so the declaration and the definition stay
compatible, but inside the definition the parameters now promise not to
alias, and the `-O3` library is compiled on that promise. An unchanged old
consumer that passes overlapping buffers — valid under the declaration it
compiled against — gets a different result after the swap (runtime witness
below, GCC and Clang). `ground_truth.json` records this separately as
`behavioral_break: true` with `truth_scope: declared-interface`, so the
`NO_CHANGE` verdict is never read as overall compatibility truth. It is an
implementation-behavior regression the library introduced, not something a
detector can infer from artifacts whose declared contract is identical.

This is the **no-header-diff detection control** paired with
[`case207_pointer_parameter_gained_restrict`](../case207_pointer_parameter_gained_restrict/README.md),
where the same qualifier is added to the published declaration and reported
as `param_restrict_added`.

## Old/new diff

| v1.c | v2.c |
|------|------|
| `void blend(float *dst, const float *src, int n)` | `void blend(float *restrict dst, const float *restrict src, int n)` |

`v1.h` and `v2.h` are identical.

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: NO_CHANGE (exit 0)

_No ABI changes detected._
```

`param_restrict_added` / `param_restrict_changed` are expected *not* to fire:
the published declaration did not change. That absence is the detection
control; the behavioral regression is recorded separately (see above).

## Minimum evidence

`min_evidence: L2` — the public header AST is what establishes that the
*published* declaration is unchanged. A comparison that read the qualifier
from the definition instead (or from DWARF's `DW_TAG_restrict_type` on the
defining subprogram) would report a contract change the consumer's own
compiler never sees.

## Why abicheck catches it

Nothing is reported because the header AST is the authority for a
declaration's qualifiers, and both sides' headers declare `blend()`
identically. `diff_param_qualifiers.py` therefore finds both parameters'
`is_restrict_fact` determinations equal and emits nothing. The case pins
which artifact abicheck treats as the contract: the header a consumer
compiles against, not the source file the library happens to build from.

## Runtime failure demonstration

**Severity: wrong results for consumers that pass overlapping buffers — a
behavioral break the declared interface does not show.**

Both libraries are built at `-O3`. The unchanged old consumer computes an
in-place running prefix sum, `blend(buf + 1, buf, 128)`, and checks every
element against a sequential oracle:

```bash
gcc -O3 -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app
./app            # first five: 1 3 6 10 15                         exit 0
gcc -O3 -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app            # first five: 1 3 5 7 9  WRONG RESULT at element 2  exit 1
```

The earlier witness used non-overlapping buffers and so could not observe
anything.

## Safe redesign

Don't add `restrict` to a published function's definition unless the
declaration says so too: the optimisation licence only exists for callers that
were promised the stronger precondition, which is case207's (reported)
change. If the library wants the vectorised path, keep the overlap-tolerant
entry point and add a new, `restrict`-declared one — or check for overlap and
dispatch.

## Cross-tool comparison

`abidiff`, reading DWARF for the definition, can see the qualifier on the
defining subprogram; abicheck deliberately answers the declaration question
from the public header instead, so the two tools are answering different
questions here rather than disagreeing about one.
