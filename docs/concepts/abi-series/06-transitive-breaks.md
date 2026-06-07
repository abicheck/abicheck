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

Swapping the *kind* of a named type is dangerous — but not uniformly, and the
distinction matters for what you do about it. The dividing line is **whether the
storage model changes**:

- Anything **involving a `union`** — `struct`→`union` or the reverse — changes
  how members are laid out (sequential vs overlapping at offset 0), so it is a
  genuine **binary** break even when the size happens to match.
- A bare **`struct`↔`class` keyword swap** with the same members is
  **binary-identical**: under the Itanium ABI the two keywords differ only in
  *default member access* and *default inheritance* — both source-level concepts.
  Nothing about layout or mangling changes, so existing binaries keep working;
  only fresh compiles can be affected (e.g. code that relied on the old default
  access).

[case55](../../examples/case55_type_kind_changed.md) is the breaking kind:
`Data` goes from a struct with sequential fields `x, y` (size 8) to a union where
`x` and `y` overlap at offset 0 (size 4): `sizeof` shrinks, `y`'s offset moves,
and writing one member now clobbers the other. (Separately, swapping `enum E :
int` for plain `int` in a C++ API is breaking for a different reason — `E` and
`int` mangle differently, so function symbols that took `E` vanish from the new
`.so`.)

!!! note "How abicheck sees it"
    abicheck reads DWARF's `DW_TAG_structure_type` / `DW_TAG_union_type` /
    `DW_TAG_class_type` and splits the verdict by storage model:

    - **union involved** → `type_kind_changed` → 🔴 **BREAKING** (layout changes).
    - **`struct`↔`class`, no union** → `source_level_kind_changed` → 🟠
      **API_BREAK** — binary-identical, source-level only. Don't bump the SONAME
      for this one; it needs at most a recompile.

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

## Source-only API breaks (binary-identical)

The breaks above are *transitive* — invisible because they hide behind a
pointer or a dependency. A related family is invisible for the opposite reason:
the change is **purely at the source level**, so the two `.so` files are
genuinely byte-identical and *every existing binary keeps running*, but code
that **recompiles** against the new headers fails to build or silently changes
meaning. abicheck reports most of these as 🟠 **API_BREAK**; a few are
conservatively **policy-escalated to 🔴 BREAKING** (notably a field rename — see
[case35](../../examples/case35_field_rename.md)) even though the binary layout is
unchanged. Either way the binary ABI is intact, so none of them *require* a
SONAME bump — but they should fail CI when you promise source compatibility.

!!! danger "Not this family: removing an *exported* overload is a real ABI break"
    Removing one overload from a set is only source-only when **no exported
    symbol disappears** — e.g. the overload was `inline`/template-only, or you
    *added* an overload that introduces ambiguity. If the removed overload had
    its own mangled symbol (the usual case for a non-inline member or free
    function), that symbol vanishes from `.dynsym` and old callers fail to
    resolve it at **load time**: that is `func_removed` → 🔴 **BREAKING** from
    [Part 2](02-symbol-contracts.md), and it *does* warrant a SONAME bump.
    [case82](../../examples/case82_sycl_overload_set_removed.md) is exactly this
    binary break, not a source-only one.

| Source-only change | Why existing binaries survive | Why a recompile breaks | Case |
|--------------------|-------------------------------|------------------------|------|
| **Default argument removed/changed** | The default was baked into the *caller's* old object code, not the library. | A new call site that omitted the argument no longer compiles (or computes a different value). | [case123](../../examples/case123_default_argument_removed.md), [case32](../../examples/case32_param_defaults.md) |
| **Access narrowed** (`public`→`protected`/`private`) | Access control is a compile-time concept; the symbol and layout are unchanged. | A consumer that called the now-private method no longer compiles. | [case34](../../examples/case34_access_level.md) |
| **Constructor became `explicit`** | No symbol or layout change. | Implicit conversions/brace-init at call sites stop compiling. | [case106](../../examples/case106_ctor_became_explicit.md) |
| **Hidden friend removed** | Hidden friends aren't ordinary exported symbols. | ADL no longer finds the operator; the expression stops compiling. | [case96](../../examples/case96_hidden_friend_removed.md) |
| **Enum / member rename** | Same value, same offset, same size — the binary layout is unchanged. | Source that named the old identifier no longer compiles. | [case31](../../examples/case31_enum_rename.md) |
| **Struct field rename** (policy-escalated to 🔴 BREAKING by default) | Same offset and size — the binary layout is unchanged. | Source that named the old field no longer compiles. | [case35](../../examples/case35_field_rename.md) |
| **Header `const`/`constexpr` constant changed** | The constant had internal linkage — it was inlined into old callers, no symbol. | A recompile picks up the new value, changing behavior. | [case124](../../examples/case124_header_constant_value_changed.md) |
| **Class became `final`** | No layout/vtable change. | A consumer that derived from it no longer compiles. | [case125](../../examples/case125_class_became_final.md) |
| **Source-standard floor raised** (e.g. C++17→20) | Already-compiled binaries are unaffected. | A consumer stuck on the old standard can no longer build against the headers. | [case98](../../examples/case98_cxx_standard_floor_raised.md) |

!!! note "Detectability: these need *headers*, not just the binary"
    Most of this family leaves **no trace in the compiled object** — default
    arguments, access levels, `explicit`, and `const`/`constexpr` values have no
    symbol at all. Comparing stripped or symbol-only `.so` files reports
    `NO_CHANGE` for every row above. Supply the **public headers** (castxml /
    `header_aware` tier) to detect them; `#define` macros and uninstantiated
    template signatures are the residual blind spot even then (see the per-change
    matrix in [Limitations](../limitations.md#source-only-changes-invisible-to-binaryobject-analysis)).

    **CI guidance:** decide up front whether you promise *source* compatibility.
    If you do, gate on 🟠 API_BREAK (and the policy-escalated 🔴 field-rename case) and
    treat it as a **semver-major** signal — you can keep the SONAME unless a
    *true binary* 🔴 BREAKING (a removed/changed symbol or a layout change) also
    fired, since these source-only changes leave the binary ABI intact.

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
