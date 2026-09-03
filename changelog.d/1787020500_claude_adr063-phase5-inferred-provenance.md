### Fixed

- **Inferred header provenance no longer counts as evidence a header
  backend ran.** A snapshot document predating the `from_headers` key has
  it *guessed* from "does this carry declarations at all" — which a legacy
  DWARF-only dump satisfies exactly as a header dump does, which is why
  `serialization.py` already marks the guess with `from_headers_inferred`.
  The case-(a) legacy-load correction was reading that inferred `True` as
  real provenance, so such a document's placeholder `is_mutable=False`,
  `default=null`, `deprecated=null`, `is_restrict=false` and
  `access="public"` still bridged to `PRESENT`. It now takes recorded
  provenance only (`from_headers and not from_headers_inferred`); unknown
  fails closed, the same way an absent `ast_producer` is read as "possibly
  clang-family" rather than silently trusted.
