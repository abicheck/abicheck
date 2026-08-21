### Fixed

- **The source-only `--depth binary` rejection missed a differently-cased
  spelling.** `depth` is accepted case-insensitively everywhere else
  (`_depth_errors`, `resolve_dump_request_evidence`'s own `.lower()`), but
  `DumpRequest.validation_errors()`'s source-only-plus-binary-depth check
  compared `depth != "binary"` exactly, so `depth="BINARY"` on a source-only
  request slipped past validation and resolved to an empty,
  `collect_mode="off"` request instead of the same rejection `depth="binary"`
  already got. Compared case-insensitively now (Codex review on #814, fresh
  evidence beyond the initial fix).
