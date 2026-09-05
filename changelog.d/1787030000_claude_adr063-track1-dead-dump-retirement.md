### Removed

- **Retired the `dump` CLI's two dead execution helpers.**
  `cli_dump_helpers.perform_elf_dump` (ELF) and
  `cli_dump_non_elf.handle_non_elf_dump` (PE/Mach-O) lost their production
  caller when ADR-063 Phase 1 routed `abicheck dump`'s real run for either
  binary format through the shared typed executor
  (`service_dump_pipeline.execute_dump_request`); only their own unit tests
  kept them alive. Both are deleted, along with `abicheck/cli_dump_protocols.py`,
  whose `Protocol` types existed solely to describe the callables `dump_cmd`
  passed into them. No behaviour change: nothing invoked either function.
  The `dump` CLI behaviours whose only test home was one of the two are now
  pinned against the live path in
  `tests/test_dump_cli_execution_behaviors.py`, including the
  dependency-scoping `header_roots` invariant as a cross-path equivalence
  against `service.run_dump`'s own choke point.
