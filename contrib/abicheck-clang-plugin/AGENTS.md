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
  **Confirmed necessary-but-not-sufficient, and independently re-verified
  twice** (a real end-to-end re-run, then a from-scratch reproduction in a
  fresh environment against a real installed Intel oneAPI 2026.1.1 and a
  real upstream `apt.llvm.org` LLVM 22): with both fixes applied, a *full*
  plugin build against upstream LLVM 22 still crashes inside real `icx`
  past `FactsAction::CreateASTConsumer`, in `deriveRootsFromIncludes` —
  same LLVM major, RTTI, and stdlib all matched, still incompatible
  frontend object layout (identical stack both times: SIGSEGV/exit 139 on
  a trivial smoke-test TU). See README.md's "Status update" subsection
  under the Intel section for the full finding. Two follow-ups this
  surfaced: (1) the SHA-256 pin on `plugin-artifact` verifies file
  integrity, not compiler compatibility — there is no machine-checked
  manifest tying an artifact to the exact compiler build/target/RTTI/stdlib
  it was built for, so a mismatched artifact is only caught by the runtime
  smoke test, not rejected upfront — **still open**, a real compatibility
  manifest (schema + pre-load comparison against the resolved compiler) is
  a separate, larger design, not attempted here; (2) the smoke-test failure
  message used to read unconditionally as an LLVM-major mismatch, which was
  actively misleading for this exact crash (major/RTTI/stdlib all already
  matched) — **fixed**: `_finish_clang_plugin` (`actions/collect-facts/
  run.sh`) now takes the resolved `is_intel_llvm` flag and names the
  downstream-fork/frontend-object-layout-drift possibility specifically
  when the loading compiler is Intel's fork, instead of steering a reader
  back to re-checking the major. The "using vendor-bundled LLVM/Clang CMake
  package" log line in `_prepare_clang_plugin` had the same shape of gap —
  it read identically whether the prefix was auto-detected under the
  resolved compiler's own `$CMPLR_ROOT` (real, if incomplete, evidence) or
  supplied via an explicit, unverified `llvm-cmake-prefix` override (which
  can point at an ordinary same-major upstream package, as this
  re-verification's own upstream-LLVM-22 build demonstrates) — also fixed,
  same pass. Regression coverage:
  `tests/test_action_collect_facts_intel_llvm_messages.py`'s
  `TestClangPluginSmokeFailureMessage`/
  `TestClangPluginBundledPrefixProvenanceMessage`. Neither fix changes
  guardrail *behavior* (the refusal still fires, the smoke test still
  fails closed) — only which explanation a reader sees, so don't revert
  either wording change to "simplify" the message without re-reading why
  it was split in the first place.
  **The "wrong source commit" question is now closed, with a debugger-
  confirmed root cause, not just a repeated crash.** A further pass built
  the plugin against the real, public `github.com/intel/llvm` `sycl`
  branch's `v7.0.0` tag (independently confirmed to report `Clang version:
  22.1.0`, matching real `icpx`, tagged 11 days before the actual product
  build date) instead of vanilla upstream LLVM — it crashed with the
  identical stack regardless. A `gdb`-attached debug build pinpoints the
  exact defect: `deriveRootsFromIncludes`'s range-for over `hso.
  UserEntries` dereferences a `std::vector` with `begin_ == 0x0` while
  `end_` is a live, non-null address — the signature of reading a real
  `HeaderSearchOptions::UserEntries` object through the wrong field layout,
  not a plugin logic bug (confirmed by a same-toolchain control: the
  identical plugin, built against vanilla apt LLVM 22 with no RTTI/stdlib
  overrides and loaded into vanilla apt `clang++-22`, exits 0 with a valid
  pack). The likeliest remaining source of the drift, given matching
  source: `icpx`'s `clang-22` binary statically links its own C++ runtime
  (`ldd`/`readelf -d` show no `libc++.so`/`libstdc++.so` dependency at
  all), while this plugin dynamically links a *separate* apt-provided
  `libc++.so.1`/`libc++abi.so.1` — two nominally-compatible but
  independently-built `libc++` copies, not guaranteed byte-identical
  without an externally-verified match. See README.md's "Root-cause
  precision" subsection for the full writeup. This sharpens, but does not
  change, the section's conclusion: a fix needs Intel's actual internal
  build (the statically-linked runtime specifically, not just the
  Clang/LLVM source tree) — a third attempt should not spend time hunting
  for a different public source commit, that question is answered.
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
