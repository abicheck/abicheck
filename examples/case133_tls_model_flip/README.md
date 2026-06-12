# Case 133: TLS Model Flip (`-ftls-model`)

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> Same source, same symbols; v1 built with `-ftls-model=global-dynamic`, v2 with
> `-ftls-model=initial-exec`. The L3 build context reveals the change →
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
`v1.compile_commands.json` carries `-ftls-model=global-dynamic`,
`v2.compile_commands.json` carries `-ftls-model=initial-exec`; the L3 diff
normalizes both to the canonical `tls_model` option and reports the switch.

## Reproduce manually
```bash
g++ -shared -fPIC -g -ftls-model=global-dynamic v1.cpp -o libv1.so
g++ -shared -fPIC -g -ftls-model=initial-exec v2.cpp -o libv2.so
abicheck dump libv1.so --build-info v1.compile_commands.json -o v1.abi.json
abicheck dump libv2.so --build-info v2.compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json   # → tls_model_changed
```

## How to fix
Choose a TLS model compatible with how the library is loaded (use
`global-dynamic` for anything that may be `dlopen`ed) and keep it stable across
releases.
