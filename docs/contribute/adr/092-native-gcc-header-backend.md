# ADR-092: Explicit experimental native GCC header backend

**Date:** 2026-11-10
**Status:** Proposed — experimental implementation for review.

## Context

CastXML reads headers through Clang even when it emulates a GCC compilation
context. Some GCC consumers need their real compiler's layout and language
semantics. Native GCC plugin experiments on GCC 13/14 established that this
can feed the existing model and checker without rewriting either.

## Decisions

1. Add `gcc` to the existing `compile.frontend` vocabulary and typed
   `header_backend` selection. `auto` keeps its existing behavior. The
   stored producer is `gcc-experimental`, explicitly distinguishing it
   from both GCC emulation and mature frontend coverage.
2. Build the plugin explicitly with a matching GCC plugin-dev SDK using
   `python -m abicheck.extract.headers.gcc.build`. A scan never builds or
   installs compiler plugins. Discover `ABICHECK_GCC_PLUGIN` (file or
   directory), then the per-version user cache, then the compiler SDK path.
   GCC's plugin version check rejects mismatches. Do not fall back to Clang.
3. Use the existing header backend protocol and `HeaderAstFields` /
   SemanticIR projection. Standard workflows assemble binary/header-only
   snapshots; ELF/DWARF handling and verdicts stay with existing owners.
4. Retain extraction context and limitations through TU carriers, CLI depth
   provenance, snapshot storage and reports. Known incomplete extraction
   lowers assurance to `partial`, including after reload. This uses the
   existing open `dump_provenance` carrier; no new snapshot section/version.
5. Key snapshot reuse on the actual plugin content and GCC identity, in
   addition to existing compile/header inputs. A native dump does not run
   Clang's header-graph pass; native graph coverage is explicitly incomplete.
6. Initially support Linux ELF and header-only C/C++ in a single header TU,
   tested with GCC 13 and 14. Reject dump manifests, SYCL/device contexts and
   source-ABI replay rather than substituting another frontend. Computed
   layouts, declarations, templates and vtables have per-fact availability;
   deferred/unrepresentable facts remain unknown. Macro/TLS comparison,
   complex templates and native graph acquisition remain incomplete.

## Validation

`tests/test_native_gcc_workflow.py` builds the real plugin and checks C/C++
`dump -> stored snapshot -> compare`, genuine ELF layout changes, zero
self-diff, persistent partial assurance, plugin errors, forbidden Clang
fallback and plugin-content cache invalidation. Earlier experiments also
matched compiler layouts and vtable/constructor/destructor symbols to ELF.
The dedicated GCC job does not depend on CastXML to execute native tests.

## Consequences

An opt-in GCC consumer can use actual compiler evidence while remaining
aware of incomplete coverage. Packaging multiple compiler SDK versions and
broad real-library parity are separate work before any default selection.
