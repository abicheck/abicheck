# Case 131: RTTI Mode Flip (`-fno-rtti`)

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> Same source, same symbols; v1 built with RTTI (`-frtti`), v2 with `-fno-rtti`.
> The generated CMake build context (L3) reveals the flip →
> `rtti_mode_changed`.

## What this demonstrates
`-fno-rtti` omits `type_info` for polymorphic types, so `dynamic_cast`/`typeid`
and cross-DSO exception matching that relies on RTTI identity can fail to link or
silently misbehave when one side has RTTI and the other does not.

## Why COMPATIBLE_WITH_RISK
A build-mode signal, not a proven binary break (ADR-028 D3). The artifact diff
confirms any concrete break; this localizes the elevated risk.

## How abicheck detects it
The CMake fixture builds v1 with `-frtti` and v2 with `-fno-rtti`; the generated
build-dir `compile_commands.json` carries those flags. The L3 diff normalizes to
the canonical `rtti` option and reports it.

## Reproduce manually
```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build /tmp/abicheck-examples-build --target case131_rtti_mode_flip_v1 case131_rtti_mode_flip_v2
abicheck dump /tmp/abicheck-examples-build/case131_rtti_mode_flip/libv1.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v1.abi.json
abicheck dump /tmp/abicheck-examples-build/case131_rtti_mode_flip/libv2.so --build-info /tmp/abicheck-examples-build/compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json   # → rtti_mode_changed
```

## How to fix
Keep a single RTTI mode for the public ABI, or rebuild consumers in the matching
mode if the public API exposes polymorphic types or `dynamic_cast`/`typeid`.
