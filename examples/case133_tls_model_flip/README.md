# Case 133: TLS Model Flip (`-ftls-model`)

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> Same source, same symbols; v1 built with `-ftls-model=global-dynamic`, v2 with
> `-ftls-model=initial-exec`. The generated CMake L3 build context reveals the change →
> `tls_model_changed`.

## What this demonstrates
The thread-local storage model changes the TLS access sequence the compiler
emits. A consumer built against the old model can use the wrong access pattern
for an exported `thread_local`; `initial-exec`, in particular, cannot be used
from a library `dlopen`ed after program start.

## Why COMPATIBLE_WITH_RISK
A build-mode signal, not a proven binary break (ADR-028 D3); the artifact diff
proves any concrete break, this localizes the risk.

## How abicheck detects it
The CMake fixture builds v1 with `-ftls-model=global-dynamic` and v2 with
`-ftls-model=initial-exec`; the generated build-dir `compile_commands.json`
carries those flags. The L3 diff normalizes both to the canonical `tls_model`
option and reports the switch.

## Reproduce manually
```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build /tmp/abicheck-examples-build --target case133_tls_model_flip_v1 case133_tls_model_flip_v2
abicheck dump /tmp/abicheck-examples-build/case133_tls_model_flip/libv1.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v1.abi.json
abicheck dump /tmp/abicheck-examples-build/case133_tls_model_flip/libv2.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json   # → tls_model_changed
```

## How to fix
Choose a TLS model compatible with how the library is loaded (use
`global-dynamic` for anything that may be `dlopen`ed) and keep it stable across
releases.
