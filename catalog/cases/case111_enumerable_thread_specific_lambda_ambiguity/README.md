# Case 111: enumerable_thread_specific Lambda-Init Ambiguity

**Category:** Subtle source break / regression suite | **Verdict:** 🟠 API_BREAK (known detector gap — abicheck currently reports COMPATIBLE at every evidence tier; see below)

## What breaks

A second constructor overload is added — `enumerable_thread_specific(int_factory_t)`
(a function-pointer-typed factory). By itself this is a pure addition:
existing call sites that pass an `int` still resolve to the original
constructor. But consumer code patterns that previously had a single
viable conversion path can now become ambiguous, particularly with
brace-initialization or generic callable arguments. The risk is silent —
code that compiled before may compile to a different constructor against
the new headers, or stop compiling at unrelated call sites that infer
the wrong overload.

**Why function-pointer instead of `std::function`?** A realistic
example (such as oneTBB) would accept `std::function<int()>`, but pulling in
`<functional>` from libstdc++ 13 trips castxml/clang (`__assume__`
attribute in `<bits/stl_bvector.h>`), which would prevent the integration
test from ever running. A function-pointer typedef exhibits the same
overload-ambiguity risk for the purposes of this regression fixture.

## Why this matters

Mirrors a documented oneTBB pain point: adding lambda-/functor-accepting
constructor overloads to existing handle types introduced overload
ambiguity in real downstream code. The pattern is repeatable across
container-like types.

## How abicheck catches it (and where it doesn't)

**It doesn't — at any evidence tier.** This is the catalog's canonical
example of a *scenario* being proven true (by the `source_smoke` oracle
below) while no current detector, at any of L0-L5, produces the verdict
that scenario demands. That is different from case105 (concept tightening)
or case122 (uninstantiated template change), where a *higher* evidence
tier (L4) does catch the break — case111 has no tier that catches it yet.

The diff exposes:

- `FUNC_ADDED`: the new `std::function<int()>` constructor

`FUNC_ADDED` on a constructor is, in isolation, compatible — it cannot
link- or ABI-break anything by itself. The follow-on **overload
ambiguity** that breaks downstream source compilation depends on the
consumer's call-site context, which no snapshot-level detector currently
reasons about for newly-added constructor overloads (contrast
`case169_overload_added`'s `OVERLOAD_ADDED`, which only groups
same-named *free-function* overloads by Itanium mangling — it does not
reason about constructor-overload call-site ambiguity).

**Canonical verdict:** `API_BREAK` — proven by this case's own
`source_smoke` (v1 compiles, v2 is ambiguous), matching the project's
definition of API_BREAK: a public-header change that breaks
recompilation while already-built binaries remain viable (`abi_break:
false`, `api_break: true`). abicheck's actual output at every evidence
tier is `COMPATIBLE` with only `func_added` observed — a real,
tracked **known detector gap**, not an evidence-depth limitation. The
gap is recorded so a `KINDS_MISMATCH`/verdict-mismatch reviewer can see
*why* the mismatch is expected rather than silently accepting the tool's
current output as ground truth.

A constructor-overload-ambiguity detector is the natural home for
closing this gap; it would need the same castxml header-AST capture
path used for case105's concept-tightening detector to reason about
call-site resolvability.

**Update:** abicheck has a `ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK`
best-effort heuristic (`diff_symbols._diff_ctor_overload_ambiguity`)
that flags a class gaining a 2nd+ non-explicit converting constructor —
the classic *implicit-conversion* ambiguity pattern. **It does not close
this case's gap.** Both of this case's constructors are declared
`explicit` (see `v1.h`/`v2.h`), and this case's own `source_smoke`
proof is triggered by empty-brace-list direct-initialization
(`ets({})`), not implicit conversion — direct-initialization performs
overload resolution over explicit constructors too, and an empty
braced-init-list value-initializes almost any scalar/pointer parameter
type, so it collides across *both* new and old overloads regardless of
`explicit`. Soundly detecting that would need a general call-site
overload-resolution simulation (which argument shapes are viable
against which parameter types), not a snapshot-level heuristic — still
future work. The heuristic was deliberately scoped to the narrower,
lower-false-positive non-explicit case rather than widened to cover
this scenario, since widening to any 2nd single-scalar-argument
constructor (explicit or not) would fire on most multi-constructor
classes.

## Code diff

| v1 | v2 |
|----|------|
| `enumerable_thread_specific(int);` | same — plus a new overload |
| (no other ctors) | `enumerable_thread_specific(int_factory_t);` (`typedef int (*int_factory_t)()`) |

## How to fix (as a library maintainer)

- Constrain the lambda-init overload with a SFINAE / concept that
  excludes `int`-convertible types. e.g.:
  ```cpp
  template <class F,
            class = std::enable_if_t<!std::is_convertible_v<F, int>>>
  explicit enumerable_thread_specific(F&& init);
  ```
  This eliminates the ambiguity at the call site.
- Or expose the lambda-init as a named factory
  (`from_lambda(...)`) rather than as an overloaded constructor.

## References

- oneTBB issue tracker — overload ambiguity in
  `enumerable_thread_specific` constructor set.
