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
  `dumper._run_clang`'s own probe-failure path now recovers a literal,
  explicitly-requested `--target=` via a new `_compiler_options.
  explicit_target_triple` helper, and — only when no explicit target was
  requested either — falls back to a triple string derived from the
  running interpreter's own `sys.platform`. The `sys.platform` guess is
  skipped entirely for CL/MSVC-compatibility mode. Whether CL mode is
  *effectively* in force is resolved by a new `_compiler_options.
  effective_driver_mode_is_cl` helper: the last explicitly-forwarded
  `--driver-mode=<value>` override, in either direction, wins over the
  binary's own name (`clang-cl`/`dpcpp-cl` default to CL mode, a plain
  `clang`/`clang++` defaults to GNU mode) — so `clang --driver-mode=cl`
  genuinely enters CL mode and `clang-cl --driver-mode=g++` genuinely
  leaves it, rather than a name-only guess that could never be revoked.
  It mostly targets Windows regardless of host OS (a real
  `clang-cl -print-target-triple` reports a Windows triple even
  cross-compiled from macOS), so guessing `sys.platform` there would
  misclassify a Windows AST as Darwin on a macOS host. The explicit-
  `--target=` recovery itself stays active under CL mode too, but
  narrowed (`explicit_target_triple(..., cl_style=True)`) to the
  spellings a CL-style driver actually honors — attached, double-dash
  `--target=<value>`; separate-argument, single-dash `-target <value>`;
  and either of those forwarded through clang-cl's documented
  `/clang:<arg>` passthrough (e.g. `/clang:--target=<value>`) — since the
  other spellings (attached single-dash, separate double-dash) complete
  with an "unknown argument ignored" warning and are never applied;
  recovering one of those as if it were real would risk applying the
  wrong platform normalization. Under GNU mode the `sys.platform` guess
  also only applies when the RESOLVED compiler binary IS the plain host
  default by invocation BASENAME (a new `dumper_clang._is_default_clang_bin`
  helper): real Clang derives its own default target from `argv[0]`, so
  basename — not real executable identity, not raw path spelling — is
  what determines its behavior. An explicit `--compiler`/`--compiler-
  prefix` cross-toolchain that `_resolve_clang_bin` actually adopts (a
  documented input, e.g. an Apple-targeting compiler run on Linux)
  carries no relationship to the host OS at all, so a probe failure there
  leaves the target unknown rather than substituting the host's own
  platform, and this now correctly includes a target-prefixed symlink to
  the exact same binary as plain `clang` (a real cross-toolchain wrapper
  shape whose basename alone changes the reported target). Conversely, a
  `--compiler` naming a non-clang-family binary, which `_resolve_clang_bin`
  silently ignores in favor of the plain host default anyway, an
  absolute-path spelling of that exact same native binary (e.g.
  `/usr/bin/clang`), or a native, version-suffixed driver name (e.g.
  `clang-18`, which LLVM/Debian packaging commonly ships — the version
  suffix is stripped before comparing, the same convention
  `dumper_clang._is_cl_style_driver_name` already used), must not
  suppress the guess for what is genuinely still the plain host compiler.
  Separately, the guess is also suppressed whenever forwarded options
  include an unexpanded `@response-file` token
  (`_compiler_options.forwards_response_file`): its contents —
  potentially a `-target`/`--driver-mode=` of their own, which a real
  compiler process honors — aren't visible to this module without
  reading and re-tokenizing the file, so its mere presence is treated as
  "unknown evidence" rather than "nothing else was requested".
  `explicit_target_triple` itself applies the same reasoning one level
  deeper: a recovered target is voided back to `None` when a response
  file token comes *after* it in the forwarded options (a real compiler
  processes arguments left to right, so the file's own invisible
  contents could still override the visible target), but not when the
  response file precedes it (nothing follows the recognized token to
  override it).
  `extract.headers.clang.context.is_darwin_target` itself is unchanged
  and still answers `False` for a bare `None`/empty triple unconditionally
  — the `sys.platform` guess is deliberately synthesized one layer up, in
  the real dump pipeline only, so a direct unit-test construction of the
  parser (which also passes no target triple) keeps its existing,
  conservative "no evidence, no guess" behavior regardless of which OS
  runs the test suite. The `"__Z..."` -> `"_Z..."` structural check itself is
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
