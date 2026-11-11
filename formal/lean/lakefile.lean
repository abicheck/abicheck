import Lake
open Lake DSL

-- Core-only Lean 4 project: no Mathlib, no external dependencies.
package «abi-lean» where

@[default_target]
lean_lib AbiLean where
