---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A failed header parse on PE or Mach-O falls back to export-table mode and can exit 0

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-11-01): OPEN.** False-clean risk.

## Evidence

`workflows/dump/header_scoped.py` (`_try_header_scoped_dump`, the direct
PE/Mach-O route) wraps the header-scoped dump in `except Exception`, emits a
`UserWarning` ("Header-based ABI scoping unavailable ... Falling back to
export-table mode -- --header/--include were ignored") and returns
`(None, "header-backend-unavailable")`. The caller then dumps the export
table only. The comparison records `requested_depth: binary`
(`requested_depth_source: implicit`) and `header_context_status:
not_evaluated`, and a header-only change is simply not seen, so the run can
exit 0. On ELF the same missing-include failure fails the run.

Reproduced on the macOS integration lane: in
`tests/test_sided_include_labelled_composition.py`, the negative control that
drops the shared include roots exits 0 on macOS and fails on Linux. That test
pins the darwin behaviour (the warning, and binary evidence depth) until this
is fixed.

## Intended resolution

An explicitly supplied `--header` whose parse fails is a failed acquisition
(AGENTS.md "Weaker evidence narrows conclusions": a failed extractor is an
error or an explicit `FAILED` fact, never an empty surface), on every binary
format. The narrower fallbacks that are deliberate (the MSVC mangling
mismatch, `mangling-fallback`) need their own ruling, because a header set
that parses but matches no export is a different state from one that does
not parse. Flip the darwin branch of that test's negative control when this
is fixed.
