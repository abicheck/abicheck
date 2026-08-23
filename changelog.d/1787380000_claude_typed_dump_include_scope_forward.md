### Fixed

- **The shared typed-dump resolver used by `compare`'s implicit-dump
  operand and `dump`'s typed `DumpRequest` API dropped the same
  declaration-provenance distinction the CLI resolvers already got.**
  `service_input_resolution._resolve_side_snapshot_impl()` seeds `includes`
  with build-derived directories before calling `service.resolve_input()`
  — but that same, possibly-widened list was also what reached
  provenance classification on all three binary formats, so a
  build-derived directory could silently promote a private sibling header
  to `PUBLIC_HEADER`, reproducing the false-clean result already fixed for
  the ELF/PE/Mach-O `dump` CLI paths. Fixed by threading the new
  `public_include_search_dirs` parameter (mirroring `dumper.dump`'s
  parameter of the same name) through `service.resolve_input()` and its
  whole-snapshot cache wiring, fed only by each side's own genuinely
  explicit `-I` list (`InputSpec.includes`, pre-seeding).
