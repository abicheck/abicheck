### Fixed

- **BTF/CTF cyclic qualifier chains (e.g. a malformed `CONST -> VOLATILE ->
  CONST` reference loop) now mark extraction incomplete** — the resolvers'
  own cycle-detection guard previously substituted a placeholder
  (`"..."`/`0`) without recording it, so a struct member referencing such a
  cycle was emitted with a plausible-looking degraded fact and
  `extraction_partial=False`.
- **DWARF advanced-channel packed-struct detection now marks incomplete
  when a struct *member's* own type is unresolvable** — distinct from the
  existing malformed-typedef-*target* case, this is a member inside an
  already-resolved named or anonymous struct; the failure was previously
  swallowed inside `_get_type_align`, indistinguishable from a legitimate
  composite-type skip, so a packing change hiding behind that one bad
  member could be missed under `--require-complete-analysis`.
- **`examples/case15_noexcept_change`'s expected verdict corrected to
  `BREAKING`** — this session's earlier fix to `_get_cfi_source` (real
  pyelftools `EH_CFI_entries()`/`CFI_entries()` method names, previously
  misspelled so CFI extraction was dead code against every real binary)
  made `frame_register_changed` fire for the first time ever against a
  real binary, correctly detecting that removing `noexcept` from
  `Buffer::reset()` changes GCC's CFA register convention (`rsp` -> `rbp`)
  for the now-throwing implementation — a real, deterministic,
  binary-level signal the case's ground truth predates. Regenerated the
  derived example docs, `agent-evals/skills/skill-eval-pack.json`, and the
  `runtime-floor-raised` Harbor eval task to match.
