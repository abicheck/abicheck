<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **A compiler profile's `compile.frontend:` now drives its own check cell's
  header parsing** (G34 Phase B). The field, its validation, and its
  `run-plan.json` projection (`compile_ast_frontend`) already existed, but
  nothing consumed the projected value — every cell in a matrix resolved the
  one workflow-global `--ast-frontend`, which is exactly the producer/scanner
  conflation the phase exists to remove. `check-project.yml`'s check job now
  forwards it as `matrix.compile_ast_frontend || inputs.ast-frontend`, the
  same per-cell-first precedence `gcc-path`/`gcc-options` have used since the
  P1 toolchain-profile audit, so a GCC profile's cell can parse headers with
  castxml while a Clang/DPC++ profile's cell in the same run uses
  `clang -ast-dump=json`. The rest of the chain (`actions/check-target` → the
  root action → the CLI flag) already carried the workflow-level input, so
  nothing downstream needed a second pass-through. A profile that sets no
  `frontend:`, or a `run-plan.json` produced before the field existed, falls
  back to the global input unchanged. The sibling `consumer_compile.frontend`
  is deliberately *not* forwarded, and a test pins that absence: it describes
  the header-AST pass of the two-pass producer/consumer extraction that has
  not been built, so there is only one dump invocation per cell for it to
  steer — forwarding it would apply a consumer overlay to the producer pass,
  which is worse than leaving the field projected but inert.
