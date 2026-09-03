### Changed

- **`abicheck dump`'s real ELF run now executes through the same typed
  pipeline `compare`'s implicit-dump operand and `scan`'s candidate
  resolution already share (`execute_dump_request`), instead of its own
  independent `perform_elf_dump` pipeline.** CLI cleanup phase two, PR C:
  the three resolvers this cleanup exists to converge (`dump`, `compare`'s
  implicit dump, `scan`'s candidate) now all route ELF extraction through
  one shared implementation rather than three hand-written ones kept in
  sync by review discipline — the structural prerequisite `.abicheck.yml`
  build-config removal (PR F/3C) has been blocked on.
  One real, user-facing behavior change falls out of it: `dump`'s L4
  source-ABI (`--depth source`) extractor default changes from an
  accidental **clang** (an unresolved `header_backend` forwarded verbatim
  to the write-time embed) to **castxml**, matching `compare`'s implicit-dump
  operand and the typed `DumpRequest` API's existing default — the same
  frontend `dump`'s own L2 header-AST parse already defaults to. Pass
  `--ast-frontend clang` explicitly to keep the previous L4 extractor.
  The legacy `-p`/`--compile-db` auto-match, `--compile-db-filter`, and
  `--build-query`/`--build-compile-db` all still behave identically — the
  migration threads the legacy match through as an explicit input to the
  shared pipeline's own precedence rule rather than dropping it.
  PE/Mach-O `dump` (`handle_non_elf_dump`) is unmigrated — no PE/Mach-O
  toolchain was available to verify a migration against.
  Three follow-up fixes to the same migration (review, before release): the
  real ELF run now also forwards its resolved collect mode to the L2
  include/compile seed (so a `--sources` tree with no compile database
  still runs its zero-config inferred build query, matching the retired
  `perform_elf_dump`'s behavior), always selects L4 source replay's
  compiler from the L3 build-context fold once it applies (matching
  `scan`'s own candidate resolution), and always treats an explicit
  `--config`/`--build-query` given on the `dump` command line as trusted
  operator input for this execution step (matching both flags' documented
  CLI contract) rather than gating them behind the deprecated, always-off
  `--allow-build-query` no-op flag — all three were silently dropped or
  misrouted kwargs in the initial migration, not new behavior.
