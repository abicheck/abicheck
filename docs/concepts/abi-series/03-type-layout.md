# Part 3 — Type Layout Breaks

> **Series navigation:** [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> **3. Type Layout** ·
> [4. C++ ABI](04-cpp-abi.md) ·
> [5. Linker & ELF](05-linker-elf.md) ·
> [6. Transitive Breaks](06-transitive-breaks.md) ·
> [7. Designing for Stability](07-designing-for-stability.md)

**What you'll learn on this page**

- Why a struct published in a header is a *byte-level contract*, and how the
  compiler turns `offsetof` and `sizeof` into immediate constants the caller
  never re-checks.
- The six axes along which layout breaks: **size/offset, alignment, enum
  values, union size, bitfields/flexible arrays, and pointer/array element
  types**.
- The exact rule that makes one union-field addition safe and another breaking.
- How to design types whose layout you can evolve forever.

Prerequisites: [Part 1 — Foundations](01-foundations.md). The "compiler bakes it
in" idea from there is the engine behind everything here.

---

## The core mechanism: layout is a frozen constant in the caller

Every aggregate type published in a header is a byte-level contract: its size,
its members' offsets, its alignment, and — for C++ — its vtable shape. **The
consumer does not re-read that contract at load time.** The compiler bakes it
into every caller:

- `offsetof(s, field)` becomes an *immediate displacement* in a `mov`
  instruction.
- `sizeof(T)` becomes an *allocation constant* (`malloc(12)`, a stack frame
  size, an array element stride).
- array indexing multiplies the index by a *stride chosen at compile time*.

```text
struct Point { int x; int y; };     // v1: sizeof = 8

  caller code, compiled against v1:
      mov  dword [rbp-8], 0     ; p.x  -> offset 0
      mov  dword [rbp-4], 0     ; p.y  -> offset 4   ← "4" is now a constant in the binary
      sub  rsp, 8               ; reserve sizeof(Point) = 8  ← "8" is now a constant too
```

When the library's next release shifts even one offset, every call site compiled
against the old layout reads or writes at the wrong address — **silently,
without a linker error, usually without a crash** until adjacent memory is
eventually read back.

---

## 1. Struct / class size and offsets

The most common layout break is appending, inserting, or reordering a field.

```c
/* v1 */ struct Point { int x; int y; };          // sizeof = 8
/* v2 */ struct Point { int x; int y; int z; };   // sizeof = 12
```

Every caller that allocates `Point` on the stack or inside another struct now
*under-allocates*; every caller passing `Point` by value sends 8 bytes while the
library reads 12. The C++ analog ([case14](../../examples/case14_cpp_class_size.md))
grows a `char data[64]` buffer to `char data[128]`: `new Buffer()` hands the
constructor a 64-byte allocation that it zero-fills with 128 bytes, smashing
whatever lives next on the heap.

Layout breaks are also **transitive through embedding and inheritance**. Adding
`int extra_field` to `class Base` ([case43](../../examples/case43_base_class_member_added.md))
shifts `Derived::value` from offset 12 to 16 — *every* subclass member in the
ecosystem silently moves.

!!! note "How abicheck sees it"
    `type_size_changed` / data-member offset deltas → 🔴 **BREAKING**, from
    DWARF or headers. [case07](../../examples/case07_struct_layout.md),
    [case14](../../examples/case14_cpp_class_size.md),
    [case40](../../examples/case40_field_layout.md) (five field-level
    mutations in one struct to show how "just one field" cascades).

---

## 2. Alignment and packing

Alignment is the second axis — fields and sizes can be identical while the
*alignment requirement* changes.

```c
/* v1 */ struct __attribute__((aligned(8)))  CacheBlock { /* ... */ };
/* v2 */ struct __attribute__((aligned(64))) CacheBlock { /* ... */ };
```

v1 callers allocate `CacheBlock` on 8-byte boundaries. v2 code may emit
aligned-load instructions (`vmovdqa`) and **fault on misaligned access** —
`SIGSEGV` on x86-64 Linux, `SIGBUS` on strict-alignment platforms — and
`malloc` (typically 16-byte aligned) can no longer hand out correctly-aligned
storage without `aligned_alloc`.

Packing is the inverse:
[case56](../../examples/case56_struct_packing_changed.md) adds `#pragma pack(1)`,
eliminating all padding. `sizeof` *shrinks*, every field after the first moves,
and on ARM/SPARC the now-unaligned `int` access traps. Because `alignas` and
`#pragma pack` propagate across translation-unit boundaries through the header, a
**single-line change rewrites offsets for every TU that includes it.**

!!! note "How abicheck sees it"
    `type_alignment_changed` / `struct_packing_changed` → 🔴 **BREAKING**.
    [case42](../../examples/case42_type_alignment_changed.md),
    [case56](../../examples/case56_struct_packing_changed.md).

---

## 3. Enum value stability

Enumerations *look* like constants, but they are part of the **wire format** —
the integer values get persisted to disk, sent over the network, and switched
on.

```c
/* v1 */ enum Color { RED=0, GREEN=1, BLUE=2 };
/* v2 */ enum Color { RED=0, YELLOW=1, GREEN=2, BLUE=3 };   // inserted YELLOW
```

Inserting `YELLOW` in the middle shifts `GREEN` and `BLUE` by one. Every binary
that tested `== 1` for green now hits the yellow branch.

Three breaking variants and one safe one:

| Change | Effect | Verdict |
|--------|--------|---------|
| Insert member in the middle ([case08](../../examples/case08_enum_value_change.md)) | shifts later values | 🔴 BREAKING |
| Reassign a value, same name, e.g. `ERROR=1`→`ERROR=99` ([case20](../../examples/case20_enum_member_value_changed.md)) | protocol rewrite with no negotiation | 🔴 BREAKING |
| Remove a member ([case19](../../examples/case19_enum_member_removed.md)) | persisted/transmitted value becomes undefined | 🔴 BREAKING |
| **Append at the end** ([case25](../../examples/case25_enum_member_added.md)) | existing values unchanged | 🟢 **COMPATIBLE** |

A subtler trap: [case57](../../examples/case57_enum_underlying_size_changed.md)
adds a sentinel `= 0x100000000LL` that forces the compiler to **widen the
underlying type** from `int` to `long`. `sizeof(Color)` jumps from 4 to 8, and
every struct embedding `Color` silently grows and relocates its later fields.

---

## 4. Union layout

Unions share offset 0 across all members, so adding a variant doesn't move
existing fields — **but the union's size equals its largest member, and that
size propagates.**

```c
/* breaking */  union Value { int i; float f; };            // sizeof 4
                union Value { int i; float f; double d; };   // sizeof 8  ← grows
```

The rule is precise:

> A new union member is **safe if and only if**
> `sizeof(new) <= sizeof(old_union)` **and** `alignof(new) <= alignof(old_union)`.

That's why [case26](../../examples/case26_union_field_added.md) (adds `double`,
grows 4→8) is 🔴 BREAKING, while
[case26b](../../examples/case26b_union_field_added_compatible.md) (adds `int` to a
union already 8 bytes wide) is 🟢 COMPATIBLE. Removing a variant
([case24](../../examples/case24_union_field_removed.md)) is a *semantic* break
even at unchanged size: consumers compiled to write `d.f = 3.14f` have no
replacement access path.

