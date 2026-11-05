---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Clang leaves enum- and typedef-typed slots without a `type_abi_fact` that castxml records

Index: [Known gaps](../known-gaps.md)

**Status (2026-11-05): OPEN; cause not traced.**

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

castxml records `type_abi_fact` PRESENT for the `Color` and `Mode` parameters (unsigned, 4/4 and 2/2) and for the `handle_t` return (signed, 8/8). The clang frontend records `NOT_COLLECTED` for those three slots. It does record `count_t` (a typedef of a builtin) and every plain builtin slot, and its `typedef_abi_facts` map has `lib::handle_t` PRESENT, so the information exists in the snapshot and is not carried to the slot.

The gap is marked, not empty: an unknown side never compares equal, so the ABI-equivalence downgrade is simply not taken for these slots, and no break is manufactured. It reproduces with every optional tool present, so it is a frontend parity gap, not an optional-tool dependency. Found by `tests/test_default_install_fact_invariants.py` while comparing the two frontends' snapshots.

`builtin_type_fact` is called on the slot's desugared spelling and has no input for an enum's underlying type, which is the likely place to start. Owner: `extract/headers/clang/functions.py`.
