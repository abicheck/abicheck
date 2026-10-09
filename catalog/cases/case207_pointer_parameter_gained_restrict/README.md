# Case 207: Pointer Parameter Gained `restrict`

**Category:** Risk | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

`blend()`'s two pointer parameters gain `restrict`. Nothing about the calling
convention changes — a `float *` is still one machine word in the same
register — so no prebuilt consumer fails to link and no call site fails to
compile. What changed is the *caller's* obligation: under v1 a caller could
legally pass overlapping buffers; under v2 doing so is undefined behaviour,
and the callee's compiler is now free to vectorise the loop on that promise.

That obligation is not hypothetical: the runtime witness below shows an
unchanged, already-built consumer that relies on overlap computing a
different result after the swap, under both GCC and Clang. Consumers that
never alias the two arguments are unaffected, and nothing in the library
says which kind a consumer is — so the finding is a **conditional risk**
(`param_restrict_added`, `COMPATIBLE_WITH_RISK`), not a calling-convention
break. A project that promises overlap-tolerant behaviour should gate it.

It is the mirror image of
[`case186_c_api_pointee_const_abi_neutral`](../case186_c_api_pointee_const_abi_neutral/README.md),
where a qualifier change tightened what the *callee* promises. Here the
tightening runs the other way, onto the caller.

Its negative control is
[`case208_restrict_added_to_definition_only`](../case208_restrict_added_to_definition_only/README.md),
where `restrict` appears only inside the implementation and never in the
published declaration. The pair proves two things about these two fixtures:
a `restrict` change on the *published contract* is reported, and one confined
to the implementation is not — although case208's implementation-only
`restrict` breaks the same overlapping consumer at runtime (recorded there as
a separate behavioral break, not a detection target).

## Old/new diff

| v1.h | v2.h |
|------|------|
| `void blend(float *dst, const float *src, int n);` | `void blend(float *restrict dst, const float *restrict src, int n);` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

param_restrict_added: Parameter restrict qualifier added: blend param dst
param_restrict_added: Parameter restrict qualifier added: blend param src
```

`func_params_changed` must not appear: top-level `restrict` qualifies the
parameter object, not the function's type. (The clang header backend used to
keep it in the parameter's type spelling and report a BREAKING
"parameters changed" finding with the wrong mechanism; both backends now
carry it only through `Param.is_restrict`.) Removing `restrict` is reported
as `param_restrict_changed` (`COMPATIBLE`): the callee only drops an
optimizer assumption.

## Minimum evidence

`min_evidence: L2` — `restrict` is a declaration property the header-AST
backends record (`Param.is_restrict`). It is not part of the mangled name
(L0) and DWARF's own `DW_TAG_restrict_type` is not what abicheck reads here,
so the public header is the evidence tier this finding rests on.

## Why abicheck catches it

`compare/parameter_facts.py` compares each matched parameter's
`is_restrict_fact` between the two sides and emits `param_restrict_added`
when it was gained (`param_restrict_changed` when lost). It compares the
`Fact[bool]` sibling rather than the raw flag precisely so "not collected"
and "confirmed not restrict-qualified" are not folded together — a snapshot
produced by a backend that never populated the field would otherwise read as
every qualifier having just been added.

## Runtime failure demonstration

**Severity: wrong results for consumers that pass overlapping buffers.**

Both libraries are built at `-O3` regardless of the build type (the promise
only matters once the optimizer acts on it). The unchanged old consumer
computes an in-place running prefix sum, `blend(buf + 1, buf, 128)`, which
v1's declaration permits, and checks every element against a sequential
oracle.

```bash
gcc -O3 -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app
./app            # first five: 1 3 6 10 15                         exit 0
gcc -O3 -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app            # first five: 1 3 5 7 9  WRONG RESULT at element 2  exit 1
```

Reproduced with GCC 13 and Clang 18/19, Debug and Release consumers. GCC
already takes the vectorised path at 4 elements; Clang needs a longer input,
hence 128.

## Safe redesign

Do not add `restrict` to an already-published parameter. Introduce a new
entry point that carries the stronger precondition (the case200 pattern) and
document the aliasing contract there, leaving the original function's weaker
promise intact for existing callers.

## Cross-tool comparison

`abidiff` treats `restrict` as part of the parameter's type and reports a
subtype change; ABICC reports a parameter-type change. abicheck reports it as
its own `param_restrict_changed` kind at `COMPATIBLE` severity, which keeps
the aliasing-contract signal visible without claiming a binary break that did
not happen.
