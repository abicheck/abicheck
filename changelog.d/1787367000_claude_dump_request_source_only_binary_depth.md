### Fixed

- **A typed `DumpRequest` for a source-only side (`path=None`) with
  `depth="binary"` silently passed `validate()`.** The CLI already rejects
  this exact shape (`dump --sources <tree> --depth binary` with no
  `SO_PATH`) as a usage error — a source-only dump has no native artifact
  to report binary evidence from, and `--depth binary` resolves
  `collect_mode` to `"off"`, so the request would otherwise resolve to an
  empty, evidence-free dump instead of failing fast. `DumpRequest.
  validation_errors()` now applies the identical `path is None and
  depth == "binary"` rejection `dump_cmd` already uses (Codex review on
  #814).
