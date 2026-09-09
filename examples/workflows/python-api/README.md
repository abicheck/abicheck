# Workflow: check compatibility from a Python script

**Task:** "I want to run this check from my own build script or test suite,
not by shelling out to the CLI — how do I call abicheck from Python?"

This is a Phase 5 slice of the examples/catalog split (retired plan
record, `docs/contribute/plans/index.md`) — a small,
curated, task-oriented example independent of the 197-case calibration
catalog under `catalog/cases/` (which exists to calibrate detectors, not to
teach the CLI). See `../../CLAUDE.md` for the rest of this curated set
(Phase 5 is complete, 7 of 7).

## The project

A tiny shared library, `shapes`, drops a function between releases:

```text
v1/shapes.h   double square_area(double side);
              double square_perimeter(double side);
v2/shapes.h   double square_area(double side);   -- square_perimeter removed
```

## Run it

```bash
cd examples/workflows/python-api

# Build both releases as shared libraries
python3 build_shared_lib.py -fPIC -g v1/shapes.c -o libshapes_v1.so
python3 build_shared_lib.py -fPIC -g v2/shapes.c -o libshapes_v2.so

# Run the check from a Python script instead of the CLI
python3 check.py
```

`check.py` is the whole workflow — the same
[`run_compare`](../../../docs/reference/python-api-reference.md) function
the `compare` CLI command itself calls, imported directly:

```python
import sys
from pathlib import Path

from abicheck.service import run_compare

result = run_compare(
    Path("libshapes_v1.so"),
    Path("libshapes_v2.so"),
    old_headers=[Path("v1/shapes.h")],
    new_headers=[Path("v2/shapes.h")],
)

print(f"Verdict: {result.diff.verdict.value}")
for change in result.diff.changes:
    print(f"  - {change.kind.value}: {change.description}")

sys.exit(result.exit_decision.code)
```

## What you get

```text
Verdict: BREAKING
  - func_removed: Public function removed: square_perimeter
```

Exit code is `4` (ABI break) — same exit code the CLI would give
(`result.exit_decision.code` is the identical, fully-explainable exit
decision `compare` renders; see
[Exit Codes](../../../docs/reference/exit-codes.md)). `result.diff` is the
same `DiffResult` the CLI's own reporter formats, so a script can inspect
`result.diff.changes` (each with `.kind`/`.severity`/`.description`) instead
of parsing text or JSON output — useful for a build step that wants to add
its own logic (e.g. "fail the build only for a specific `ChangeKind`") on
top of the same classification.

## Next steps

- Want the full request/response shape? `run_compare` is a keyword-argument
  shim over `CompareRequest`/`CompareResult` — see the
  [Python API Reference](../../../docs/reference/python-api-reference.md)
  for every field and function `abicheck.service` publishes (dump, scan,
  policy/suppression loading, and more).
- Want JSON instead of inspecting Python objects directly? `render_output`
  (also in `abicheck.service`) renders a `CompareResult` through the same
  reporter the CLI's `--format json`/`--format sarif` flags use.
- Want this exact check running in CI? See
  [`compare-release`](../compare-release/README.md) for the CLI-invocation
  equivalent, or [the GitHub Action](../../../docs/use/github-action.md) to
  run it as a PR check without writing any script at all.
