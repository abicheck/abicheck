---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# The header-graph parse picks C mode for C++ headers without a recognised C++ construct

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-12): CLOSED.** The header graph's parse now follows the language mode the primary pass resolved. `service_header_graph_attach._attach_header_graph` reads it from the snapshot (`ast_toolchain["resolved_lang_mode"]`) and, with no explicit `--lang`, passes it to `acquire_header_graph_ast` -> `clang_header_dump(resolved_lang_mode=...)`, which uses `"c++"` instead of re-deriving C from header text (it never demotes, and an explicit `lang` still wins). A prefetched parse that ran -- or failed -- in C mode under a C++ decision is re-parsed once; `HeaderGraphAst.resolved_force_cpp` records the mode each parse ran or attempted, so a header set that fails as C++ too is not parsed twice. Because the decided mode, not the evidence behind it, is what the AST cache key hashes, the graph's parse reaches the primary pass's key: under `--ast-frontend clang` it reuses that pass's in-process AST instead of running clang again.

Found while building the fixtures for the [header semantic-facts fix](l2-header-projection-drops-template-override-callback-facts.md). The primary pass (castxml or clang) decides C or C++ from header text *and* the binary's mangled exports (`extract/headers/toolchain._resolve_force_cpp`, correlated by `ast_config._exported_symbols_indicate_cpp`). The header graph's parse, often prefetched before that decision exists, re-decided from header text alone (`ast_config._detect_cpp_headers`); neither a non-const reference (`void fire(Slot& s);`), a `std::` name, an overload nor a default argument matches `_CPP_ONLY_PATTERNS`, so such a `.h` header was parsed as `-x c -std=gnu11` and failed.

`hygiene` (was) — The failure was honest (the graph fell back to declaration-only and `header_call_graph` was stamped degraded), but the graph lost every type, call and semantic edge for a header the primary pass parsed as C++.

## Reproduction (now a regression test)

`tests/test_header_graph_language_mode.py` dumps four such C++ headers (non-const reference, `std::function`, overload, default argument) under both frontends, plus two C controls that must stay C. Before the fix, `abicheck dump libapi.so -H api.h` logs `header graph: clang AST parse failed` and the stored graph has `degraded_passes: {"header_call_graph": true}`.

## Direction taken

Agree with the primary pass (the first direction below was recorded as an option); the text heuristic was left as it is, since the export evidence already decides these headers.

### Directions recorded

- Agree with the primary pass: if it parsed the headers as C++ (castxml always does), or the binary's exports are Itanium-mangled, parse the graph's headers as C++ too.
- Or widen the text heuristic (`&` in a declarator, `std::`), with a C-mode retry kept for genuine C headers.
