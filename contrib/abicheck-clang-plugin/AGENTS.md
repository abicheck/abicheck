# AGENTS.md — `contrib/abicheck-clang-plugin/`

Agent-facing companion to `README.md`, which documents the plugin's design,
output format, and CLI usage in depth — **read `README.md` first**; this
file is the short "how do I safely change this" guide `README.md` doesn't
try to be. See `/AGENTS.md` for the canonical project-wide contract.

## What this is, in one paragraph

`AbicheckFactsPlugin.cpp` is a Clang plugin that emits abicheck's
`source_facts/*.jsonl` directly from the AST during a normal compile — a
faster, optional alternative to the portable `abicheck-cc` compiler wrapper
(`abicheck/cc_wrapper.py`) and full source scan. It is **never a required
gate in main abicheck CI** (ADR-038 "Plugin injection"): it's ABI-locked to
the loading clang's LLVM major, so one build cannot serve every host clang.
The reference implementation it must match is
`abicheck/buildsource/source_extractors/clang.py`
(`source_abi_from_clang_ast`) — **not** the castxml recipe (`base.py`).

## LLVM-major sensitivity — the thing to never forget

A build of this plugin only loads into the exact clang it was built against
(shared-library ABI, not just API). Consequences for how you work here:

- Don't assume a locally-built `.so` from one LLVM version is reusable
  against another — `.github/workflows/clang-plugin.yml` validates a
  **matrix** of LLVM/Clang majors (16-22 as of this writing; check the
  workflow for the current set) precisely because there is no single
  portable artifact.
- If you add code that depends on a Clang/LLVM API that changed shape across
  those majors, either guard it with a version check or confirm the whole
  matrix still builds — a change that only compiles against the newest
  major silently breaks the older legs. `clang::FileEntry::getName()` is one
  concrete example: it exists on 16/17 (where `SourceManager::fileinfo_*`
  iterates `const FileEntry *`) but was removed upstream by the time LLVM
  reached 22 — `AbicheckFactsPlugin.cpp`'s `fileEntryKeyName` overload for
  that type is now guarded `#if CLANG_VERSION_MAJOR < 18` (18 is where the
  iteration key itself switched to `FileEntryRef`, which still has
  `getName()`), instead of relying on overload resolution alone to make the
  now-uncompilable overload unreachable dead code.
- **A vendor/downstream LLVM fork (Intel's icpx, Apple's clang, etc.)
  reporting the same `__clang_major__` as an apt.llvm.org major does not mean
  the plugin builds or loads against it.** This CI matrix only proves parity
  against vanilla apt.llvm.org majors; a fork can diverge in API (its own
  patches) or ABI (struct/vtable layout) independently of that number, and
  most forks don't ship the LLVM/Clang CMake devel package needed to build
  against them at all (confirmed for Intel's icpx/icx: its apt packages
  carry `IntelSYCL`/`IntelDPCPP` CMake helpers, never `LLVMConfig.cmake`/
  `ClangConfig.cmake`). Building against that fork's *own* source at the
  matching release commit is the only reliable path if support is ever
  wanted; a green matrix leg here is not evidence toward that.
  **Confirmed, not just theorized, for Intel's fork**: real testing against
  a real `icpx` install found a same-major distro Clang build both (a)
  fails to load outright with RTTI enabled and (b) crashes once it *does*
  load, from a libstdc++-vs-libc++ standard-library ABI mismatch crossing
  the Clang plugin interface — see README.md's "Intel oneAPI (icx/icpx) —
  experimental, not certified" section for the full findings and the
  `ABICHECK_PLUGIN_RTTI`/`ABICHECK_PLUGIN_STDLIB` CMake overrides this
  produced. `actions/collect-facts/run.sh`'s `_prepare_clang_plugin`
  refuses the same-major apt fallback for this specific fork (detected via
  the `__INTEL_LLVM_COMPILER` predefined macro) rather than silently
  attempting a build known to produce a broken artifact — don't revert that
  refusal to "fix" a user's build failure; point them at `llvm-cmake-prefix`
  (a genuine vendor SDK) or `plugin-artifact` (a pre-certified binary)
  instead, per the README's recommended distribution model.
  **Confirmed necessary-but-not-sufficient in a later re-run**: with both
  fixes applied, a *full* plugin build against upstream LLVM 22 still
  crashes inside real `icx` past `FactsAction::CreateASTConsumer`, in
  `deriveRootsFromIncludes` — same LLVM major, RTTI, and stdlib all
  matched, still incompatible frontend object layout. See README.md's
  "Status update" subsection under the Intel section for the full finding.
  Two follow-ups this surfaced, still open: (1) the SHA-256 pin on
  `plugin-artifact` verifies file integrity, not compiler compatibility —
  there is no machine-checked manifest tying an artifact to the exact
  compiler build/target/RTTI/stdlib it was built for, so a mismatched
  artifact is only caught by the runtime smoke test, not rejected upfront;
  (2) the smoke-test failure message reads as an LLVM-major mismatch, which
  is misleading when the major already matches and the real cause is
  fork-internal ABI drift — don't "fix" a report of this failure by
  re-checking the major, and don't reword the message without keeping both
  causes distinguishable.
