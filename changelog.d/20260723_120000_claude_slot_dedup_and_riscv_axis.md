<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A repeated declared header could flip `profile_fingerprint` via its
  ancestor slot token** (Codex review, PR #624): `_slot_token_for_ancestor`
  built its owned-header pair list directly from `declared_headers`, which
  is not itself deduplicated before reaching this function. The same
  header supplied twice in one CLI/manifest invocation (`[a.h, b.h]` vs.
  `[a.h, b.h, a.h]`) retained a duplicate `(identity, relative_path)` pair,
  making `include_sequence` — and the whole `profile_fingerprint` — differ
  even though nothing about the declared surface changed. Now
  deduplicated (`sorted(set(...))`), matching the same rule already applied
  to `header_sequence` and scope's `headers` field.
- **The platform-identity carve-out couldn't verify a `target_triple`
  change for ELF families that share `e_machine` across word sizes**
  (Codex review, PR #624): `EM_RISCV` covers both RV32 and RV64, so a
  `target_triple` change from `riscv32-...` to `riscv64-...` — really just
  an expression of a genuine word-size change — failed verification on its
  own narrow "machine" component even though `elf_class` already confirmed
  the architecture genuinely differs, raising `ProfileMismatchError` before
  `diff_platform.py` could report the more specific `elf_class_changed`.
  `target_triple` (a coarse, composite architecture descriptor, unlike
  `pointer_width`/`endianness` which each map to one specific, independently
  meaningful field) is now verified against the full binary platform axis —
  confirmed by any genuine difference among `machine`/`elf_class`/`ei_data`,
  not just its own single component.
