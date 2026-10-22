---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A `gcc -aux-info` names-only header frontend does not pay for itself (2026-10-22)

Index: [Known gaps](../known-gaps.md)

**Status (measured 2026-10-22 at `6f640dd33`): NEGATIVE_RESULT.** The header frontend is about 1-3% of a cold `compare --no-baseline` run, so replacing it cannot give a meaningful speedup, and `-aux-info` is C-only, so it cannot serve the C++ libraries abicheck targets. Do not build it.

## The proposal

Add a fast "names-only" header frontend for the single-build declared-vs-exported
audit (`compare --no-baseline LIB -H headers`, `public_not_exported`), based on
`gcc -aux-info out.txt -fsyntax-only t.c`. GCC prints one line per function
prototype it sees, with its location, for example
`/* inc/greet.h:2:NC */ extern int greet_exported (int);`.

## Measurement

Intel MKL from conda-forge: `lib/libmkl_rt.so.3` and `include/mkl.h`, which
includes about 46 `mkl*.h` headers. The driver TU was `#include "mkl.h"`. Tools:
gcc 13.3, clang 18.1.3, castxml 0.7.0. The `compare` runs used a fresh
`XDG_CACHE_HOME` each time, so the AST cache started empty. Frontend times are the
median of three runs.

| Step | Wall time | Share of the cold `compare` |
|---|---|---|
| `abicheck compare --no-baseline libmkl_rt.so.3 -H mkl.h -I include`, cold cache (default backend, and again with `ABICHECK_AST_FRONTEND=castxml`) | 77.2 s / 77.1 s | 100% |
| castxml alone on the aggregate (C++ mode, 26 MB XML) | 1.07 s | 1.4% |
| `clang -ast-dump=json -fsyntax-only` alone (C mode) | 2.0 s | 2.6% |
| `gcc -aux-info -fsyntax-only` (5.9 MB text) | 0.60 s | 0.8% |
| `gcc -fsyntax-only` (no aux-info) | 0.43 s | — |

The best case for aux-info is about 1.5 s saved out of 77 s, roughly a 2% gain.
A py-spy profile (20 Hz, with subprocesses) of the cold run puts the time in
Python, not in the frontend process. Out of about 1,700 samples:

- about 460 in `clang_header_dump`/`acquire_header_graph_ast`, which is
  mostly `project_header_graph_ast_file` (about 300). That is projecting and
  caching the AST, not parsing it.
- about 400 in `compare/surface_reconcile` and `checker.py`
- about 390 in `_dump_elf`
- about 250 in `write_snapshot_compact`, the snapshot cache write
- about 175 in `cross_source_findings`

A names-only frontend would remove none of these, because they run on the
snapshot or on the 40,004 + 2,130 findings that make up the report.

## Name-set agreement

On MKL the two frontends give the same function names. From headers under
`include/`, aux-info yields 27,111 non-static function prototypes. castxml
yields 27,112 non-static, non-inline `Function` elements: the same 27,111 plus
`__builtin_memcpy`. The `_Mkl_Api` wrapper macros expand before either tool sees
the code, so both report the expanded prototypes. The run produced 2,130
`public_not_exported` and 40,004 `exported_not_public` findings.

## Why it is also not correct for the target users

- **C only.** `-aux-info` is ignored for C++. oneDAL, oneTBB and other C++
  targets would get an empty surface. Under AGENTS.md's "weaker evidence narrows
  conclusions", an empty surface must be a `FAILED` fact, never a clean audit.
  So the frontend would need a C/C++ detector and a fallback to castxml or clang
  anyway.
- **No mangling.** Even for `extern "C"` declarations in a C++ build, matching
  against ELF names needs language linkage that aux-info does not record.
- **No variables.** Exported data such as `extern int foo;` never appears, so
  the frontend cannot decide `public_not_exported` for a variable. It would have
  to report "unknown" for every variable.
- **Inline and static functions.** aux-info lists `static`/`static inline`
  definitions, and its `NC`/`OC`/`ND` flags are the only clue. The frontend would
  have to parse them to avoid fabricating `public_not_exported` for functions
  that are never meant to be exported.
- **`#ifdef` branches.** aux-info sees one preprocessor configuration, the same
  limit castxml and clang have. It has no advantage here.
- **Attribution.** aux-info gives `file:line`, which is enough for header
  attribution. This is its only real strength, and the existing backends
  already provide it.

## When to revisit

Revisit only if a profile shows that the header *parse subprocess* itself
dominates a cold `--no-baseline` run, rather than the Python projection, compare
and serialization stages listed above. Even then, the evidence-level and C++
gaps above still apply.
