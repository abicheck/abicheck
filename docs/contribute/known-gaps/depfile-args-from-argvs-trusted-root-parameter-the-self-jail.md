---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `depfile_args_from_argv()`'s `trusted_root` parameter — the self-jail vulnerability is closed, real production wiring not implemented

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-25): OPEN.** grep finds no `trusted_root=` argument at any production call site; only buildsource/include_graph.py defines the parameter (lines 119-216), so production callers still drop the @file token.

Closing
the vulnerability (a compile unit's own `directory` field, attacker-
controlled for a unit sourced from an untrusted build pack, was used as
both the resolution base *and* the trust jail for expanding an unexpanded
`@response-file`) required the three production call sites
(`ClangIncludeExtractor.extract_from_build`,
`ClangPreprocessorExtractor.capture_macros`, `preprocessor_scan._depfile_context`)
to fall back to the existing safe "drop the token" behavior, since none of
them currently supply an independently-trusted `trusted_root` (Codex
review). **Not implemented**: threading a genuinely-trusted root into
those three call sites so response-file expansion works again for this
secondary L5/S2-scoping path. This is real, non-trivial plumbing, not a
one-line fix: `BuildEvidence.build_root`/`source_root` exist as fields but
no adapter (`compile_db.py`, `cmake_file_api.py`, `ninja.py`, `bazel.py`,
`make.py`) actually populates them today, so there is no already-flowing
trusted value to read off the model — the real anchor would have to be
threaded as a new parameter from `inline.collect_inline_pack()`'s own
`sources`/`build_info` CLI arguments (or, for the separate Flow-2
`abicheck_inputs/` ingest path in `inputs_pack.py`, the pack's own `root:
Path` already used for `_safe_pack_path` containment) through several
call layers in `inline.py` (already WARN-flagged oversized) and
`preprocessor_facts.py`. The functional impact of the current gap is
narrower than it first appears: `build_context.py`'s own `@file`
expansion (correctly jailed to the compile database's own directory since
the first response-file fix in this PR) already expands a
`compile_commands.json`-sourced `CompileUnit.argv` *before* it reaches
these three call sites, so they rarely see a raw, unexpanded `@file`
token for that primary path in practice — the gap mainly affects the
Flow-2 untrusted-pack path this fix was specifically about securing in
the first place. Confirmed via the full local suite (20935 passed) that
disabling expansion at these three call sites introduces no test
regressions.
