<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **L3 build evidence is now applied automatically to L2 header parsing**
  (P0.3) — when `--sources`/`--build-info` L3 evidence (or a typed
  `DumpRequest`/`CompareRequest` `InputSpec.sources`/`build_info`) is
  already available for a run, abicheck now derives the real build's
  standard, defines/undefines, include search paths, sysroot, target
  triple, and ABI-relevant flags (`-fPIC`, `-fno-omit-frame-pointer`, ...)
  from the matching `CompileUnit`(s) and feeds them into the `castxml`/
  `clang` header-AST invocation, instead of parsing headers with only
  user-supplied `--gcc-options`/`compile:` context or none at all. The
  existing `header_parse_context_drift`/`header_build_context_mismatch`
  advisory findings now correctly stop firing once context is genuinely
  applied (`AbiSnapshot.parsed_with_build_context` is stamped), while
  continuing to fire unchanged when no compile evidence is available. When
  two or more `CompileUnit`s that reference the same public header disagree
  on an ABI-relevant field (a different `-std=`/`-fPIC`/target/macro set),
  the new `HeaderCompileContextAmbiguousError` fails the run closed rather
  than silently guessing which context to use — unless the caller's own
  explicit `CompileContext`/`--gcc-options` already pins the disputed field
  (e.g. an explicit `-std=c++20` resolves a `-std=`-only disagreement across
  matched compile units), in which case only a genuinely *unpinned* field
  still fails closed. See `abicheck/buildsource/header_compile_context.py`
  for the header↔`CompileUnit` matching heuristic and scope boundary
  (single-context applied automatically; genuinely ambiguous multi-context
  input is reported, not resolved). A two-token compile-DB flag whose
  operand is a separate argv entry (`-target aarch64-linux-gnu`,
  `--sysroot /sdk`, `-isysroot /sdk`) no longer forwards a dangling,
  operand-less switch alongside the equivalent structured `--target=`/
  `--sysroot=` rendering. An explicit `CompileContext.gcc_options`/
  `sysroot` now reliably wins over a conflicting L3-derived flag in the
  actually-constructed command (both are folded into trailing tokens ahead
  of `gcc_option_tokens`, matching the derived-leads/explicit-wins
  precedence this feature was already documented to have) — previously the
  structured fields rendered *before* the derived tokens, letting a later,
  conflicting derived flag silently win instead. Sysroot/include-path
  flags derived from a `CompileUnit` are now rendered forward-slash-
  normalized (`.as_posix()`) on every platform, matching the convention the
  `castxml`/`clang` header command builders already use elsewhere, instead
  of a native `\`-separated Windows path the frontends don't parse the same
  way.
