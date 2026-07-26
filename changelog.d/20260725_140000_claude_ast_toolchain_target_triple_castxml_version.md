### Added

- **`ast_toolchain` now carries a target triple and separately-parsed CastXML
  version fields** (P1 toolchain-provenance audit follow-up). Every
  `_tool_identity_metadata()` probe (frontend and host-compiler alike) now
  also runs `<tool> -dumpmachine` and stores the result under `target_triple`
  (`compiler_target_triple` for the host-compiler entry) — omitted, not
  empty, for a tool that doesn't support the flag (castxml itself, MSVC
  `cl.exe`). A castxml-producer snapshot additionally gets `castxml_version`
  and `castxml_bundled_clang_version` as structured counterparts to the raw
  combined `version` transcript, via a new `CastxmlVersionCheck.provenance_fields()`
  reusing the already-parsed `castxml_policy.evaluate_castxml_version()`
  result rather than re-parsing it. Purely additive keys on the existing
  untyped `ast_toolchain: dict[str, str]` — no schema-version bump needed,
  and a pre-existing snapshot simply lacks the keys.
