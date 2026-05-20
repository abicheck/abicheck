# Case 94: Empty Tag Gained State

**Category:** Type Layout | **Verdict:** 🔴 BREAKING

## What breaks

A type that was *empty* in v1 (`sizeof == 1` per C++ rules) is no longer empty in v2.
Any consumer compiled against v1 that passes the tag by value — into a header-inline
template, an algorithm overload selector, or any function taking it by value — wrote a
1-byte argument into what is now an 8-byte parameter slot. The callee reads partially
uninitialized memory, then steps on subsequent stack/register state.

## Why this is a oneTBB-flavored break

The pattern is exactly the `tbb::auto_partitioner` / `tbb::simple_partitioner` /
`tbb::affinity_partitioner` shape: empty tag types are passed by value into header-only
algorithm wrappers (`tbb::parallel_for`, `tbb::parallel_reduce`). The library author
sees the tag as an implementation detail with "no public members" — but its sizeof
*is* part of the ABI because consumers serialize the value at every call site.

This is exactly the failure mode `affinity_partitioner` had to engineer around: it
*does* carry state, so it's intentionally non-copyable and only passed by reference.

## Code diff

| v1 | v2 |
|------|------|
| `struct auto_partitioner {};` | `struct auto_partitioner { void* affinity_state_; };` |
| `sizeof == 1` (empty class rule) | `sizeof == 8` (pointer-sized) |

## How abicheck catches it

The existing `TYPE_SIZE_CHANGED` detector fires on the tag struct.
`STRUCT_FIELD_ADDED` also fires (a previously-zero-field struct gained `affinity_state_`).

## How to fix

If you need to add state to a previously-empty tag, the safe migration is:
1. Mark v1's tag as deprecated.
2. Introduce a *new* tag type (e.g. `auto_partitioner_v2`).
3. Provide a v1-compatible overload that ignores the old tag and converts.
4. Bump SONAME on the next ABI release.

## Real-world example

oneTBB's `affinity_partitioner` is intentionally larger than the other partitioners
and is the only one that's stateful — the library evolved this distinction
specifically to avoid the silent-corruption pattern this case demonstrates.

## References

- [oneTBB VERSIONING.md](https://github.com/uxlfoundation/oneTBB/blob/master/VERSIONING.md)
- [C++ empty class rule (sizeof must be >= 1)](https://en.cppreference.com/w/cpp/language/object#Object_representation_and_value_representation)
