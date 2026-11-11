---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Clang leaves enum-typed parameter slots without a `type_abi_fact` that castxml records

Index: [Known gaps](../known-gaps.md)

**Status (2026-11-11): PARTLY CLOSED.** A return type spelled through a typedef now carries its fact (extractor generation 54): Clang gives a function's own type no desugared spelling, so the return slot is looked up among the TU's typedef facts, innermost scope first, stopping at the nearest declared typedef (`extract/headers/clang/return_type.typedef_return_fact`). The enum-typed parameter slots below stay open; the file name keeps its original wording so existing links resolve.

For a header declaring

```cpp
namespace lib {
enum Color { Red = 1, Green = 5, Blue = 9 };
enum class Mode : std::uint16_t { Off = 0, On = 3 };
typedef std::int64_t handle_t;
using count_t = unsigned int;
handle_t make(Point p, Color c, Mode m, count_t n);
}
```

castxml records `type_abi_fact` PRESENT for the `Color` and `Mode` parameters (unsigned, 4/4 and 2/2) and for the `handle_t` return (signed, 8/8). The clang frontend records `NOT_COLLECTED` for the two enum parameters. It records `count_t` (a typedef of a builtin), every plain builtin slot and, since generation 54, the `handle_t` return with castxml's value.

The gap is marked, not empty: an unknown side never compares equal, so the ABI-equivalence downgrade is simply not taken for these slots. For the return slot that did manufacture a break: removing `typedef int count_t;` and spelling `int counted(int)` read `func_return_changed` (BREAKING) under Clang instead of an ABI-equivalent change (the edit corpus's `typedef_removed`, fixed above, `tests/test_header_backend_parity_regressions.py`). For an enum parameter the retype is reported, not downgraded. It reproduces with every optional tool present, so it is a frontend parity gap, not an optional-tool dependency. Found by `tests/test_default_install_fact_invariants.py` while comparing the two frontends' snapshots.

`builtin_type_fact` is called on the slot's desugared spelling and has no input for an enum's underlying type, which is the likely place to start. Owner: `extract/headers/clang/functions.py`.
