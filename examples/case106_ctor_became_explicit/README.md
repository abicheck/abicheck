# Case 106: Conversion Operator Became `explicit`

**Category:** Source API contract | **Verdict:** 🟠 API_BREAK

## What breaks

A user-defined conversion operator (`operator int() const`) that previously
allowed implicit conversion gains the `explicit` specifier. The mangled
name is unchanged, so previously-compiled consumers keep linking — but
every source TU that relied on implicit conversion fails to compile
against the new headers.

The case applies equally to converting constructors (`explicit Foo(int)`),
but conversion operators are used in the example because they receive
mangled names from both castxml and DWARF, while constructors only mangle
via DWARF — a quirk that would otherwise prevent integration tests from
exercising the detector through the default castxml dump path.

## Why this is a oneTBB-flavored break

oneTBB handle types (`task_arena`, `task_group`, `global_control`) frequently
wrap a primitive (concurrency, version, count) and expose conversions to/from
the underlying integer. Tightening these with `explicit` per the modern C++
Core Guidelines is a tempting cleanup, but it silently breaks every consumer
that used the implicit conversion at a function-call argument boundary or
in copy-initialization.

## Code diff

| v1 | v2 |
|----|------|
| `operator int() const;` | `explicit operator int() const;` |
| `int n = ta;` (compiles) | same line: **error** — explicit cast needed |

## How abicheck catches it

New ChangeKind `CTOR_EXPLICIT_ADDED` (API_BREAK). The detector reads
`DW_AT_explicit` on the function's `DW_TAG_subprogram` (and the equivalent
`explicit="1"` attribute on castxml `Constructor` / `Method` / `Converter` elements) and
emits a finding when the bit transitions from absent to present.

The symmetric kind `CTOR_EXPLICIT_REMOVED` is COMPATIBLE_WITH_RISK — the
implicit-conversion path may now select a different overload silently.

The detector is **tri-state**: a missing `is_explicit` field in either
snapshot suppresses the finding. This prevents false positives against
older baseline snapshots that predate the field.

## Real Failure Demo

**Severity: BAD PRACTICE / API BREAK**

```bash
# v1 header, v1 .so: compiles and runs.
# Note linker order: object/source inputs must precede -l flags on modern
# Linux toolchains (-Wl,--as-needed is the default).
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app
./app   # → concurrency = 4 (expect 4)

# v2 header, v2 .so: app.cpp does `int n = ta;`, an implicit conversion
# via `operator int() const`. v2 declares the operator `explicit`, so the
# same app.cpp source no longer compiles:
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app
# → error: cannot convert 'mylib::task_arena' to 'int' in initialization
```

## How to fix

- Provide a non-explicit accessor (`int task_arena::concurrency() const`)
  as the migration path for any users who relied on implicit conversion.
- Stage the `explicit` tightening across a deprecation window: warn users
  for one release, error in the next.

## References

- [C++ Core Guideline C.46: declare single-argument constructors `explicit`](https://isocpp.github.io/CppCoreGuidelines/CppCoreGuidelines#c46-by-default-declare-single-argument-constructors-explicit)
- [DWARF 5 §3.3.8.1 — DW_AT_explicit](https://dwarfstd.org/doc/DWARF5.pdf)
