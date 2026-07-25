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
[`examples/probes/onedpl.yaml`](https://github.com/abicheck/abicheck/blob/main/examples/probes/onedpl.yaml)
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

> **History note:** running probes and diffing matrices used to be two
> standalone commands, `abicheck probe run` and `abicheck probe compare`.
> The ADR-043 CLI reset removed both with no replacement command — the
> underlying Python functions are unchanged and still directly callable
> (below), and `compare --probe-matrix old=<file> --probe-matrix new=<file>`
> still folds a pair of pre-built `MatrixSnapshot` files into a comparison's
> verdict and report.

## Producing and diffing a matrix (Python API)

```python
from abicheck.probe_harness import load_probe_spec, run_probe_matrix
from abicheck.diff_build_config import diff_matrix

spec = load_probe_spec("examples/probes/onedpl.yaml")

# Compile every (configuration × probe) pair for each release.
old = run_probe_matrix(spec, library_name="onedpl", version="2022.0")
new = run_probe_matrix(spec, library_name="onedpl", version="2023.0")

# Per-configuration compile failures (e.g. a compiler missing from PATH)
# are captured in the matrix as per-result errors; run_probe_matrix does
# not abort on them.

findings = diff_matrix(old, new)   # list[Change]: the three kinds above
```

```python
Path("onedpl-2022.json").write_text(old.to_json())
Path("onedpl-2023.json").write_text(new.to_json())
```

Save each `MatrixSnapshot` this way to feed `compare --probe-matrix
old=onedpl-2022.json --probe-matrix new=onedpl-2023.json` instead, so the
findings fold into that comparison's own verdict/report rather than a
standalone diff.

### Incomplete matrices — a known gap since the CLI removal

The `API_DEPENDS_ON_CONSUMER_ENV` detector only inspects probes that
compiled successfully. If `run_probe_matrix` produced failures — most
commonly a compiler missing from `PATH` — every result for that
configuration carries an error and no snapshot; diffing two such matrices
skips the failed results and could report no findings, silently treating
an *untested* configuration as compatible.

The deleted `probe compare` command used to guard against exactly this,
rejecting an input matrix with failed results (exit `3`) unless
`--allow-failures` was passed. That guard lived only in the CLI layer and
was not preserved as a library function — `diff_matrix` itself does not
check for failed results. Until a future pass adds an equivalent check,
inspect `old.results`/`new.results` (or the compare report's coverage
warnings) for per-result errors yourself before trusting a `NO_CHANGE`-looking
diff from an incomplete matrix.
