# Case 131: RTTI Mode Flip (`-fno-rtti`)

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> Same source, same symbols; v1 built with RTTI (`-frtti`), v2 with `-fno-rtti`.
> The per-side build context (L3) reveals the flip → `rtti_mode_changed`.

## What this demonstrates
`-fno-rtti` omits `type_info` for polymorphic types, so `dynamic_cast`/`typeid`
and cross-DSO exception matching that relies on RTTI identity can fail to link or
silently misbehave when one side has RTTI and the other does not.

## Why COMPATIBLE_WITH_RISK
A build-mode signal, not a proven binary break (ADR-028 D3). The artifact diff
confirms any concrete break; this localizes the elevated risk.

## How abicheck detects it
`v1.compile_commands.json` carries `-frtti`, `v2.compile_commands.json` carries
`-fno-rtti`; the L3 diff normalizes to the canonical `rtti` option and reports it.

## Reproduce manually
```bash
g++ -shared -fPIC -g -frtti v1.cpp -o libv1.so
g++ -shared -fPIC -g -fno-rtti v2.cpp -o libv2.so
abicheck dump libv1.so --build-info v1.compile_commands.json -o v1.abi.json
abicheck dump libv2.so --build-info v2.compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json   # → rtti_mode_changed
```

## How to fix
Keep a single RTTI mode for the public ABI, or rebuild consumers in the matching
mode if the public API exposes polymorphic types or `dynamic_cast`/`typeid`.
