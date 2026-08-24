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
  docstring are unchanged. `service._attach_header_graph` is re-exported
  under its original name from its original module (`from
  .service_header_graph_attach import _attach_header_graph as
  _attach_header_graph`) so every existing import site,
  `monkeypatch.setattr(...)`/`unittest.mock.patch(...)` call, and doc
  reference keeps resolving unchanged. `handle_non_elf_dump` has **no**
  equivalent re-export in `cli_dump_helpers.py` — every caller (`cli.py`'s
  `dump_cmd`, and this repo's own tests) now imports it directly from the
  new `abicheck.cli_dump_non_elf` module instead; the four shared `Protocol`
  types both `perform_elf_dump` (still in `cli_dump_helpers.py`) and
  `handle_non_elf_dump` take as parameters were pulled out into a further
  new, dependency-free leaf module, `abicheck/cli_dump_protocols.py`, so
  neither of the two split modules needs to import the other for them.
  `scripts/check_ai_readiness.py`'s `IMPORT_CYCLE_ALLOWLIST` gained
  `service_header_graph_attach` as a member of the existing, already-accepted
  CLI-registration/service-routing strongly-connected component — a split of
  an already-member module (the identical, already-signed-off shape
  `service_compare_pipeline`/`service_dump_pipeline`/
  `service_input_resolution` established), not a new dependency direction,
  and introducing no init-time deadlock. `cli_dump_non_elf` needed no such
  entry: extracting the shared Protocol types into their own leaf module
  means it never joins that cluster in the first place. The one
  `CLI_CONTRACT_ALLOWLIST` entry pinned to `cli_dump_helpers.py`'s own
  direct `dumper.dump()` call site had its line number updated twice to
  match where that call now sits, first after `handle_non_elf_dump`'s
  removal and again after the Protocol-type extraction.
