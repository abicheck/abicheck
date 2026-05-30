# Probe Harness (build-configuration matrix)

Some ABI hazards are invisible to a single-binary comparison because the
library's public surface depends on **how the consumer builds against
it** — the language standard, the active backend macro, the compiler.
oneDPL is the canonical example: the same header tree exposes different
declarations under `ONEDPL_USE_TBB_BACKEND` vs `ONEDPL_USE_DPCPP_BACKEND`,
and raises its C++ standard floor between releases.

The probe harness compiles a small matrix of *consumer* translation
units (probes) under several *configurations* and diffs the resulting
matrices across two versions. It surfaces three change kinds:

| Change kind | Meaning |
|---|---|
| `API_DEPENDS_ON_CONSUMER_ENV` | A public declaration exists under some configurations but not others, *within a single version*. The public API depends on the consumer's toolchain. |
| `CXX_STANDARD_FLOOR_RAISED` | The minimum C++ standard across configurations rose between releases. Consumers still on the old standard get a degraded API. |
| `BEHAVIOURAL_DEFAULT_CHANGED` | A value in the manifest's `defaults:` section changed — source compiles unchanged, runtime behaviour silently differs. |

## Manifest

A probe spec is a YAML file with `configurations`, `probes`, and an
optional `defaults` map. See
[`examples/probes/onedpl.yaml`](https://github.com/napetrov/abicheck/blob/main/examples/probes/onedpl.yaml)
for a complete oneDPL manifest:

```yaml
name: onedpl
configurations:
  - id: gcc13_cxx17_tbb
    compiler: g++-13
    flags: [-std=c++17, -O0, -fopenmp]
    defines: {ONEDPL_USE_TBB_BACKEND: "1"}
    include_dirs: [/opt/oneapi/dpl/2023/include]
  - id: gcc13_cxx20_omp
    compiler: g++-13
    flags: [-std=c++20, -O0, -fopenmp]
    defines: {ONEDPL_USE_OPENMP_BACKEND: "1"}
    include_dirs: [/opt/oneapi/dpl/2023/include]
probes:
  - name: sort
    headers: [oneapi/dpl/execution, oneapi/dpl/algorithm]
    body: |
      void probe_sort(int* a, int* b) {
          oneapi::dpl::sort(oneapi::dpl::execution::par, a, b);
      }
defaults:
  backend: tbb
  execution_policy: par
```

The `-std=c++NN` flag is parsed automatically to populate each
configuration's C++ standard floor.

## `abicheck probe run`

Compile every (configuration × probe) pair and emit a `MatrixSnapshot`:

```bash
abicheck probe run examples/probes/onedpl.yaml \
    --library onedpl --version 2022.0 --out onedpl-2022.json
```

| Option | Purpose |
|---|---|
| `--library` | Library name stamped into the snapshot (required). |
| `--version` | Version label stamped into the snapshot (required). |
| `-o, --out` | Write the JSON here (default: stdout). |
| `--work-dir` | Keep generated `.cpp`/`.o` files here (default: temp dir). |
| `--no-snapshot` | Compile only; skip the dumper (routing check). |

Per-configuration compile failures (e.g. a compiler missing from `PATH`)
are captured in the snapshot as per-result errors and summarised on
stderr — the run does not abort.

## `abicheck probe compare`

Diff two matrix snapshots and report the findings through the standard
reporter / SARIF / JUnit paths:

```bash
abicheck probe compare onedpl-2022.json onedpl-2023.json -f markdown
```

| Option | Purpose |
|---|---|
| `-f, --format` | `json` (default), `markdown`, `sarif`, or `junit`. |
| `-o, --output` | Write the report here (default: stdout). |
| `--policy` | Built-in policy profile for verdict classification. |
| `--allow-failures` | Diff even when an input matrix has failed probe results. |

The exit code follows the legacy `compare` mapping: `0` = compatible,
`2` = source break, `4` = ABI break.

### Incomplete matrices

The `API_DEPENDS_ON_CONSUMER_ENV` detector only inspects probes that
compiled successfully. If a `probe run` produced failures — most commonly
a compiler missing from `PATH` — every result for that configuration
carries an error and no snapshot. Diffing two such matrices would skip
the failed results and could report `NO_CHANGE` / exit `0`, silently
marking an *untested* matrix as compatible.

To avoid that false-negative, `probe compare` **rejects** an input matrix
that contains failed results, printing the failures and exiting with code
`3`. Pass `--allow-failures` to diff the successful subset anyway; the
report is then marked low-confidence with a coverage warning naming how
many results were skipped.

## Python API

The same capability is available programmatically:

```python
from abicheck.probe_harness import load_probe_spec, run_probe_matrix
from abicheck.diff_build_config import diff_matrix

spec = load_probe_spec("examples/probes/onedpl.yaml")
old = run_probe_matrix(spec, library_name="onedpl", version="2022.0")
new = run_probe_matrix(spec, library_name="onedpl", version="2023.0")
findings = diff_matrix(old, new)
```
