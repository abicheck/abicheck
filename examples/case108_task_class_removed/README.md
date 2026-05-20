# Case 108: `task` Class Removed (oneTBB historical break — vtable angle)

**Category:** Polymorphic Class Removal | **Verdict:** 🔴 BREAKING

## What breaks

An entire publicly-derivable polymorphic base class is removed. Every user
subclass that overrode the virtual `execute()` becomes a vtable error at
link/load time: the typeinfo symbol for the v1 base is gone, RTTI fails,
and `delete` on a polymorphic pointer to the removed base invokes UB.

This is more severe than case107 because:
- The base class was a *derivation point* — user code embedded the v1 vtable
  layout into every derived class's vtable.
- RTTI strings (`typeinfo for mylib::task`) crossed DSO boundaries; removing
  the base silently breaks `dynamic_cast` and exception handling for any
  user exception derived from it.

## Why this is a oneTBB-flavored break

Classic TBB's `tbb::task` low-level API was the recommended way to write
parallel algorithms before `parallel_invoke` / `task_group`. The class was
fully removed in oneTBB 2021.1 along with `task_scheduler_init` (case107).

## Code diff

| v1 | v2 |
|----|------|
| `class task { virtual task* execute() = 0; ... };` | *(removed)* |
| `task* mylib_spawn_dummy();` | *(removed)* |
| — | `class task_group { using task_fn = void (*)(); void run(task_fn); ... };` |

## How abicheck catches it

The existing `FUNC_REMOVED` / `TYPE_REMOVED` detectors fire on the base
class symbols, vtable symbol (`_ZTV7mylib4task`), and typeinfo
(`_ZTI7mylib4task`). This case exists to pin the full surface as a named
regression fixture.

## How to fix

A polymorphic base class is the most expensive thing to remove. The safe
migration is:
1. Keep the old base class header in a `legacy/` subdir for one release.
2. Provide an adapter that wraps the new API in the old interface.
3. Bump SONAME on removal.
4. Document the equivalence map (which `task` workflow maps to which
   `task_group` call).

## References

- [oneTBB 2021.1 release notes](https://github.com/uxlfoundation/oneTBB/releases)
- [Itanium C++ ABI § 2.9 — vtable and RTTI symbol layout](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#vtable)
