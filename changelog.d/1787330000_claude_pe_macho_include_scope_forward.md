### Fixed

- **The PE/Mach-O `dump` CLI path dropped the same declaration-provenance
  distinction the ELF path already got: a build-derived, auto-widened
  include directory could reach provenance the same way an explicit `-I`
  does.** `handle_non_elf_dump()` seeds `includes` with build-derived
  directories (e.g. so a `--sources`-derived umbrella header's own relative
  `#include`s resolve) before calling the native dumper — but that same,
  possibly-widened list was also what reached declaration-provenance
  classification, so a build-derived directory could silently promote a
  private sibling header to `PUBLIC_HEADER` on PE/Mach-O, reproducing the
  exact false-clean result already fixed for ELF's `perform_elf_dump`.
  Fixed by threading a new `public_include_search_dirs` parameter through
  `service.run_dump`/`_finish_native_snapshot` (mirroring `dumper.dump`'s
  parameter of the same name), fed only by the CLI's own genuinely explicit
  `-I` list, with every other caller's behavior unchanged (defaults to the
  pre-existing `includes` fallback).
