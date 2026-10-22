---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `internal_leak` is now four `compare/` modules, but its test module is still grandfathered above the test cap

Index: [Known gaps](../known-gaps.md)

## What landed

The internal-namespace leak rule moved out of the unclassified root module
`abicheck/internal_leak.py` into `compare/`, as four modules:

- `compare/internal_leak.py` — the detectors (`detect_internal_leaks`,
  `detect_call_graph_leaks`) and the combined `detect_leak_findings` the
  post-processing pass calls.
- `compare/internal_leak_paths.py` — the layout and type-graph walk
  (`compute_leak_paths`) and the path predicates shared with the finding
  builder.
- `compare/internal_leak_graph.py` — the call-graph walk
  (`compute_call_graph_leak_paths`) over the L5 source graph.
- `compare/internal_leak_names.py` — subject classification and the
  `internal_namespace` and `internal_type_leak` name heuristics.

Each module is under the 800-line production cap. Nothing re-exports from
the old `abicheck/internal_leak.py` path, and `architecture/debt.yaml` no
longer records that path.

## What remains

`tests/test_internal_leak.py` still sits above the 1200-line test-file cap.
It is grandfathered on a `no_growth` record at its current length, so it
cannot grow, and it is the only large module left from this migration.
The call-graph and path-selection tests have already been split out into
sibling files. The rest of the module's tests still need to be split by
behavior axis.

## How to check

```bash
python scripts/check_architecture.py
python -m pytest tests/test_internal_leak*.py -q
```
