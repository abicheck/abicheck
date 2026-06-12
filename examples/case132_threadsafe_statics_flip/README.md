# Case 132: Thread-Safe Statics Mode Flip (`-fno-threadsafe-statics`)

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> Same source, same symbols; v1 built with thread-safe local-static
> initialization (`-fthreadsafe-statics`, the default), v2 with
> `-fno-threadsafe-statics`. The L3 build context reveals the flip →
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
`v1.compile_commands.json` carries `-fthreadsafe-statics`,
`v2.compile_commands.json` carries `-fno-threadsafe-statics`; the L3 diff
normalizes to the canonical `threadsafe_statics` option and reports the flip.

## Reproduce manually
```bash
g++ -shared -fPIC -g -fthreadsafe-statics v1.cpp -o libv1.so
g++ -shared -fPIC -g -fno-threadsafe-statics v2.cpp -o libv2.so
abicheck dump libv1.so --build-info v1.compile_commands.json -o v1.abi.json
abicheck dump libv2.so --build-info v2.compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json   # → threadsafe_statics_mode_changed
```

## How to fix
Keep thread-safe statics enabled for any public inline holding a function-local
static, or guarantee consumers never rely on cross-TU first-use ordering.
