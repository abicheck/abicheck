# Case 130: Exceptions Mode Flip (`-fno-exceptions`)

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> Same source, same exported symbols. The only difference is the **build mode**:
> v1 was built with C++ exceptions enabled (`-fexceptions`), v2 with
> `-fno-exceptions`. abicheck captures the per-side build context (L3) from a
> generated CMake `compile_commands.json` and reports
> `exceptions_mode_changed`.

## What this demonstrates
The two modes are not link-compatible: an exception thrown in `-fexceptions`
code that unwinds through a frame compiled `-fno-exceptions` is undefined
behaviour, and `-fno-exceptions` changes the codegen / EH tables of every public
inline that uses `throw`/`try`/`catch`. A symbol-only or even DWARF-only check
sees nothing — the signal lives in the captured build flags.

## Why COMPATIBLE_WITH_RISK
Per ADR-028 D3 a build-context finding never decides a shipped-ABI break on its
own; the artifact diff proves any concrete break. This flags the elevated risk
and localizes the cause for review.

## How abicheck detects it
The CMake fixture builds v1 with `-fexceptions` and v2 with `-fno-exceptions`.
The generated build-dir `compile_commands.json` carries those flags; the L3
build-evidence diff normalizes them to the canonical `exceptions` option and
reports the flip.

## Reproduce manually
```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build /tmp/abicheck-examples-build --target case130_exceptions_mode_flip_v1 case130_exceptions_mode_flip_v2
abicheck dump /tmp/abicheck-examples-build/case130_exceptions_mode_flip/libv1.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v1.abi.json
abicheck dump /tmp/abicheck-examples-build/case130_exceptions_mode_flip/libv2.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json   # → exceptions_mode_changed
```

## How to fix
Ship one exception mode for the public ABI, or rebuild all consumers in the
matching mode if the public API exposes exception types or throwing inlines.
