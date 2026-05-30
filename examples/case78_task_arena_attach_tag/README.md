# Case 78: task_arena::attach Tag Type Replaces Enum

**Category:** ABI + source break / oneTBB regression suite | **Verdict:** 🔴 BREAKING

## What breaks

An `attach_mode_t` enum and the constructor `task_arena(attach_mode_t)`
are replaced by an empty tag struct `task_arena::attach` and a
`task_arena(attach)` constructor. The old enum is removed entirely, the
old constructor's mangled name is gone, and consumer source that wrote
`task_arena ta(attach_to_current);` no longer compiles.

## Why this is in the oneTBB regression suite

Mirrors a documented oneTBB API move from enum-value-based mode
selection to tag-type-based selection. The motivation upstream was to
align with modern C++ idioms (tag dispatch, `std::piecewise_construct`,
`std::nothrow`), but the transition broke both source and ABI for any
consumer that referenced the old API.

## How abicheck catches it

The diff exposes:

- `TYPE_REMOVED`: `attach_mode_t` (the enum)
- `ENUM_MEMBER_REMOVED`: `no_attach`, `attach_to_current`
- `FUNC_REMOVED`: old mangled `task_arena(attach_mode_t)`
- `TYPE_ADDED`: `task_arena::attach`
- `FUNC_ADDED`: new mangled `task_arena(task_arena::attach)`

Any of `FUNC_REMOVED` / `TYPE_REMOVED` / `ENUM_MEMBER_REMOVED` on a
public symbol is BREAKING under default policy, so no new detector is
needed.

## Code diff

| v1 | v2 |
|----|------|
| `enum attach_mode_t { no_attach=0, attach_to_current=1 };` | (removed) |
| `task_arena(attach_mode_t mode);` | `task_arena(attach tag);` |
| (no nested types) | `struct attach {};` (nested) |

## How to fix (as a library maintainer)

- Ship a deprecation cycle: in release N–1, add the tag type and the
  tag-form constructor while keeping the enum + old constructor;
  mark the enum and old constructor `[[deprecated]]`. In release N,
  remove the deprecated path.
- Resist the urge to remove the old enum aggressively — tag-dispatch
  cleanups feel "purely additive" until you measure the downstream
  consumer fleet.

## Real failure demo

```bash
# v1 header, v1 .so:
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app   # compiles, links

# v2 header, v2 .so:
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app
# → error: 'attach_to_current' was not declared in this scope
#   error: no matching function for call to 'mylib::task_arena::task_arena(<unknown>)'
```

## References

- oneTBB 2021 release notes — `task_arena::attach` tag introduction.
