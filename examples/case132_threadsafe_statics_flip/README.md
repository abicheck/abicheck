# Case 132: Thread-Safe Statics Mode Flip (`-fno-threadsafe-statics`)

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> Same source, same symbols; v1 built with thread-safe local-static
> initialization (`-fthreadsafe-statics`, the default), v2 with
> `-fno-threadsafe-statics`. The generated CMake L3 build context reveals the flip →
> `threadsafe_statics_mode_changed`.

## What this demonstrates
`-fno-threadsafe-statics` omits the `__cxa_guard` acquire/release around a
function-local static's first-use initialization. A public inline holding a
function-local static, compiled in different modes across translation units, has
mismatched guard expectations — a data race or double-init on concurrent first use.

## Why COMPATIBLE_WITH_RISK
A build-mode signal, not a proven binary break (ADR-028 D3); the artifact diff
proves any concrete break, this localizes the risk.

## How abicheck detects it
The CMake fixture builds v1 with `-fthreadsafe-statics` and v2 with
`-fno-threadsafe-statics`; the generated build-dir `compile_commands.json`
carries those flags. The L3 diff normalizes to the canonical
`threadsafe_statics` option and reports the flip.

## Reproduce manually
```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build /tmp/abicheck-examples-build --target case132_threadsafe_statics_flip_v1 case132_threadsafe_statics_flip_v2
abicheck dump /tmp/abicheck-examples-build/case132_threadsafe_statics_flip/libv1.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v1.abi.json
abicheck dump /tmp/abicheck-examples-build/case132_threadsafe_statics_flip/libv2.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json   # → threadsafe_statics_mode_changed
```

## How to fix
Keep thread-safe statics enabled for any public inline holding a function-local
static, or guarantee consumers never rely on cross-TU first-use ordering.
