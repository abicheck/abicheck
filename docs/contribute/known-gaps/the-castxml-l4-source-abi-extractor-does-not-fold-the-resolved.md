---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The castxml L4 source-ABI extractor does not fold the resolved EMULATED compiler's identity into either `fact_set.compiler_version` or the D8 TU cache key — attempted once for the persisted half, and REVERTED after a follow-up review caught it as a real regression, not a fix (Codex review, PR #719, three follow-up rounds)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-25): OPEN.** buildsource/source_extractors/castxml.py still resolves cc_bin. compiler_version is _castxml_identity_with_stat_fallback(self.castxml_bin) (line 723), and the comment at line 719 says a `cc_bin --version` probe is still future work.

castxml shells
out to the emulated compiler (`cc_bin`, resolved per compile unit by
`pick_compiler_binary` — the real build's own recorded `argv[0]`, absent
an explicit `--gcc-path` override) purely to discover its built-in
defines/include paths (`docs/learn/architecture.md`), so a header
conditional on `__GNUC__`/`_MSC_VER` can extract differently once that
compiler is upgraded at the same path, even though castxml itself and
its own `--version` probe stay identical — a real gap. The second
follow-up round's fix folded a STAT signature (`dev`/`ino`/`mtime_ns`/
`size`) of the resolved `cc_bin` into `fact_set.compiler_version`
(`_stamp_fact_set_and_coverage()` already has the per-TU `compile_unit`
in scope). The third follow-up round found this made things WORSE, not
better: those stat fields are filesystem-local, so (1) two TUs in ONE
surface resolved to DIFFERENT but same-toolchain drivers (`gcc` for a
`.c` TU, `g++` for a `.cpp` TU — an entirely ordinary mixed-language
build) get different suffixes purely from being different files on
disk, tripping `rollup_fact_set()`'s exact-equality check into
`fact_set_inconsistent` for a perfectly healthy, unchanged surface; and
(2) an identical build run on two different machines/paths (baseline
collected in one CI run, compared against another — an entirely
ordinary workflow) never shares device/inode at all, making every such
cross-machine comparison spuriously inconsistent and silently
suppressing every structured/opaque/source-edge finding. Between
"silently under-detect genuine toolchain drift" (the pre-existing gap)
and "spuriously suppress every finding on completely ordinary mixed-
language or cross-machine comparisons" (the attempted fix), the second
is strictly worse for typical usage, so the stat-based fold was
reverted rather than patched further under review pressure. **Not fixed
here, either half**: a correct fix needs a portable SEMANTIC identity —
a real `cc_bin --version` probe, normalized (mirroring
`_castxml_tool_version`'s existing shape, but for an arbitrary
gcc/clang/MSVC driver rather than castxml specifically) — not
filesystem stat fields, for the persisted half; the D8 cache-key half
additionally needs a wider, per-instance-hook-signature change to
`source_replay.py`'s shared cache-key infra (also used by
`ClangSourceExtractor`, whose analogous `--gcc-path` case resolves once
per extractor construction, not per TU, so its existing zero-arg hook
shape doesn't transfer directly), plus a decision on probing cost (a
version probe per distinct resolved `cc_bin` across a build with mixed
toolchains, cached the same way `_castxml_tool_version`'s `lru_cache`
already is). Left for a dedicated follow-up with its own MSVC-vs-
GCC-vs-Clang version-probe design, not a same-PR reactive patch.
