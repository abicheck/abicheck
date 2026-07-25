### Added

- **`profiles.<id>.compile` — an optional compile-context overlay for
  `.abicheck.yml` `profiles:` entries** (P1 toolchain-profile audit).
  `ProfileSpec` previously carried only `{id, contract, os, arch}`; the new
  `ProfileCompileSpec` lets a profile declare `compiler_family`,
  `compiler_version` (a version constraint), `target`, `standard`, `stdlib`,
  a logical toolchain `binding` id, `abi_macros`, and normalized extra
  `args` — additive over the root `compile:` block. Declarative only, per
  the project's untrusted-config trust boundary: no raw executable
  path/command field, and every atom is rejected if it contains whitespace
  (the same `_safe_compile_atom` convention `BuildConfig.compile.std`
  already uses) so a single YAML scalar can never smuggle multiple compiler
  argv tokens. This is config-schema/validation groundwork; G30 P1.4's
  run-plan generator (`abicheck/buildsource/run_plan.py`, merged separately)
  resolves `(target, profile, check)` cells from `build-output.json` but
  does not yet read this `compile:` overlay — no consumer forwards it into
  an actual `dump`/`compare` invocation yet.
- **Structured compile-context provenance on every header-AST snapshot**
  (schema v14 → **v15** — bumped past v14 rather than reusing it, since
  ADR-050 D1's `AbiSnapshot.contract` had already claimed schema v14 on
  `main` independently of this work): `AbiSnapshot.ast_resolved_standard`
  (the C/C++ standard actually used — an explicit `-std=`/`--std=`/`/std:`
  value verbatim, or `"gnu++20"` when the requires/concept heuristic forced
  it; `None` when the frontend's own unpinned default was used, never
  guessed), `ast_cplusplus_macro` (the standard-mandated `__cplusplus`
  literal for that standard), `ast_compile_args` (the ordered extra
  compiler arguments passed to the header frontend), and `ast_sysroot`.
  Purely additive — a pre-v15 snapshot loads all four as their conservative
  "not recorded" defaults. Populated once via a shared
  `dumper_toolchain._ast_compile_provenance` helper so the ELF/PE/Mach-O
  snapshot constructors can't drift.
