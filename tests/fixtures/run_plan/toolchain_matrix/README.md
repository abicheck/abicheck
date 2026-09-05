# Toolchain-matrix reference example

Demonstrates `abicheck project plan`'s `profiles.<id>.compile` overlay
projection (P1 toolchain-profile audit, closing the gap
`ProfileCompileSpec`'s own docstring once flagged: "no run-plan generator/
toolchain resolver lives here yet"). See
[`docs/reference/run-plan-schema.md`](../../../../docs/reference/run-plan-schema.md#runplancheck-fields)
for the field reference this walkthrough exercises, and
[`docs/reference/project-targets-schema.md`](../../../../docs/reference/project-targets-schema.md#profiles)
for the `.abicheck.yml` schema itself.

## What's here

- `.abicheck.yml` — one library target, one check, and **two** contract
  profiles that each pin a different real toolchain for the same target:
  - `linux-gcc14`: GCC via `compile.binding: gcc14`, no dialect/macro
    overlay beyond `standard`/`stdlib`.
  - `linux-clang20`: Clang via `compile.binding: clang20`, **plus** a C++20
    standard, an ABI macro (`MATRIXDEMO_ABI_V2=1`), and an extra `-fno-rtti`
    flag — every axis `ProfileCompileSpec` supports in one profile.
- `toolchain-bindings.yml` — the separately-trusted mapping resolving both
  logical `binding` ids (`gcc14`, `clang20`) to (fictitious, for this
  fixture) executable paths.

## Reproduce it yourself

```bash
# From the repository root:
mkdir -p /tmp/matrix-demo/bo-gcc14 /tmp/matrix-demo/bo-clang20
cat > /tmp/matrix-demo/bo-gcc14/build-output.json <<'EOF'
{"schema": "abicheck.build-output/v1", "profile": {"id": "linux-gcc14"},
 "targets": [{"id": "libmatrixdemo", "binary": "build/libmatrixdemo.so"}]}
EOF
cat > /tmp/matrix-demo/bo-clang20/build-output.json <<'EOF'
{"schema": "abicheck.build-output/v1", "profile": {"id": "linux-clang20"},
 "targets": [{"id": "libmatrixdemo", "binary": "build/libmatrixdemo.so"}]}
EOF

abicheck project plan tests/fixtures/run_plan/toolchain_matrix/.abicheck.yml \
  --build-output linux-gcc14=/tmp/matrix-demo/bo-gcc14 \
  --build-output linux-clang20=/tmp/matrix-demo/bo-clang20 \
  --toolchain-bindings tests/fixtures/run_plan/toolchain_matrix/toolchain-bindings.yml
```

## What to look for in the output

Two `checks[]` entries, one per profile, each carrying its own resolved
compiler context:

```json
{
  "check_id": "libmatrixdemo@linux-gcc14#release-contract@headers",
  "profile_id": "linux-gcc14",
  "compile_gcc_path": "/opt/gcc-14.2.0/bin/g++",
  "compile_gcc_options": "-std=gnu++17 -stdlib=libstdc++"
},
{
  "check_id": "libmatrixdemo@linux-clang20#release-contract@headers",
  "profile_id": "linux-clang20",
  "compile_gcc_path": "/opt/llvm-20/bin/clang++",
  "compile_gcc_options": "-std=gnu++20 -stdlib=libc++ -DMATRIXDEMO_ABI_V2=1 -fno-rtti"
}
```

`check-project.yml` forwards `compile_gcc_path`/`compile_gcc_options` as
that cell's `gcc-path`/`gcc-options` inputs (ahead of the workflow's own
global values) when its `toolchain-bindings-path` input is set — see
[`docs/reference/reusable-workflows.md`](../../../../docs/reference/reusable-workflows.md#shared-analysis-options).

## Regression coverage

`tests/test_run_plan.py::TestToolchainMatrixFixtureExample` loads this
exact `.abicheck.yml`/`toolchain-bindings.yml` pair (not an inline dict) and
asserts both cells' `compile_gcc_path`/`compile_gcc_options` match the
values shown above — this README and the fixtures can't silently drift
apart.

## Note: not part of the `examples/` ABI-diff catalog

This fixture lives under `tests/fixtures/`, not `examples/case*/`, because
it demonstrates *config/toolchain resolution*, not an ABI comparison —
`examples/` is exclusively the compiled `v1`/`v2` ground-truth catalog
tracked in `catalog/ground_truth.json` (see `examples/CLAUDE.md`); a
project-config artifact with no verdict/expected-kinds shape doesn't fit
that schema.
