# Case 201: Public Function Parameters Reordered

**Category:** Breaking | **Verdict:** ❌ BREAKING

## Verdict and consumer impact

`plot_point()`'s two parameters swap places. The exported C symbol name does
not encode the signature, so the link still succeeds. What breaks is the
**parameter contract**: the old and new declarations are incompatible, a
caller recompiled against v2 that still writes `plot_point(7, 1.5)` compiles
(with at most a conversion warning) and silently passes `7.0` / `1`, and an
already-built caller's arguments reach the callee only as far as the target
ABI happens to allow.

That last part is ABI-dependent, and the case does not claim a universal
register story:

* **x86-64 System V (the tested target):** an `int` goes in the first integer
  register and a `double` in the first SSE register *whatever their
  positions*, so v2's callee still receives `index=7, value=1.5` from an old
  caller — the runtime witness shows no signal, under GCC and Clang.
* **Positional ABIs:** with `__attribute__((ms_abi))` (Microsoft x64
  convention) the same swap delivers garbage to the callee under both GCC
  and Clang; i386 stack passing likewise. These are Linux mechanism probes,
  not native Windows tests.

Register assignments that happen to agree on one ABI do not turn
incompatible C declarations into a compatibility guarantee, so the verdict
stays `BREAKING` (`func_params_changed`).

This is the **positive control** for the parameter-order mechanism. Its
negative control is
[`case202_public_header_declaration_order_changed`](../case202_public_header_declaration_order_changed/README.md),
where the header's *declaration* order changes and no parameter list does.
The pair proves two things about these two fixtures: reordering a parameter
list is flagged, and reordering declarations in the header is not.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `void plot_point(int index, double value);` | `void plot_point(double value, int index);` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

func_params_changed: Parameters changed: plot_point
```

## Minimum evidence

`min_evidence: L1` — the ordered parameter list is the fact that has to be
compared, and a `-g` build records it in DWARF as the ordered
`DW_TAG_formal_parameter` children of the subprogram DIE. L0 sees the same
`plot_point` export on both sides and reports nothing.

## Why abicheck catches it

`diff_symbols.py` compares matched functions' parameters **positionally**,
so a permutation reads as a per-position type change rather than as an
unchanged multiset. This is deliberate: a parameter list is an ordered
calling contract, not a set.

Note the limit this case does *not* claim to cover: a reorder of two
**same-typed** parameters produces an identical signature at every evidence
tier abicheck can collect, and is therefore invisible — a real, silent break
that no static comparison of these two artifacts can distinguish. This
fixture uses two differently-typed parameters precisely so the mechanism it
demonstrates is the one abicheck actually observes.

## Runtime failure demonstration

**Severity: ABI-dependent for already-built callers; silent value swap for
recompiled ones.**

The implementations record what they receive, and the old app checks it:

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app
./app            # callee received index=7 value=1.5            exit 0
gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app            # callee received index=7 value=1.5            exit 0  (SysV)
```

On x86-64 System V there is deliberately no runtime signal — see above. The
earlier fixture's implementations ignored their arguments, so its smoke could
not have shown delivery either way.

## Safe redesign

Never permute an existing parameter list. Add a new entry point (as case200
does) or take an options struct, so a future argument arrives as a field
rather than as a new position.

## Cross-tool comparison

`abidiff` reports the parameter-type changes at positions 0 and 1; ABICC
reports the same from its header dump. All three tools are blind to the
same-typed reorder described above.
