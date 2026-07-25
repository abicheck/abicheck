<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **PE and Mach-O header-scoped dumps never got an ADR-050 extraction
  contract** (Codex review, PR #624 follow-up): `service.py`'s
  `_try_header_scoped_dump` — the path every real PE/Mach-O `--header`
  compare through the CLI/scan/service layer uses — calls
  `dumper._dump_pe`/`dumper._dump_macho` directly, bypassing
  `dumper.dump()` entirely, so the new contract-population wiring never
  ran for it (the ELF equivalent, `service._dump_elf`, already routes
  through `dumper.dump()` and was unaffected). Factored the wiring out of
  `dump()` into a shared `dumper._attach_extraction_contract()` (now in a
  new sibling module, `dumper_contract.py`, to stay under `dumper.py`'s
  file-size cap) and call it from both `dump()` and
  `_try_header_scoped_dump`, so a real PE/Mach-O header-scoped dump now
  gets the same `contract` population an ELF dump already did.
- **The effective `--lang` mode was missing from the ADR-050 profile
  fingerprint** (Codex review, PR #624 follow-up): the same clang
  executable, with no explicit `-std=`, dumping the same header once with
  `lang="c"` and once with `lang="c++"` produced identical
  `profile_fingerprint`s — `language_standard` only captured an explicit
  `-std=`, even though the actual frontend command genuinely parses a
  different language (`-x c` vs. the C++ default) depending on `lang`.
  Added `_compiler_options.language_standard_field()`, combining the
  explicit `--lang` mode with any explicit `-std=` value; wired into both
  call sites above. Pure content-based language auto-detection (no
  explicit `--lang`, header content alone triggering C++ mode) is still
  not captured — that needs the frontend's own resolved `force_cpp`
  decision threaded out as toolchain metadata, deferred as a narrower
  follow-up.
