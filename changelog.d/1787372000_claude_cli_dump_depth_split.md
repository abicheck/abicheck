### Changed

- **Split `resolve_dump_depth`/`resolve_dump_collect_context` out of
  `cli_dump_helpers.py` into a new leaf module, `cli_dump_depth.py`.** Purely
  to stay under the AI-readiness 2000-line hard cap after merging PR 3A's two
  migrations — no behavior change. Both names keep resolving from
  `abicheck.cli_dump_helpers` via an explicit `as`-aliased re-export, so
  every existing `from .cli_dump_helpers import resolve_dump_depth`/
  `resolve_dump_collect_context` caller is unaffected.
