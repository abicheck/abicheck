# Part 4 — C++ ABI Specifics

> **Series navigation:** [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> [3. Type Layout](03-type-layout.md) ·
> **4. C++ ABI** ·
> [5. Linker & ELF](05-linker-elf.md) ·
> [6. Transitive Breaks](06-transitive-breaks.md) ·
> [7. Designing for Stability](07-designing-for-stability.md)

**What you'll learn on this page**

- Why C++ is the language where ABI stability is hardest, and what the *Itanium
  C++ ABI* freezes as part of your contract.
- The seven C++ mechanisms most likely to corrupt a consumer binary: **vtables,
  method qualifiers, templates/inline, inline namespaces, `noexcept`,
  trivial→non-trivial, and base-class layout**.
- Why some of these (like `noexcept`) are *risk*, not hard *breaks* — and the
  precise reason.
- The four C++ design patterns that give you room to evolve.

Prerequisites: [Part 2 — Symbol Contracts](02-symbol-contracts.md) (mangling,
name-only resolution) and [Part 3 — Type Layout](03-type-layout.md) (offsets,
`sizeof`).

---

## Why C++ is the hard case

Every class with a virtual method carries a hidden pointer to a
statically-ordered table of function pointers. Every method name is mangled
through a grammar that encodes qualifiers, namespaces, template arguments, and
parameter types. Every struct with a user-defined destructor changes how it is
*passed* between functions.

The **Itanium C++ ABI** — followed by GCC and Clang on Linux, macOS, the BSDs,
and most embedded targets — is rigid *by design*: it guarantees cross-compiler
interoperability at the cost of making almost any visible change to a class a
potential binary break. MSVC on Windows uses a different but equally rigid ABI
with the same categories of pitfall. The seven sections below tour the
mechanisms most likely to bite.

!!! note "\"Itanium-style\", not gospel"
    The Itanium C++ ABI specification states that it is *not* the authoritative
    definition for any particular platform — each vendor pins its own details on
    top of it (and the platform's data model). The examples below use the
    **Itanium-style** model unless noted; treat the exact mangling, slot
    ordering, and passing rules as illustrative of the *mechanism*, and consult
    your toolchain's ABI document for the byte-exact contract.

---

## 1. Vtables and virtual methods

A polymorphic class carries a hidden `vptr` as its first word, pointing to a
per-class **vtable** — a static array of function pointers indexed by the order
virtual methods are *declared*. The Itanium ABI fixes this slot ordering as a
public part of the class contract. A call compiles to a slot index baked into
the call site:

```text
widget->resize();      // compiles to:  (*widget->vptr[1])(widget)
                       //                               ^ slot 1 is a constant
```

Insert a new virtual method *before* an existing one and every later slot
silently shifts:

```cpp
/* v1 */ struct Widget { virtual void resize(); };            // resize = slot 0
/* v2 */ struct Widget { virtual void recolor();              // recolor = slot 0
                         virtual void resize(); };            // resize  = slot 1
```

Now every old call to `resize()` (still using slot 0) dispatches to `recolor()`
— wrong method, no crash.

| Variant | Effect |
|---------|--------|
| Insert virtual before existing ([case09](../../examples/case09_cpp_vtable.md)) | reroutes calls to the wrong slot |
| Make a method pure-virtual ([case23](../../examples/case23_pure_virtual_added.md)) | slot becomes `__cxa_pure_virtual` → unconditional `abort()` |
| Add the **first** virtual to a non-polymorphic class ([case68](../../examples/case68_virtual_method_added.md)) | a vptr is *prepended*; every member shifts by `sizeof(void*)`, `sizeof` grows |
| **Remove** a virtual method (`func_virtual_removed`) | every later slot shifts up by one, so every old call dispatches one slot off — the same reroute as insertion, in reverse |

The only safe addition is to **append** new virtual methods after every existing
slot — and only when no consumer-side derived classes exist that would
themselves extend the vtable.

---

## 2. Method qualifiers

Qualifiers are **load-bearing parts of the mangled name**, not cosmetic
annotations. A `const` member function mangles with a `K` marker, `volatile`
with `V`, ref-qualifiers (`&`/`&&`) with `R`/`O`. Edit one and you rename the
symbol.

```cpp
/* v1 */ int Widget::get() const;   // _ZNK6Widget3getEv   (note the K)
/* v2 */ int Widget::get();         // _ZN6Widget3getEv    (K is gone)
```

The old symbol vanishes from `.dynsym`; every consumer hits `symbol lookup
error` at load ([case22](../../examples/case22_method_const_changed.md)). This is
one of the *easier* C++ breaks to diagnose, because it's a clean `dlopen` abort
rather than silent corruption.

The dangerous sibling is converting an instance method to `static`
([case21](../../examples/case21_method_became_static.md)): the mangled name is
often *identical* (`_ZN6Widget3barEv` for both), so the linker is happy — but the
v1 caller passes an implicit `this` in `%rdi` that the static callee never reads,
and the function computes from register garbage. Treat **every qualifier edit on
a public declaration as renaming the symbol.**

---

## 3. Templates and inline

Inline and template code lives where the One Definition Rule meets the link
model — and that's where ABI assumptions get baked into the *consumer's* binary
without the library ever seeing them.

An explicitly instantiated `Buffer<int>` produces a mangled symbol like
`_ZN6BufferIiEC1Em`. Adding a `capacity_` field
([case17](../../examples/case17_template_abi.md)) keeps the symbol name
*identical* while growing `sizeof(Buffer<int>)` from 16 to 24. The consumer
stack-allocates 16; the v2 constructor writes 24 — a stack smash with no
header-level signal.

**Header-only inline definitions embed the *body* into each consumer TU**, so
the implementation callers run is frozen when they compile. Changing an inline
body between releases produces ODR violations (LTO sometimes catches these) and
silent disagreements between two consumers who pulled in different header
versions.

The transition direction matters:

| Transition | Result | Verdict |
|------------|--------|---------|
| inline-in-header → outlined-in-`.so` ([case47](../../examples/case47_inline_to_outlined.md), [case16](../../examples/case16_inline_to_non_inline.md)) | old binaries keep their inlined copy; a new export simply *appears* | 🟢 COMPATIBLE (addition) |
| outlined-in-`.so` → inline-in-header ([case59](../../examples/case59_func_became_inline.md)) | the exported symbol *vanishes* from `.dynsym` | 🔴 BREAKING |

The compatible direction has a **build-order caveat** worth knowing
([case16](../../examples/case16_inline_to_non_inline.md)): comparing old→new
`.so` it is purely an added symbol, so the verdict is 🟢 COMPATIBLE — but a
caller freshly compiled against the *new* header (which now expects an imported
symbol) and then linked against the *old* `.so` (which never exported it) fails
at link time. That is a downgrade/mismatched-build hazard, not a regression in
the new release, and it dissolves once the symbol exists. If you *also* change
the function's body in the same move, stale callers running their old inlined
copy can disagree with the new export — an ODR hazard LTO sometimes catches.

Template instantiations, inline functions, and `constexpr` bodies are part of
the ABI **even though they never appear in `readelf -Ws`.**

---

## 4. Covariant returns and inline namespaces

An **inline namespace** is transparent to source-level name lookup but is
mangled into every symbol declared inside it — making it the canonical Itanium
mechanism for *generational ABI versioning*.

```cpp
namespace crypto { inline namespace v1 { void encrypt(/*...*/); } }
// source writes crypto::encrypt(...) ; symbol = _ZN6crypto2v17encryptE...

namespace crypto { inline namespace v2 { void encrypt(/*...*/); } }
// same source compiles ; symbol = _ZN6crypto2v27encryptE...   ← different symbol
```

Source compiles unchanged against both, but pre-compiled callers can't resolve
the new symbol ([case71](../../examples/case71_inline_namespace_moved.md)). This
is exactly the device libstdc++ used for its **dual ABI**: GCC 5 introduced
`std::__cxx11::basic_string` alongside the legacy COW `std::string`, gated on
`_GLIBCXX_USE_CXX11_ABI`. Distributions spent *years* untangling the resulting
lookup failures.

**Covariant returns** interact with vtable layout directly: a `Circle::clone()`
returning `Circle*` generates a `this`-adjusting thunk; inserting a new
intermediate base class
([case72](../../examples/case72_covariant_return_changed.md)) shifts sub-object
offsets and invalidates hardcoded vtable slots.

The lesson: inline namespaces are a **power tool** — wielded deliberately they
let you ship a breaking change under a new mangled surface while keeping the old
one exported; switched accidentally they rename every symbol you export.

---

## 5. `noexcept` — why this is *risk*, not a hard break

[case15](../../examples/case15_noexcept_change.md) is classified
🟡 **COMPATIBLE_WITH_RISK**, not BREAKING, and the reasoning is worth
internalizing.

Before C++17, `noexcept` was **not part of the function type**, so the Itanium
mangler ignored it: `void reset() noexcept` and `void reset()` both mangle to
`_ZN6Buffer5resetEv` and resolve to the *same* `.dynsym` entry. Removing
`noexcept` therefore **does not break linkage** — hence not BREAKING.

What it *does* break is the caller's **unwinding assumption**. The v1 compiler
saw `noexcept`, so it omitted exception landing pads, cleanup frames, and
`.eh_frame` entries at the call site. If v2 now throws, the exception propagates
into a frame with no unwinding metadata and `std::terminate()` fires
unconditionally — every destructor skipped, every `catch` bypassed.

This is the deployment-risk shape: binary-linkable, source-recompilable, but
**semantically unsafe** for binaries built under the stricter old contract — the
kind of change that merits review rather than a silent pass.

!!! note "How abicheck sees it"
    abicheck classifies the bare change kinds `func_noexcept_removed` /
    `func_noexcept_added` as 🟢 **COMPATIBLE** — on an ordinary member or free
    function they alter neither layout nor the mangled symbol. case15 reaches
    🟡 **COMPATIBLE_WITH_RISK** because *introducing `throw`* also raises a
    libstdc++ version requirement, reported separately as
    `symbol_version_required_added` (a RISK-tier deployment signal). So the risk
    verdict is driven by that version-requirement finding, **not** by the
    `noexcept` kind itself: a pure toggle with no new version requirement
    classifies COMPATIBLE. The unwinding hazard described above is the *reason*
    the deployment signal is worth heeding — not something abicheck infers from
    the `noexcept` change alone.

!!! note "The C++17 subtlety"
    C++17 made `noexcept` part of the function *type*, but under Itanium that
    only changes mangling where the *full function-type* is encoded — function
    pointers, references to functions, and templates parameterized by function
    type — **not** the `<bare-function-type>` used for ordinary member/free
    symbols. So toggling `noexcept` on a plain declaration leaves the direct
    symbol unchanged (abicheck: 🟢 COMPATIBLE), but the **same change escalates
    to 🔴 BREAKING** for callers that pass the function through a pointer or
    template where the `E` tag now participates in the mangled name.

---

## 6. Trivial → non-trivial: the invisible calling-convention flip

The System V AMD64 convention passes **trivially-copyable** aggregates directly
in registers, but passes **non-trivially-copyable** ones *by invisible
reference* — the caller materializes the object on the stack and hands the callee
a pointer. Whether a class is trivially copyable is decided by whether it has
*user-provided* copy/move/destructor special members. **A single line flips the
register/memory decision.**

```cpp
/* v1 */ struct Point { double x, y; };                 // trivially copyable
/* v2 */ struct Point { double x, y; ~Point() {} };     // user-provided dtor → non-trivial
```

Layout unchanged, `sizeof` unchanged, mangled symbol unchanged, loader perfectly
happy. But the v1 caller passes `x, y` in `%xmm0, %xmm1` while the v2 callee
reads `%rdi, %rsi` as *pointers* and dereferences them — segfault or silent
garbage, with no toolchain diagnostic
([case69](../../examples/case69_trivial_to_nontrivial.md)).

!!! note "How abicheck sees it"
    No header-diff tool that looks only at declarations catches this. abicheck
    reports `value_abi_trait_changed` by inspecting the DWARF
    trivially-copyable flag.

    **Design rule:** pin the trivially-copyable status of any by-value type
    from version 1. If cleanup might ever be needed, commit *from day one* to
    a user-provided destructor — an empty body `~T() {}` or an out-of-line
    `T::~T() = default;` in the `.cpp`. An in-class `~T() = default;` on the
    first declaration is user-*declared* but not user-*provided*, so it does
    **not** pin the convention.

---

## 7. Base-class position and layout

Multiple inheritance places each base sub-object at a specific offset, and those
offsets are compiled into every upcast and virtual call.

```cpp
/* v1 */ struct Widget : Drawable, Clickable { /*...*/ };
/* v2 */ struct Widget : Clickable, Drawable { /*...*/ };   // bases reordered
```

The type name and every method signature are identical, yet
`static_cast<Drawable*>(widget)` now points into the `Clickable` sub-object,
because the compiler applied v1's offset to v2's layout
([case60](../../examples/case60_base_class_position_changed.md)).

[case37](../../examples/case37_base_class.md) generalizes this with three
independent hazards: **reordering** bases changes `this`-adjustments and which
vptr sits at offset 0; converting non-virtual to **`virtual` inheritance**
restructures the whole object (the virtual base moves to the end and a
vbase-offset table is inserted); **appending** a base grows `sizeof` and shifts
every member.

A related multiple-inheritance trap is **overriding a virtual inherited from a
*non-primary* base** — i.e. any base after the first. Because that base
sub-object sits at a non-zero offset, the override needs a *thunk* that adjusts
`this` back to the sub-object before dispatching. Introducing (or removing) such
an override in a later release changes the set of thunks the vtable must carry
and the `this`-adjustments baked into consumer call sites — a silent break even
though the method's source signature is unchanged.

!!! note "How abicheck sees it"
    Reported as `base_class_position_changed` / `type_base_changed` when DWARF
    or headers are available — ELF symbol tables alone cannot see them, which
    is why C++ ABI checking *requires* debug info or headers.

---

## Modern C/C++ and toolchain ABI hazards

The break families above predate C++11. Newer language features and toolchain
*flags* introduce a second class of hazard: the **declaration looks unchanged in
the header, but the bytes the compiler emits move** because a type's size,
mangling, or passing rule shifted under it. These are the cases reviewers miss
most often, because nothing in the diff "looks like" an ABI change.

| Hazard | What silently changes | abicheck case |
|--------|----------------------|---------------|
| **`_GLIBCXX_USE_CXX11_ABI` flip** | libstdc++ ships *two* `std::string`/`std::list` ABIs in parallel behind the `__cxx11` inline namespace; flipping the macro re-mangles every symbol that touches those types. | [case104](../../examples/case104_glibcxx_dual_abi_flip.md) |
| **ABI tags (`[[gnu::abi_tag]]`)** | A tag is mangled into the symbol name; adding/removing one renames the symbol with no source-visible signature change. | [case113](../../examples/case113_abi_tag_changed.md) |
| **`char8_t` (C++20)** | `const char*` → `const char8_t*` is a *distinct type*: different mangling, and a new overload-resolution result. | [case114](../../examples/case114_char8t_migration.md) |
| **`_BitInt(N)` width** | Changing `N` changes size/alignment and the register/stack class the value is passed in. | [case115](../../examples/case115_bit_int_width_changed.md) |
| **`_Atomic` qualifier** | Adding/removing `_Atomic` can change size, alignment, and whether the object is passed by lock-free path. | [case116](../../examples/case116_atomic_qualifier_changed.md) |
| **`[[no_unique_address]]`** | Lets an empty member overlap the next field; adding it shrinks the struct and shifts every following offset. | [case117](../../examples/case117_no_unique_address.md) |
| **Concept tightening (C++20)** | Narrowing a constraint removes instantiations the consumer relied on — a *source* break with no symbol-table change for already-emitted instantiations. | [case105](../../examples/case105_concept_tightening.md) |
| **LP64 → ILP64 / data-model drift** | `long`/pointer widths change out from under every struct and signature — a whole-ABI shift driven by the target, not the source. | [case112](../../examples/case112_lp64_ilp64.md) |

Several more live only in the **build flags**, not the source, and abicheck
surfaces them as toolchain/deployment risk when build context is captured:
`-fno-exceptions` / `-fno-rtti` (drop EH/RTTI machinery callers may rely on),
`-fshort-enums` (changes enum underlying size — see
[Part 3](03-type-layout.md)), packing/alignment flags, vector-ABI flags, and
CPU-dispatch/IFUNC selection ([case83](../../examples/case83_cpu_dispatch_isa_dropped.md),
[case29](../../examples/case29_ifunc_transition.md)).

!!! warning "Why these need debug info or headers"
    Like the rest of Part 4, every hazard above is recoverable only when DWARF/PDB
    *or* headers are supplied — and the dual-ABI and ABI-tag cases need the
    *mangled* symbol names, so a stripped, name-demangled view can hide them.

---

## How to design C++ libraries for ABI stability

!!! tip "Design patterns for Part 4"
    - **Pure-virtual interface + factory.** Expose an abstract class (no data
      members, no inline methods) and a C-linkage `create_foo()`. Consumers
      hold only the abstract pointer, so you can evolve the implementation
      class without touching any consumer vtable.
    - **Non-Virtual Interface (NVI).** Public methods are *non-virtual*
      wrappers over a small, stable set of `virtual` hooks. You can add public
      methods (non-virtual additions are ABI-safe) without growing the vtable.
    - **Pimpl ABI firewall.** Every data member lives in an `Impl` defined in
      the `.cpp`; the public class holds one `std::unique_ptr<Impl>`.
      `sizeof(Widget)` never changes; offsets are invisible.
    - **Inline namespaces for generational ABI.** Wrap public declarations in
      `inline namespace abi_v1`. For a breaking change, ship `abi_v2` alongside
      and keep the old symbols exported — consumers migrate on their schedule,
      mirroring libstdc++ `__cxx11`.
    - **`-fvisibility=hidden` + export macros.** Shrink the exported surface to
      exactly what you intend to stabilize (see [Part 5](05-linker-elf.md)).

    Full code for each is in
    [Part 7 — Designing for Stability](07-designing-for-stability.md).

---

## Next

Underneath the language-level ABI sits a second contract enforced purely by the
dynamic linker: SONAME identity, symbol visibility, version nodes, calling
conventions, and TLS models — all recorded in the `.so` itself.

➡️ **[Part 5 — ELF & Linker-Level Concerns](05-linker-elf.md)**

*See also:* [ABI Cheat Sheet](../abi-cheat-sheet.md) ·
[Risk examples](../../examples/by-verdict/compatible-risk.md)
