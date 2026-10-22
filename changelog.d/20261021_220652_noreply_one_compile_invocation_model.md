### Changed

- **Clang replay argv is rendered from one parsed compile invocation** — the L4 and L5 AST-dump and macro argv, the castxml replay argv, the public-root include probe and the replay cache key now read one parsed `CompilerInvocation` per compile unit instead of rescanning the raw `argv` each time. The replayed argv is unchanged: a golden corpus of gcc, clang, clang-cl, relative and `env -C` cases pins the clang and castxml argv against the pre-change output. Internal only; no flag, schema or report output changes.
