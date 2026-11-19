import Lake
open Lake DSL

-- Core-only Lean 4 project: no Mathlib, no external dependencies.
package «abi-lean» where

@[default_target]
lean_lib AbiLean where
  -- RecordEditFragment checks its 9252-case space with `decide +kernel`,
  -- which needs a larger elaboration thread stack than the default.
  moreLeanArgs := #["--tstack=4000000"]
