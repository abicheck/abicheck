---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The header-graph parse picks C mode for C++ headers without a recognised C++ construct

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-10-12 at `f25eddcdd`): OPEN.** Found while building the fixtures for the [header semantic-facts fix](l2-header-projection-drops-template-override-callback-facts.md). With no explicit `--lang`, the header graph's own clang parse (`service_header_graph_attach.acquire_header_graph_ast` -> `clang_header_dump`) chooses C or C++ from header text (`extract/headers/ast_config._detect_cpp_headers`). Neither a non-const reference (`void fire(Slot& s);`) nor a `std::` name (`#include <functional>` plus `std::function<void(int)>`) matches `_CPP_ONLY_PATTERNS`, so such a `.h` header is parsed as `-x c -std=gnu11` and fails.

`hygiene` — The failure is honest: the header graph falls back to declaration-only and `header_call_graph` is stamped degraded, so nothing reads as clean. But the graph loses every type, call and semantic edge for a header the primary (castxml) dump parsed as C++ without trouble, and whose library exports C++-mangled names.

## Reproduction

`tests/test_header_graph_semantic_facts.py` wraps its callback and `std::function` fixtures in a namespace for this reason. Without the namespace, `abicheck dump libapi.so -H api.h` logs `header graph: clang AST parse failed` and the stored graph has `degraded_passes: {"header_call_graph": true}`.

## Directions

- Agree with the primary pass: if it parsed the headers as C++ (castxml always does), or the binary's exports are Itanium-mangled, parse the graph's headers as C++ too.
- Or widen the text heuristic (`&` in a declarator, `std::`), with a C-mode retry kept for genuine C headers.
