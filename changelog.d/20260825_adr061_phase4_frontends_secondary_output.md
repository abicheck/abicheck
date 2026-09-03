### Changed

- Began ADR-061 Phase 4 (thin CLI and Python API) by creating the
  `abicheck.frontends` responsibility package and moving its first tenant
  into it: the flat `abicheck/cli_secondary_output.py` (the shared
  `--write FORMAT=PATH` Click option factory and its coherence validator)
  is now `abicheck.frontends.cli.options.secondary_output`, re-exported
  from `abicheck.frontends.cli.options`. The module had zero first-party
  imports, so the move is purely mechanical; its four call sites
  (`cli_options.py`, `cli_scan_helpers.py`, `cli_compare_helpers.py`,
  `cli_compare_release.py`) now import it from the new package and are
  otherwise unchanged. The much larger remaining work this phase names —
  the interdependent `cli_options.py` option-declaration cluster, and
  reducing `cli.py`/`service.py` to thin registration/delegation facades —
  stays deferred: both need Phase 3's per-artifact resolve/execute
  pipeline to exist first, which has so far only relocated one
  dependency-free contract type (`ResolvedArtifactPlan`).
