# Part 6 — Subtle & Transitive Breaks

> **Series navigation:** [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> [3. Type Layout](03-type-layout.md) ·
> [4. C++ ABI](04-cpp-abi.md) ·
> [5. Linker & ELF](05-linker-elf.md) ·
> **6. Transitive Breaks** ·
> [7. Designing for Stability](07-designing-for-stability.md)

**What you'll learn on this page**

- The breaks that **survive code review**: the exported symbol table is
  byte-identical, every signature is preserved, `nm --dynamic` shows no diff —
  yet consumers corrupt memory on the first call.
- Five mechanisms that smuggle a layout change across the boundary: **dependency
  leaks, anonymous structs, type-kind swaps, reserved-field misuse, and embedded
  leaf structs**.
- Why these are precisely the cases that need DWARF-aware tooling — and why the
  *real* fix is structural, not a better scanner.

Prerequisites: [Part 3 — Type Layout](03-type-layout.md) (offsets propagate
through embedding) and [Part 2 — Symbol Contracts](02-symbol-contracts.md).

---

## The common thread: a transitive contract

The breaks on this page share one property: a **transitive dependency**. The ABI
contract is implied not by anything the library itself defines, but by a type it
*publishes through a header, a padding field, or a nested member*. Static
analyzers that look only at the shipped `.so` miss them entirely; DWARF-aware
tools catch most but require debug info to travel with the binary.

> The recurring mental model: your public type's size and layout are the *sum* of
> things you may not control. The moment any of those things shifts — a vendored
> type, an anonymous member, a "reserved" byte — your `sizeof` changes, and from
> [Part 3](03-type-layout.md) you know what a changed `sizeof` does.

---

## 1. Dependency leaks

When a public header includes a third-party type — `std::string`, `boost::any`,
`tbb::task_arena`, `grpc::Status` — your library silently inherits that type's
ABI contract. Upgrade the third-party library and every consumer's compiled
size, field offsets, and vtable assumptions become wrong, **even though your own
source never changed.**

[case18](../../examples/case18_dependency_leak.md) demonstrates this with a
`ThirdPartyHandle` that grows from 4 to 8 bytes: `libfoo`'s exported symbols are
identical, `nm` and a naive `abidiff` see no difference, but a caller built
against v1 headers allocates a 4-byte struct that the v2 library reads 8 bytes
from. libstdc++'s dual-ABI split (`std::string` after GCC 5) and TBB 2021.3's
`task_arena` re-layout both propagated through exactly this mechanism, fracturing
every consumer who had leaked the type into a public API.

!!! note "How abicheck sees it"
    `type_size_changed` → 🔴 **BREAKING**, *only when DWARF for the third-party
    type is present* in the shipped `.so`. Stripped distributions hide the
    hazard entirely — which is why the fix is structural (pimpl / opaque
    handles), not tooling.

---

## 2. Anonymous structs

C and C++ permit unnamed nested structs and unions inside a public type. The
container's size and alignment depend entirely on the unnamed member's contents,
but the anonymous member has **no stable name** to refer to in a diff — and in
C++ it changes the mangled layout without touching any source-visible
identifier.

```c
/* v1 */ struct Variant { int tag; union { int i; float  f; }; };  // sizeof 8
/* v2 */ struct Variant { int tag; union { int i; double d; }; };  // sizeof 16
```

Replacing `float f` with `double d` inflates the anonymous union from 4 to 8
bytes and — with 8-byte alignment — shifts the whole struct from 8 to 16,
moving `i` from offset 4 to offset 8
([case36](../../examples/case36_anon_struct.md)). A caller allocating
`sizeof(Variant) == 8` on the stack then calls a v2 library that reads `i` at
offset 8 — four bytes past the allocation, into uninitialized memory. **The
source diff is one line inside an anonymous scope; the ABI diff is total.**
abicheck traces the layout through DWARF's anonymous-member rules and reports
`type_size_changed` with the exact member-offset delta.

---

## 3. Type-kind changes

Swapping `struct` for `union`, `enum` for plain `int`, or `class` for `struct`
at the same name — **even when the size happens to match** — is always an ABI
break, because the *semantics* of member storage differ.

[case55](../../examples/case55_type_kind_changed.md) changes `Data` from a struct
with sequential fields `x, y` (size 8) to a union where `x` and `y` overlap at
offset 0 (size 4): `sizeof` shrinks, `y`'s offset moves, and writing one member
now clobbers the other. Even a *same-size* swap — `enum E : int` → plain `int`
in a C++ API — breaks overload resolution and name mangling (`E` and `int`
mangle differently), so function symbols vanish from the new `.so`.

!!! note "How abicheck sees it"
    abicheck reads DWARF's `DW_TAG_structure_type` vs `DW_TAG_union_type` vs
    `DW_TAG_enumeration_type` and classifies any transition as
    `type_kind_changed` → 🔴 **BREAKING**, regardless of byte-for-byte size
    equality — because a consumer's *code generation* assumes the kind, not
    just the footprint.

---

## 4. Reserved-field misuse

Reserving padding for future growth — `int __reserved1`, `char _pad[16]` — is
the standard way to extend a struct without bumping SONAME, **but it only works
if no shipped binary ever touched the reserved bytes.** The moment a consumer
writes to reserved storage (deliberately via a cast, or accidentally via
`memset(&s, 0xFF, sizeof(s))` before field-wise init), repurposing those bytes
becomes silent data corruption.

[case54](../../examples/case54_used_reserved_field.md) shows the **correct**
pattern: v1 ships `__reserved1` and `__reserved2` at defined offsets; v2 renames
them to `priority` and `max_retries` with the *same types and offsets*.

!!! note "How abicheck sees it"
    `_diff_reserved_fields` recognizes the naming convention (`__reserved`,
    `_reserved`, `__pad`, `_unused`) and classifies the rename → 🟢
    **COMPATIBLE**. The catch the tool *cannot* verify: there is no way to
    prove no consumer ever wrote to the slot — the pattern's safety rests on a
    documented contract that users zero-initialize and ignore the field. Linux
    `struct stat`, glibc `pthread_attr_t`, and Wayland protocol structs all
    depend on exactly this contract.

---

## 5. Leaf structs through pointers

Pointer indirection is the single strongest ABI firewall in C: a caller that
handles only `T*` is agnostic to `sizeof(T)`, to `T`'s field offsets, and to
`T`'s kind-tag.

[case48](../../examples/case48_leaf_struct_through_pointer.md) contrasts this with
the failing case — a `Container` that **embeds** `Leaf` by value:

```c
/* breaking: embed by value */
struct Container { struct Leaf leaf; int flags; };   // flags offset moves with Leaf

/* safe: indirect through a pointer */
struct Leaf;                                          // incomplete type
struct Container { struct Leaf *leaf; int flags; };   // flags offset fixed
```

When `Leaf` grows from 4 to 8 bytes, the *embedding* version shifts
`Container::flags` from offset 8 to 16 — the public API still takes only
`Container*`, but the size change propagates through embedding and a v1-compiled
caller reads `flags` at the wrong offset. The *pointer* version is immune: the
caller's compilation depends only on pointer size (stable per ABI), and the
library alone controls allocation and layout. This is the mechanism behind every
opaque-handle C API (`FILE*`, `sqlite3*`, `git_repository*`).

---

## How to defend against transitive breaks

!!! tip "Design patterns for Part 6"
    - **Opaque wrappers around third-party types.** Never forward-publish
      `std::string`, `boost::any`, `tbb::task_arena`, or any
      vendored-dependency type in your headers; wrap it in a type you own.
    - **Stable DTOs at the API boundary.** Define plain structs with explicit
      layout for every value that crosses the ABI, and treat those DTOs as a
      versioned schema separate from your internal types.
    - **Build-time abicheck in CI.** Run `abicheck compare` against the last
      released `.so` on every PR; flag anything above `COMPATIBLE_WITH_RISK` as
      a release blocker — and ship the build with debug info so transitive
      layout shifts are *visible* to the tool.
    - **Zero reserved fields before public release**, or commit in
      documentation to never activating them. Reserved padding that is never
      used is free; reserved padding whose safety you can't audit is a future
      🔴 BREAKING verdict.
    - **Pointer-to-incomplete-type for anything you might evolve.** If you
      can't guarantee a struct's layout for the lifetime of a SONAME, expose a
      forward-declared tag and a constructor/destructor pair — not the
      definition.

---

## Next

You've now seen every family of break. The final page turns the scattered "how
to fix" boxes into a single, coherent design playbook — the patterns that make a
library *evolvable*, plus the CI gate that enforces them.

➡️ **[Part 7 — Designing for Stability](07-designing-for-stability.md)**

*See also:* [ABI Cheat Sheet](../abi-cheat-sheet.md) ·
[BREAKING examples](../../examples/by-verdict/breaking.md) ·
[Limitations](../limitations.md)
