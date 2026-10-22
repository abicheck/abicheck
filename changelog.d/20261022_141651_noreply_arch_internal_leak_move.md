### Changed

- **Internal-namespace leak detection now lives in `compare/`** — the
  root module `abicheck/internal_leak.py` is retired and split into four
  `compare` modules, each under the 800-line production cap:
  `compare/internal_leak.py` (the detectors `detect_internal_leaks`,
  `detect_call_graph_leaks` and the combined `detect_leak_findings`),
  `compare/internal_leak_paths.py` (the layout and type-graph walk),
  `compare/internal_leak_graph.py` (the call-graph walk) and
  `compare/internal_leak_names.py` (subject classification and the
  `internal_namespace`/`internal_type_leak` heuristics). There is no
  re-export at the old path; import from the owning module above. Behavior
  is unchanged.

