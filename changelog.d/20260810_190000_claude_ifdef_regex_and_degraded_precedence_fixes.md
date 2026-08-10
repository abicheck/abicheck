### Fixed

- **`macro_graph.py`'s unmodeled-conditional fallback didn't recognize
  `#ifdef`/`#ifndef`**: a malformed-but-compiler-accepted directive with
  trailing tokens (`#ifdef FEATURE_X extra`) or a line continuation failed
  every simple-guard pattern *and* the `#if`-only fallback (`\b` fails right
  after "if", since "d"/"n" are word characters too) — so no frame was
  pushed at all, and the matching `#endif` popped the *enclosing* guard's
  frame instead, truncating it early. The fallback now also matches
  `#ifdef`/`#ifndef`.
- **`fold_virtual_dispatch_graph` let a narrowed prerequisite mask a
  degraded one**: its three prerequisites stamp independently, so one can
  narrow cleanly while another degrades (a real per-TU clang failure);
  checking `narrowed` before `degraded` stamped only the narrowed key,
  silently dropping the untracked gap from the failed TUs. `degraded` (and
  a prerequisite that never ran at all) now takes precedence, matching
  `SourceGraphSummary.degraded_passes`'s own documented precedence.
- **`test_inline_graph_folds_macro_edges_when_clang_available` could shell
  out to a real `clang++`**: `with_call_graph=True` also constructs real
  `ClangOverrideGraphExtractor`/`ClangTemplateGraphExtractor`/
  `ClangCallbackGraphExtractor` instances, previously unfaked in this
  non-`integration`-marked test. Now faked like the others.

### Docs

- Corrected `docs/reference/source-graph-schema.md`'s callback-family
  section, which still said every `DECL_REGISTERS_CALLBACK`/
  `DECL_TAKES_ADDRESS_OF` edge joins onto a pre-existing `source_decl` node
  — it now mints a missing endpoint instead.
