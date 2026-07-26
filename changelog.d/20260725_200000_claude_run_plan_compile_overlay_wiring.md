### Added

- **`abicheck run-plan generate` now projects `profiles.<id>.compile` into
  each cell it produces** (P1 toolchain-profile audit, closing the gap
  `ProfileCompileSpec`'s own docstring flagged: "no run-plan generator/
  toolchain resolver lives here yet"). Every resolved `RunPlanCheck` whose
  profile declares a `compile:` overlay gets `compile_gcc_options` — its
  `standard`/`stdlib`/`target`/`abi_macros`/`args` composed into one
  extra-flags string (`-std=... -stdlib=... --target=... -Dmacro=value ...
  <args>`) — and, when the new `--toolchain-bindings PATH` flag resolves the
  profile's `compile.binding` logical id, `compile_gcc_path` (the resolved
  exact executable path; an unresolvable declared binding is now a
  generation error, exit 1). `check-project.yml` forwards both ahead of its
  own global `gcc-path`/`gcc-options` inputs for that cell (new
  `toolchain-bindings-path` workflow input, empty by default — no behavior
  change for a project with no `profiles.*.compile` block).
  `compiler_family`/`compiler_version` stay validated-but-unforwarded
  (documented as a deliberate gap: family only selects a toolchain through
  `binding`, and version is a constraint string, not an invocation flag —
  verifying a resolved binding's actual version needs a real subprocess
  probe, out of scope for this pure, no-I/O module).