- This asymmetry is *why* the plugin is optional infrastructure: Full source
  scan and the `abicheck-cc` wrapper remain the portable, always-supported
  producers. Don't propose making the plugin required without addressing
  this constraint first.

## The conformance gate is the actual spec

`tests/conformance.py` (ADR-038 C.6) compiles one fixture TU two ways with
the *same* clang — through this plugin and through the `abicheck-cc` wrapper
pinned to the clang extractor — then asserts the two `abicheck_inputs/`
packs are **entity-equivalent**: same `SourceEntity.identity()` set, same
`signature_hash`/`type_hash`/`body_hash`/`value`/`visibility`/`api_relevant`
per entity (macros compared leniently on value; everything else strict).

**Any change to what this plugin emits, or to how it hashes an AST subtree,
must keep this gate green — or the divergence is a real bug, not a
plugin-side style choice.** The plugin achieves parity by serializing with
clang's own JSON AST dumper in-process and porting `clang.py`'s
`_alpha_rename_map`/`_canonical`/`_subtree_hash` onto that JSON (see
`PrunedJsonParser` in the `.cpp` for the performance-motivated parse
strategy). If you touch hashing/canonicalization on either side
(`AbicheckFactsPlugin.cpp` or `clang.py`), change both together and re-run
the conformance test — don't let them drift and rely on the gate to catch it
later; it should catch it *before* you move on, not after.

Run it locally against a built plugin:

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(llvm-config --cmakedir)/.."
cmake --build build
python contrib/abicheck-clang-plugin/tests/conformance.py \
  --plugin build/libabicheck-facts.so --clangxx clang++
```

Other tests in `tests/`: `scan_flow.py` (end-to-end: plugin pack → `abicheck
dump --build-info` → binary matching, proving the pack is consumable, not
just entity-equivalent) and `test_public_roots_diagnostic.py` (the
loud-not-silent empty-pack diagnostics described in `README.md`).

## Performance is a documented, measured property — don't regress it silently

`README.md`'s "Pruned parse (perf)" section records a specific, measured
compile-time-overhead number (from-scratch LLVM build, 143 TUs) as the
current state after the pruned-parser optimization. If you change the
dump/parse/canonicalize path, re-measure with
`ABICHECK_PLUGIN_PROFILE=1` (or `ABICHECK_PLUGIN_PROFILE_LOG=<path>` for
parallel builds) and update that section rather than leaving a stale number
— the whole point of the plugin over the wrapper is the avoided second
parse, so a performance regression here undermines the plugin's reason to
exist.

## What NOT to do

- Don't make this plugin a required dependency of core abicheck CI — its
  LLVM-major lock-in is exactly why it stays optional (ADR-038).
- Don't change hashing/canonicalization here without also checking
  `clang.py` and re-running `tests/conformance.py` — see above.
- Don't hand-reproduce clang's JSON AST dump instead of using clang's own
  dumper (`Decl::dump(os, false, ADOF_JSON)`) — the whole parity argument
  depends on consuming the *exact* `-ast-dump=json` path the wrapper's clang
  backend also consumes.
- Don't add a second frontend pass (e.g. a follow-up `-E`/`-ast-dump`
  invocation) — "zero extra parse" is the plugin's entire value proposition
  over the wrapper; if a feature needs one, it belongs in the wrapper path
  instead.