---

## 5. Bitfields and flexible arrays

**Bitfields** are the most fragile layout primitive, because storage-unit
allocation is implementation-defined.
[case63](../../examples/case63_bitfield_changed.md) widens `mode` from 3 bits to
5 inside a 32-bit register map. `sizeof` is *unchanged* — naive size checks pass
— but `channel`, `priority`, and `reserved` all shift two bit positions, so
every consumer reads corrupt values with no crash and no diagnostic. This bites
hardest in hardware-register maps and protocol headers, exactly where bitfields
are most used.

**Flexible array members** have the opposite static-size profile, same failure:
[case70](../../examples/case70_flexible_array_member_changed.md) changes
`float data[]` to `double data[]`. The fixed header `sizeof(Packet)` compares
*equal*, but every caller that allocated
`sizeof(Packet) + count*sizeof(float)` now holds half the needed memory, and
`p->data[i]` indexes with stride 8 instead of 4.

---

## 6. Pointer chains and arrays

Multi-level type changes propagate through indirection.
[case45](../../examples/case45_multi_dim_array_change.md) changes
`float data[4][4]` to `double data[4][4]` inside a struct: the element type
changes, the struct doubles from 72 to 136 bytes, the stride doubles, and the
accessor functions change return/parameter widths.
[case46](../../examples/case46_pointer_chain_type_change.md) reaches further — a
function returning `int **` becomes `long **`, a two-level chain where only the
ultimate pointee changes; callers that write an `int` through the chain write 4
bytes into what v2 treats as an 8-byte cell.

!!! note "How abicheck sees it"
    abicheck walks pointer and array types *structurally* during
    `func_return_changed` and `func_params_changed` detection — a surface-level
    "both sides return a pointer" comparison would miss these.

---

## How to defend type layout

!!! tip "Design patterns for Part 3"
    - **Opaque handles.** Expose only `struct foo *`; define the struct in a
      `.c` file. Callers can't take `sizeof` or `offsetof`, so layout is free
      to change. OpenSSL 1.1.0's move to `EVP_MD_CTX_new`/`_free` opaque
      handles is the canonical precedent.
    - **Pimpl (C++).** The public class holds one `d_ptr` to a private `Impl`;
      all state lives in `Impl`, so `sizeof` of the public class never
      changes. Qt enforces this across every public class.
    - **Reserved padding.** End every public struct with `void *reserved[N]` /
      `uint64_t _pad[N]`; future releases repurpose slots without changing
      `sizeof` or shifting offsets (POSIX `pthread_attr_t`, kernel UAPI).
    - **Freeze the enum underlying type.** Write `enum class Color : int32_t`
      in C++; in C keep values in `int` range or add an explicit sentinel.
      Never let a new enumerator silently widen the type.
    - **Append-only evolution.** Reordering, inserting, or removing a field is
      always breaking. Append only at the end, and only when no embedded
      `sizeof(T)` assumption exists (union analog: case26 vs case26b).

    These patterns are developed in full, with code, in
    [Part 7 — Designing for Stability](07-designing-for-stability.md).

---

## Next

C++ adds a whole second layer of binary contract on top of plain layout:
vtables, mangled qualifiers, templates, and the trivially-copyable trait that
silently flips the calling convention.

➡️ **[Part 4 — C++ ABI Specifics](04-cpp-abi.md)**

*See also:* [ABI Cheat Sheet](../abi-cheat-sheet.md) ·
[BREAKING examples](../../examples/by-verdict/breaking.md) ·
[COMPATIBLE examples](../../examples/by-verdict/compatible.md)
