### Changed

- **`abicheck dump` now builds one typed `DumpRequest` from its CLI
  parameters, and `--dry-run` renders from the shared resolve-only pipeline**
  (CLI cleanup phase two, PR 3A blocker 5). `dump_cmd` previously built no
  request at all: `--dry-run` and the real run each read its own hand-derived
  locals, so the preview was a second implementation of resolution kept in
  step by review discipline. The request is built from the CLI's
  *already-resolved* values (compile context, frontend, explicit-language
  decision), so it records the run rather than forming a parallel opinion
  about it, and `tests/test_dump_request_from_cli.py` pins the fields the
  pipeline derives independently — header set, collect mode, header backend —
  as equal to the CLI's own. Real ELF/PE/Mach-O execution is unchanged and
  still runs through `perform_elf_dump`/`handle_non_elf_dump`.
- **The `scan --against` baseline-context-reuse rule now lives in one shared
  primitive** (`service_input_resolution.BaselineReuseContext` /
  `resolve_baseline_compile_context`, PR 3A blocker 6). Whether the
  candidate's own L3→L2 folded `CompileContext` may also parse the baseline
  — the decision `-H old=PATH`/`-I old=PATH` turns on — was a four-clause
  boolean inline in `scan_engine.run_scan_core` that took three review rounds
  to get right. `service_input_resolution._resolve_side_snapshot_impl` accepts
  the same context as an optional `baseline_reuse_hint` and reports the
  identical answer on `SideResolution.baseline_compile_context`, so the
  migration that finally routes `scan`'s candidate resolution through the
  shared resolver inherits the rule instead of reimplementing it. Behaviour is
  unchanged; a caller that passes no hint is unaffected.
