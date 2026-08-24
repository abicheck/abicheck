<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Split `handle_non_elf_dump` out of `abicheck/cli_dump_helpers.py` into a
  new sibling module, `abicheck/cli_dump_non_elf.py`, and `_attach_header_
  graph` out of `abicheck/service.py` into a new sibling module,
  `abicheck/service_header_graph_attach.py`.** Both moves are purely
  file-size-cap splits, no behavior change: `cli_dump_helpers.py` was 2001
  lines and `service.py` was 2023 lines, both over the AI-readiness
  2000-line hard cap on `main` itself. Each function's body, signature, and
  docstring are unchanged; both are re-exported under their original name
  from their original module (`cli_dump_helpers.handle_non_elf_dump`,
  `service._attach_header_graph`) so every existing import site,
  `monkeypatch.setattr(...)`/`unittest.mock.patch(...)` call, and doc
  reference keeps resolving unchanged. `abicheck/cli.py` now imports
  `handle_non_elf_dump` directly from the new module instead of via the
  `cli_dump_helpers` re-export. `scripts/check_ai_readiness.py`'s
  `IMPORT_CYCLE_ALLOWLIST` gained both new modules as members of the
  existing, already-accepted CLI-registration/service-routing strongly-
  connected component — each is a split of an already-member module (the
  identical, already-signed-off shape `service_compare_pipeline`/
  `service_dump_pipeline`/`service_input_resolution` established), not a
  new dependency direction, and introduces no init-time deadlock. The one
  `CLI_CONTRACT_ALLOWLIST` entry pinned to `cli_dump_helpers.py`'s own
  direct `dumper.dump()` call site had its line number updated to match
  where that call now sits after `handle_non_elf_dump`'s removal.
