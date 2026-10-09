# Case 209: Calling Convention Changed via a Macro

**Category:** Function ABI | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

The macro-spelled sibling of
[`case64_calling_convention_changed`](../case64_calling_convention_changed/README.md).
`vector_dot`/`vector_scale` are declared `double MATHLIB_CALL vector_dot(...)`
in a public header that is **textually identical** between versions. Only the
included config header changes: `MATHLIB_CALL` expands to
`__attribute__((sysv_abi))` (the System V AMD64 default, spelled explicitly)
in v1 and to `__attribute__((ms_abi))` in v2. After preprocessing this is
exactly case64's break — parameters move from `rdi`/`rsi`/`rdx` to
`rcx`/`rdx`/`r8` — so an unchanged, already-built consumer reads stale
registers and computes garbage. Recompilation against v2 is mandatory.

Its negative control is
[`case210_calling_convention_respelled_via_macro`](../case210_calling_convention_respelled_via_macro/README.md),
where a macro is introduced but expands to the convention the literal
spelling already had. The pair proves two things about these two fixtures:
a convention change hidden behind a macro is reported, and a pure respelling
of the same convention is not.

## Old/new diff

| v1_config.h | v2_config.h |
|-------------|-------------|
| `#define MATHLIB_CALL __attribute__((sysv_abi))` | `#define MATHLIB_CALL __attribute__((ms_abi))` |

`v1.h` and `v2.h` differ only in which config header they include; both
declare `double MATHLIB_CALL vector_dot(const double *a, const double *b, int len);`.

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

calling_convention_changed: Calling-convention attribute changed for vector_dot: (default) → ms_abi
calling_convention_changed: Calling-convention attribute changed for vector_scale: (default) → ms_abi
```

Verified identically with `ABICHECK_AST_FRONTEND=castxml` and
`ABICHECK_AST_FRONTEND=clang`. An explicit `sysv_abi` is the platform
default on x86-64 Linux and is reported as `(default)`.

## Minimum evidence

`min_evidence: L1` — as in case64, Clang records the convention in DWARF's
`DW_AT_calling_convention`, so a Clang-built pair is detected from debug info
alone, whatever spelling the source used. GCC emits no such attribute, so a
GCC-built pair needs the public headers (L2) — and there the convention is
only visible after macro expansion.

## Why abicheck catches it

CastXML 0.7 drops GNU x86-64 conventions (`ms_abi`/`sysv_abi`) from its own
`attributes` output, so abicheck recovers them from the declaration; this
case pins that the recovery sees through the `MATHLIB_CALL` macro to its
expansion rather than reading only the literal declaration text (which here
is identical on both sides). The clang AST backend records the expanded
attribute natively. Both backends then compare the effective convention per
function.

## Runtime failure demonstration

**Severity: CRITICAL**

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app -lm
./app
# → dot product = 32.0 (expected 32.0)
# → scaled = {2.0, 4.0, 6.0} (expected {2.0, 4.0, 6.0})       exit 0

gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app
# → dot product = 0.0 (expected 32.0)
# → scaled = {0.0, 0.0, 0.0} (expected {2.0, 4.0, 6.0})
# → WRONG RESULT: calling convention mismatch — parameters passed in wrong registers!   exit 1
```

## Safe redesign

Treat a convention macro as part of the ABI: once a release has shipped,
its expansion for a given platform must never change. Introduce a new entry
point (`vector_dot_ms()`) with the new convention instead, and keep the old
symbol on the old one.

**Real-world example:** Windows-facing libraries commonly route conventions
through macros such as `WINAPI`/`CALLBACK`/`APIENTRY` or a project-specific
`FOO_CALL`; redefining such a macro silently re-conventions every function
that uses it.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libv1.so
abidw --out-file v2.xml libv2.so
abidiff v1.xml v2.xml
```

`abidiff` sees the change only if the producer emitted
`DW_AT_calling_convention` (Clang, not GCC); ABICC works from the
preprocessed headers.
