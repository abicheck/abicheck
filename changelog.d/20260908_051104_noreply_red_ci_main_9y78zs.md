<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Darwin extraction reliably strips Mach-O's Itanium mangled-name
  linker decoration even when the target-triple probe fails.** The
  `--ast-frontend clang` header-AST backend recognized Darwin's leading
  double-underscore decoration (`"__Z..."` for a real `"_Z..."` Itanium
  mangled name) only when `dumper._configured_target_triple`'s
  `-print-target-triple` subprocess probe of the configured compiler
  succeeded and was recognized as Darwin; a probe failure (any of several
  plausible real CI causes — compiler resolution mismatch, a sandboxed
  runner, a flag the resolved compiler build doesn't support) silently
  disabled the whole normalization on a genuinely Darwin host, leaving
  `Function.mangled`/`Variable.mangled` still decorated and mismatched
  against the binary's own (correctly normalized) export table — the
  exact symptom behind a self-comparison of an unchanged Mach-O library
  reporting a spurious `COMPATIBLE_WITH_RISK` verdict.
  `extract.headers.clang.context.is_darwin_target` now falls back to the
  running interpreter's own `sys.platform` when the probed triple is
  unavailable *and no explicit `--target=` was requested* — recovered via
  a new `_compiler_options.explicit_target_triple` helper wired into
  `dumper._run_clang`'s own probe-failure path — so an explicit,
  unprobeable cross-target request is never silently reinterpreted as the
  host platform. The `"__Z..."` -> `"_Z..."` structural check itself is
  now the single canonical `model.mangled_name.strip_macho_itanium_
  decoration` helper, reused by `extract.headers.clang.context.
  strip_darwin_itanium_decoration`, `model.mangled_name.
  _itanium_strip_prefix`, and `dumper_hybrid._macho_normalize_mangled`
  (previously three independent copies of the identical check).
  Deliberately **not** applied to the castxml header-AST backend's own
  mangled-name production: castxml's `mangled` attribute never carries
  this decoration to begin with, so stripping it unconditionally there
  would instead corrupt the one real case that shape can mean for that
  backend — a literal, explicit `asm("__Zfake")` assembler-label
  declaration, which castxml reports verbatim regardless of target.

- **`--write markdown=...`'s written report is always readable as UTF-8.**
  `tests/test_presentation_analysis_separation.py` read a `compare
  --write markdown=...` report file with the platform-default text
  encoding instead of `encoding="utf-8"`, failing to decode a non-ASCII
  character on Windows (`cp1252`). The report writer itself
  (`_safe_write_output`) already wrote every format with explicit
  `encoding="utf-8"`; only the test read it back without pinning the same
  encoding.

<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Changed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
