# ABI/API Handling & Recommendations

This is the single conceptual hub for understanding ABI/API change handling in
abicheck. It combines what used to be three separate pages — the stability
guide, the "ABI breaks explained" taxonomy, and the breaking-cases catalog —
into one narrative: *why* breaks happen, *which families* exist, and *how to
design for stability*. For per-case reproductions with code, runtime demos, and
fixes, see the generated **[Examples & Case Encyclopedia](../examples/index.md)**.

## Break families at a glance

Every detected change maps to one of these families. The verdict column shows
the typical classification; the exact verdict per fixture lives in
`examples/ground_truth.json` and the [Examples Encyclopedia](../examples/index.md).

| Family | Representative cases | Typical verdict |
|--------|---------------------|-----------------|
| Symbol/function removal & rename | 01, 12, 58, 66 | 🔴 BREAKING |
| Signature changes (params, return, pointer level) | 02, 10, 33, 46 | 🔴 BREAKING |
| Struct/class layout, alignment & packing | 07, 14, 40, 42, 43, 56, 117 | 🔴 BREAKING |
| Enum value/underlying changes | 08, 19, 20, 57 | 🔴 BREAKING |
| Union layout | 24, 26 (grows) · 26b (no growth) | 🔴 / 🟢 |
| C++ vtable & virtual methods | 09, 23, 38, 68, 72 | 🔴 BREAKING |
| C++ qualifiers, mangling & ABI tags | 21, 22, 30, 71, 86, 101, 113 | 🔴 / 🟠 |
| Trivial → non-trivial (calling convention) | 64, 69 | 🔴 BREAKING |
| Templates, inline & ODR | 16, 17, 47, 59, 79, 85, 87 | 🔴 / 🟢 |
| Modern C/C++ contract shifts (char8_t, _BitInt, _Atomic, concepts) | 105, 114, 115, 116 | 🔴 / 🟢 |
| Transitive/dependency & `detail::` leaks | 18, 48, 74–77, 80, 97, 104, 112 | 🔴 BREAKING |
| ELF/linker metadata (SONAME, visibility, versioning, RPATH, TLS) | 05, 06, 13, 49, 51, 52, 65, 67 | 🔴 / 🟢 |
| Source-only / API-level (rename, access, explicit) | 31, 34, 96, 106 | 🟠 API_BREAK |
| Deployment risk (noexcept, ISA dispatch, version-require) | 15, 83 | 🟡 COMPATIBLE_WITH_RISK |
| Compatible additions & quality signals | 03, 25, 26b, 27, 29, 61, 62, 99 | 🟢 COMPATIBLE |
| Scoped/non-public internal changes | 118, 119, 120 | ✅ NO_CHANGE |

The rest of this page explains the *mechanisms* behind these families and the
design patterns that prevent each one. For a 2-minute scannable card, see the
[ABI Cheat Sheet](abi-cheat-sheet.md); for verdict semantics and exit codes, see
[Verdicts](verdicts.md).

## Introduction

An **API** (Application Programming Interface) is a *source-level* contract: the set of declarations — function signatures, type definitions, macros, and semantic guarantees — that a consumer's source code compiles against. An **ABI** (Application Binary Interface) is the *binary-level* contract between already-compiled artifacts: the exact byte-level layout of types, symbol names and mangling, calling conventions, register usage, vtable shapes, stack-unwinding metadata, and the relocation rules that the dynamic linker relies on. An API break forces downstream code to be edited; an ABI break does not — but it silently corrupts memory, misroutes calls, or fails to resolve symbols at load time, because the consumer binary was produced under assumptions the new library no longer satisfies.

The cost of an ABI break compounds with the size of the ecosystem depending on the library. When `libfoo.so.1` breaks ABI without bumping its SONAME, a Linux distribution must rebuild — and re-test, re-sign, and re-ship — every reverse-dependency in the archive; Debian and Fedora each track hundreds of such transitions per release. In embedded and firmware contexts, an ABI break shipped in an OTA update can brick devices in the field when a pre-linked application loads a new system library whose struct offsets have shifted. Plugin ecosystems — audio hosts loading VST modules, game engines loading mods, browsers loading NPAPI/PPAPI components, IDEs loading extensions — fracture entirely when the host's ABI changes: third-party binaries that shipped years earlier fault on first call, and the plugin author may no longer exist to rebuild them.

abicheck classifies every comparison into one of five verdicts — `NO_CHANGE`, `COMPATIBLE`, `COMPATIBLE_WITH_RISK`, `API_BREAK`, and `BREAKING` — mapped to CI exit codes so that release gates can distinguish a harmless symbol addition from a silent memory-corruption hazard. The five tiers and their exit-code semantics are documented in detail in [Verdicts](verdicts.md). This guide catalogs the concrete mechanisms by which ABI breaks occur; for a condensed checklist consult the [ABI Cheat Sheet](abi-cheat-sheet.md), and for per-case reproductions see the [Examples & Case Encyclopedia](../examples/index.md).

## Part 1: Symbol Contract Breaks

The dynamic linker (`ld.so` on Linux, `dyld` on macOS, the PE loader on Windows) resolves every external reference in an executable by name at load time or at first call. A symbol that existed at link time but is missing at load time is a hard error — no fallback, no default, just `symbol lookup error` and process termination. The four classes of break below each violate the name-keyed contract in a different way: by erasing the name, by keeping the name but changing what it means, by preserving type size while changing type meaning, or by letting a data symbol drift out from under consumers that baked its layout into their own binary.

### Removing or renaming symbols

When an executable is linked against `libfoo.so.1`, every reference to a library function is recorded as a named relocation in the binary's `.rela.plt` (for functions) or `.rela.dyn` (for data). At load time `ld.so` walks those relocations and performs `dlsym`-equivalent lookups against the library's `.dynsym` table. If the name is absent — whether v2 dropped it entirely (see [case01](../examples/case01_symbol_removal.md), where `helper` disappears) or only kept a differently-named function alongside the deletion (see [case12](../examples/case12_function_removed.md), where `fast_add` is removed and `other_func` is added) — the lookup returns `NULL` and the process aborts before `main()` under `RTLD_NOW`, or at the first PLT trampoline under the default lazy binding. A rename is the same mechanism: from the loader's viewpoint, renaming `fast_add` to `fast_add_v2` is a removal of the old name plus an addition of the new one, and every pre-existing binary still resolves against the old name. v1 of case01 exports both entry points:

```c
int compute(int x) { return x * 2; }
int helper(int x)  { return x + 1; }
```

v2 drops `helper`, and every downstream binary that ever called it fails with `./app: symbol lookup error: ./app: undefined symbol: helper` until recompiled against v2 headers. Name identity is the only key `.dynsym` is indexed by — which is why the loader cannot distinguish a removal from a rename, and why neither is safe without a SONAME bump.

### Changing function signatures

Signatures are not part of the symbol name in C — `process` mangles to `process` regardless of whether it takes `(int, int)` or `(double, int)` — so the dynamic linker cheerfully binds v1 callers to v2 implementations whose parameter types disagree. The x86-64 System V ABI passes the first six integer-class arguments in `RDI, RSI, RDX, RCX, R8, R9` and the first eight floating-point arguments in `XMM0..XMM7`, with integer and FP registers assigned from independent queues; anything past those queues spills onto the stack in right-to-left order. When [case02](../examples/case02_param_type_change.md) widens the first parameter from `int` to `double`, the v1 caller loads an integer into `EDI` while the v2 callee reads an FP value from `XMM0` — two disjoint registers — and `XMM0` holds whatever garbage the caller last left there:

```c
/* v1 */ double process(int a, int b)    { return (double)(a + b); }
/* v2 */ double process(double a, int b) { return a + b; }
```

[case10](../examples/case10_return_type.md) is the mirror failure on the return path: widening `int` → `long` makes the callee write all 64 bits of `RAX`, but v1 callers read only `EAX`, truncating `3_000_000_000` to `-1_294_967_296`. Struct-passing changes are worse still, because aggregates straddle the register/stack boundary by classification rules that depend on size, alignment, and member types — a single added `int64_t` field can push an entire argument onto the stack.

### Pointer-level changes

Every pointer on a 64-bit target occupies 8 bytes, so `int *` and `int **` look identical in a symbol's size on the wire. They are not identical in semantics. The v1 and v2 implementations of [case33](../examples/case33_pointer_level.md) make the contrast concrete:

```c
/* v1 */ void process(int *data)  { buf[0] = *data; }
/* v2 */ void process(int **data) { buf[0] = **data; }
```

A v1 caller passes the address of a stack `int`; v2 treats that address as an `int *` and dereferences it again, reading the 32-bit integer value as a 64-bit pointer. The result is almost always an unmapped-page fault, but on unlucky memory layouts the synthesised "pointer" lands inside a valid mapping and the library silently reads or writes the wrong bytes — a data-corruption bug with no crash to trace back to. The same failure occurs on the return path: if `get_buffer()` grows from `int *` to `int **`, v1 callers index through a pointer-to-pointer as if it were a flat buffer and walk off into arbitrary memory.

### Global variable changes

Exported globals are the hardest class to refactor compatibly because the executable bakes in layout facts about the variable at link time. On ELF, a reference to an imported data symbol typically generates a **COPY relocation**: the linker allocates space in the executable's own `.bss` sized to `sizeof(v1_type)`, and at load time `ld.so` memcpy's the library's initial value into that executable-owned slot. Subsequent reads and writes on *both* sides redirect to the executable's copy. If v2 widens the type — as in [case11](../examples/case11_global_var_type.md), `int lib_version` → `long lib_version` — the executable's 4-byte slot cannot hold the 8-byte value; `ld.so` either warns about a size mismatch or silently truncates, so the app reads `705_032_704` where the library wrote `5_000_000_000`. [case58](../examples/case58_var_removed.md) removes the global outright: the COPY relocation has no target, and the process fails to start with `undefined symbol: lib_debug_level`. [case39](../examples/case39_var_const.md) shows the qualifier failure mode in two flavours: when COPY relocation is in play (typical for non-PIE executables on ELF), a mutable-in-v1 global that v2 declares `const` still lives in the app's writable `.bss` copy, so app-side writes succeed but the library's own updates never propagate to that copy — the two sides silently diverge. For PIE binaries that reach the library symbol directly through the GOT, the same change moves the variable into the library's `.rodata` and a write from app code faults with SIGSEGV. Either way the combined demo in case39 also removes `g_legacy_flag`, so the process itself fails to start with an undefined-symbol error before the divergence ever becomes observable.

> **Best practice — keeping the symbol contract intact**
>
> - **Deprecate, don't delete.** Mark outgoing functions `__attribute__((deprecated))` for at least one release, ship an alias (`__attribute__((alias("new_name")))`) spanning old and new names, and only remove on a SONAME bump.
> - **Use versioned symbols.** A linker version script (`GLIBC_2.17 { global: foo; };`) lets you ship `foo@GLIBC_2.17` alongside `foo@@GLIBC_2.34`, so pre-existing binaries keep resolving to the old implementation while new links pick up the new one.
> - **Prefer accessors over exported globals.** `int get_version(void)` is immune to COPY-relocation hazards and lets the library change storage, width, or qualifier without touching consumers.
> - **Freeze signatures; add new entry points.** Model the `ftell` → `ftello` pattern: ship a new symbol for the new type rather than widening the existing one.
> - **Hide layout behind opaque handles.** Publish `typedef struct foo foo_t;` with only `foo_t *` in the public header and force consumers through functions — the library then owns the struct's size and layout outright.

## Part 2: Type Layout Breaks

Every aggregate type published in a header is a byte-level contract: its size, its members' offsets, its alignment, and — for C++ — its vtable shape. Consumer code does not re-read that contract at load time. The compiler bakes it into every caller: `offsetof(s, field)` becomes an immediate displacement in a `mov` instruction, `sizeof(T)` becomes an allocation constant, array indexing multiplies by a stride chosen at compile time. When the library's next release shifts even one offset, every call site compiled against the old layout reads or writes at the wrong address — silently, without a linker error, usually without a crash until adjacent memory is eventually read back.

### Struct/Class Size and Offsets

The most common layout break is appending, inserting, or reordering a struct field. In [case07](../examples/case07_struct_layout.md), `struct Point { int x; int y; }` grows to `{ int x; int y; int z; }`. `sizeof(Point)` goes from 8 to 12, so every caller that allocates `Point` on the stack or inside another struct under-allocates; every caller passing `Point` by value sends 8 bytes while the library reads 12. In [case14](../examples/case14_cpp_class_size.md) the same failure mode strikes C++: a `char data[64]` buffer grows to `char data[128]`, `sizeof(Buffer)` doubles, and v1 callers `new Buffer()` hand the constructor a 64-byte allocation that it promptly zero-fills with 128 bytes, corrupting whatever lives next on the heap. [case43](../examples/case43_base_class_member_added.md) shows the transitive case: adding `int extra_field` to `class Base` shifts `Derived::value` from offset 12 to offset 16, so every subclass member in the ecosystem silently moves. [case40](../examples/case40_field_layout.md) bundles five field-level mutations — type widening, removal, reorder, bitfield resize, append — into a single struct to show that "just one field" changes cascade across the whole layout.

### Alignment and Packing

Alignment is the second axis of layout. [case42](../examples/case42_type_alignment_changed.md) changes only the alignment attribute — fields and sizes stay identical — going from `__attribute__((aligned(8)))` to `__attribute__((aligned(64)))`. v1 callers allocate `CacheBlock` on 8-byte boundaries; v2 code may emit aligned-load instructions (e.g., `vmovdqa`) and fault on misaligned access — the signal delivered varies by architecture and OS (commonly `SIGSEGV` on x86-64 Linux, `SIGBUS` on strict-alignment platforms) — and `malloc` (typically 16-byte aligned) can no longer hand out correctly-aligned storage without `aligned_alloc`. [case56](../examples/case56_struct_packing_changed.md) is the inverse: v1 has natural padding (`char tag` at 0, `int value` at 4, total 12), v2 adds `#pragma pack(1)` and eliminates all padding (`value` at offset 1, total 6). `sizeof` shrinks, every field except `tag` moves, and on strict-alignment architectures (ARM, SPARC) the unaligned `int` access traps. Because `alignas` and `#pragma pack` propagate across translation unit boundaries through the header, a single-line change in one header silently rewrites offsets for every TU that includes it.

### Enum Value Stability

Enumerations look like constants, but they are part of the wire format. In [case08](../examples/case08_enum_value_change.md) `{ RED=0, GREEN=1, BLUE=2 }` becomes `{ RED=0, YELLOW=1, GREEN=2, BLUE=3 }`: inserting `YELLOW` in the middle shifts `GREEN` and `BLUE` by one, so every existing binary that tested `== 1` for green now hits the yellow branch. [case20](../examples/case20_enum_member_value_changed.md) changes `ERROR = 1` to `ERROR = 99` — the same symbolic name, a different integer — which is effectively a protocol rewrite without version negotiation. [case19](../examples/case19_enum_member_removed.md) removes an enumerator: any persisted value, any database row, any network message carrying that integer becomes undefined on read. The safe counterpoint is [case25](../examples/case25_enum_member_added.md): appending `YELLOW = 3` to the end does not perturb existing values and is `COMPATIBLE`. A more insidious failure is [case57](../examples/case57_enum_underlying_size_changed.md), which adds a sentinel `= 0x100000000LL` that forces the compiler to widen the underlying type from `int` to `long`; `sizeof(Color)` jumps from 4 to 8, and every struct embedding `Color` silently grows and relocates its subsequent fields.

### Union Layout

Unions share offset 0 across all members, so adding a new variant does not move existing fields — but the union's size equals the largest member, and that size *does* propagate. [case26](../examples/case26_union_field_added.md) adds `double d` to `union Value { int i; float f; }`: `sizeof` grows from 4 to 8, so every stack allocation, every array stride, every embedding struct shifts. This is `TYPE_SIZE_CHANGED` and classified `BREAKING`. By contrast, [case26b](../examples/case26b_union_field_added_compatible.md) adds `int i` to `union { long l; double d; }` where `max(8, 8, 4) == 8` — the union does not grow, nothing downstream moves, and the verdict is `COMPATIBLE`. The rule is: a new union field is safe if and only if `sizeof(new_member) <= sizeof(old_union)` and `alignof(new_member) <= alignof(old_union)`. [case24](../examples/case24_union_field_removed.md) shows the other direction — removing a variant removes a supported reinterpretation, which is a semantic contract break even when the size is unchanged, because consumers compiled to write `d.f = 3.14f` have no replacement for that access path.

### Bitfields and Flexible Arrays

Bitfields are the most fragile layout primitive because storage-unit allocation is implementation-defined. [case63](../examples/case63_bitfield_changed.md) widens `mode` from 3 bits to 5 bits inside a 32-bit `RegMap`. `sizeof` is unchanged — naive size checks pass — but `channel`, `priority`, and `reserved` all shift two bit positions, so every v1 consumer reads corrupt values with no crash and no diagnostic. This pattern bites hardest in hardware-register maps and protocol headers, exactly the contexts where bitfields are most useful. Flexible array members have the opposite static-size profile but the same failure: [case70](../examples/case70_flexible_array_member_changed.md) changes `float data[]` to `double data[]`. The fixed header of `struct Packet` is unchanged — `sizeof(Packet)` compares equal — but every caller that allocated `sizeof(Packet) + count * sizeof(float)` now holds half the needed memory, and `p->data[i]` indexes with stride 8 instead of 4.

### Pointer Chains and Arrays

Multi-level type changes propagate through indirection. [case45](../examples/case45_multi_dim_array_change.md) changes `float data[4][4]` to `double data[4][4]` inside `struct Matrix`: the inner element type changes, the struct doubles from 72 to 136 bytes, the array stride doubles, and both `matrix_get` and `matrix_set` change return/parameter widths so the caller reads the wrong register. [case46](../examples/case46_pointer_chain_type_change.md) reaches further — a function returning `int **` becomes `long **`, a two-level pointer chain where only the ultimate pointee type changes. Every v1 caller that dereferences the returned chain and writes an `int` writes 4 bytes into what v2 treats as an 8-byte cell, corrupting the adjacent slot. abicheck walks pointer and array types structurally during `FUNC_RETURN_CHANGED` and `PARAM_TYPE_CHANGED` detection precisely because a surface-level "both sides return a pointer" comparison would miss these.

> **Best Practice — Defending Type Layout**
>
> - **Opaque handles.** Expose only `struct foo *` to callers; define the struct in a `.c` file. Callers cannot take `sizeof` or `offsetof`, so layout is free to change. OpenSSL 1.1.0's migration from direct `EVP_MD_CTX` access to `EVP_MD_CTX_new`/`EVP_MD_CTX_free` opaque handles is the canonical real-world precedent — it sealed off decades of accumulated struct-layout churn.
> - **Pimpl idiom (C++).** The public class holds a single `d_ptr` to a private `Impl`; all state lives in `Impl`. `sizeof` of the public class never changes. Qt enforces this as a binary-compatibility rule across every public class in every release, which is why Qt 5.x maintained ABI for years despite internal refactors.
> - **Reserved padding fields.** Include `void *reserved[N]` or `uint64_t _pad[N]` at the end of every public struct. Future releases can repurpose slots without changing `sizeof` or shifting offsets. POSIX `pthread_attr_t` and many kernel UAPI structs use this deliberately.
> - **Freeze the enum underlying type.** In C++ write `enum class Color : int32_t { ... };` explicitly; in C keep all values within `int` range or add an explicit sentinel value such as `INT32_MAX`. Never let a new enumerator silently widen the type (see case57).
> - **Never reorder or insert fields — use append-only evolution.** Reordering a field, inserting one in the middle, or removing one is always breaking. If a new field is required, append it at the end of the struct, and only when no embedded `sizeof(T)` assumption exists (see case26 vs case26b for the union analog).

## Part 3: C++ ABI Specifics

C++ is the language where ABI stability is hardest. Every class with a virtual method carries a hidden pointer to a statically-ordered table of function pointers; every method name is mangled through a grammar that encodes qualifiers, namespaces, template arguments, and parameter types; every struct with a user-defined destructor changes how it is passed between functions. The Itanium C++ ABI — used by GCC and Clang on Linux, macOS, the BSDs, and most embedded targets — is rigid by design: it guarantees cross-compiler interoperability at the cost of making almost any visible change to a class a potential binary break. MSVC on Windows uses a different, equally rigid ABI with the same categories of pitfalls. What follows is a tour of the seven C++ mechanisms most likely to silently corrupt a consumer binary.

### 1. Vtable and Virtual Methods

Every polymorphic class carries a hidden `vptr` as its first word, pointing to a per-class vtable — a static array of function pointers indexed by the order virtual methods are declared. The Itanium C++ ABI fixes this slot ordering as a public part of the class contract: callers compile `widget->resize()` into `(*widget->vptr[1])(widget)` where `1` is baked into the call site. The ordering is determined at the point of declaration, propagates unchanged into every derived class, and cannot be renegotiated after the first binary ships.

Inserting a new virtual method *before* an existing one silently shifts every later slot. [case09](../examples/case09_cpp_vtable.md) demonstrates the canonical form: a new `recolor()` at slot 1 reroutes every call to `resize()` into `recolor()`, producing wrong results without a crash. Making an existing method pure-virtual ([case23](../examples/case23_pure_virtual_added.md)) replaces the slot with `__cxa_pure_virtual`, turning every old call site into an unconditional `abort()`.

Adding the *first* virtual method to a previously non-polymorphic class is the most destructive variant ([case68](../examples/case68_virtual_method_added.md)): a vptr is prepended, every data member shifts by `sizeof(void*)`, and `sizeof` grows — readers of the old layout interpret the new vptr as their first field. [case38](../examples/case38_virtual_methods.md) combines virtual-insertion, pure-virtual promotion, and copy-constructor deletion in a single release to show how the hazards compound. The only safe addition is to *append* new virtual methods after every existing slot, and only when no derived classes exist in consumer binaries that would themselves need to extend the vtable.

### 2. Method Qualifiers

Method qualifiers are load-bearing parts of the Itanium mangled name, not cosmetic source-level annotations. A `const` member function mangles with a `K` marker in the parameter list, a `volatile` one with `V`, and ref-qualified methods (`&`/`&&`) with `R`/`O`. Any edit that adds, removes, or flips one of these markers renames the symbol from the linker's perspective.

Dropping `const` from `Widget::get() const` ([case22](../examples/case22_method_const_changed.md)) changes the symbol from `_ZNK6Widget3getEv` to `_ZN6Widget3getEv` — the leading `K` disappears, the old symbol vanishes from `.dynsym`, and every consumer hits `symbol lookup error` at load time. The failure mode is a clean `dlopen` abort rather than silent corruption, which makes it one of the easier C++ breaks to diagnose in production.

Converting an instance method into a `static` one ([case21](../examples/case21_method_became_static.md)) is subtler: the mangled name is often identical (`_ZN6Widget3barEv` for both forms), so the linker is happy, but the calling convention silently diverges — the v1 caller passes an implicit `this` in `%rdi` that the v2 static callee never reads, and the function returns data computed from register garbage. Adding `const`/`volatile` to struct *fields* ([case30](../examples/case30_field_qualifiers.md)) leaves layout unchanged but reclassifies the surface as a source break, and `volatile` additionally invalidates any cached loads in already-compiled callers. Treat every qualifier edit on a public declaration as equivalent to renaming the symbol.

### 3. Templates and Inline

Inline and template code lives at the boundary where the One Definition Rule meets the link model, and that boundary is where ABI assumptions get baked into *consumer* binaries without the library ever seeing them. An explicitly instantiated `Buffer<int>` in `libfoo.so` produces the mangled symbol `_ZN6BufferIiEC1Em`; adding a `capacity_` field ([case17](../examples/case17_template_abi.md)) keeps the symbol name identical while growing `sizeof(Buffer<int>)` from 16 to 24 bytes. The consumer stack-allocates 16 and the v2 constructor writes 24, corrupting the caller's frame — a classic stack smash with no header-level signal.

Header-only inline definitions embed the *body* into each consumer translation unit, so the implementation that callers execute is frozen when they compile. Changing the inline implementation between releases produces ODR violations that LTO can detect and link-time surprises that LTO cannot; worse, two consumers who pulled in different versions of your header will silently disagree about what your function does.

Moving a function from inline-in-header to outlined-in-`.so` ([case47](../examples/case47_inline_to_outlined.md)) is compatible — old binaries keep their inlined copy, new binaries call the export. The inverse transition ([case59](../examples/case59_func_became_inline.md)) or mixing builds where the header says outlined but the `.so` does not ([case16](../examples/case16_inline_to_non_inline.md)) removes the symbol from `.dynsym` and hard-fails at load. Template instantiations, inline functions, and `constexpr` bodies are part of the ABI even though they never appear in `readelf -Ws`.

### 4. Covariant Returns and Inline Namespaces

An **inline namespace** is transparent to source-level name lookup but is mangled into every symbol declared inside it, making it the canonical Itanium mechanism for generational ABI versioning. [case71](../examples/case71_inline_namespace_moved.md) shows a library moving `encrypt` from `inline namespace v1` to `inline namespace v2`: source code that writes `crypto::encrypt(...)` compiles unchanged against both versions, but the emitted symbol goes from `_ZN6crypto2v17encryptE...` to `_ZN6crypto2v27encryptE...` — a clean break for pre-compiled callers.

This is precisely the device libstdc++ uses for its **dual ABI**. GCC 5 introduced `std::__cxx11::basic_string` alongside the legacy COW `std::string`, gated on the `_GLIBCXX_USE_CXX11_ABI` preprocessor switch; every distribution spent years untangling the resulting symbol-lookup failures as users mixed libraries built with the two flavors. The lesson is that inline namespaces are a *power tool*: wielded deliberately they enable forward evolution, but switching them unintentionally renames every symbol you export.

Covariant return types interact with vtable layout directly. A derived `Circle::clone()` returning `Circle*` generates a thunk that adjusts `this` before delegating; inserting a new intermediate base class ([case72](../examples/case72_covariant_return_changed.md)) shifts sub-object offsets, changes the covariant's return type, and invalidates every hardcoded vtable slot in consumer binaries. Used deliberately, inline namespaces let you ship breaking changes under a new mangled surface while keeping the old one exported for compatibility; used accidentally, they are an invisible renaming of every function you declare.

### 5. noexcept

[case15](../examples/case15_noexcept_change.md) is classified `COMPATIBLE_WITH_RISK`, not `BREAKING`, and the reasoning is worth internalizing. Before C++17, `noexcept` was not part of the function type, so the Itanium mangler ignored it: `void reset() noexcept` and `void reset()` both mangle to `_ZN6Buffer5resetEv` and resolve to the same `.dynsym` entry. Removing `noexcept` therefore *does not* break linkage — hence not `BREAKING`.

What it does break is the caller's unwinding assumption. The v1 compiler saw `noexcept` and omitted exception landing pads, cleanup frames, and `.eh_frame` entries in the call site; if the v2 implementation now throws, the exception propagates into a frame with no unwinding metadata and `std::terminate()` fires unconditionally. Every destructor that was supposed to run during stack unwinding is skipped, every `catch` block that would have handled the exception is bypassed, and the process dies.

abicheck also flags the associated GLIBCXX version bump that appears when `throw` is introduced (`SYMBOL_VERSION_REQUIRED_ADDED`), which is a deployment-risk signal rather than a linkage failure. The `_WITH_RISK` tier exists for exactly this shape of change: binary-linkable, source-recompilable, but semantically unsafe for binaries built under the stricter old contract. C++17 promoted `noexcept` to part of the function type, but under the Itanium C++ ABI that only changes mangling in contexts where the full *function-type* is encoded — function pointers, references to functions, and templates parameterized by function type — not in the `<bare-function-type>` used for ordinary member and free-function symbols. So toggling `noexcept` on a plain declaration remains `_WITH_RISK` for those direct symbols, but the same change can escalate to `BREAKING` for callers that pass the function through a pointer or template where the `E` tag on the function-type encoding now participates in the mangled name.

### 6. Trivial to Non-Trivial

The System V AMD64 calling convention — and its equivalents on other Itanium-ABI platforms — passes **trivially-copyable** aggregates directly in registers (`%xmm0`/`%xmm1` for a pair of doubles, `%rdi`/`%rsi` for two pointers), but passes **non-trivially-copyable** ones by invisible reference: the caller materializes the object on the stack and hands the callee a pointer. Whether a class is trivially copyable is determined by whether it has user-provided copy/move/destructor special members — a *single line of code* can flip the register/memory decision.

[case69](../examples/case69_trivial_to_nontrivial.md) shows `struct Point { double x, y; }` gaining an empty user-defined `~Point() {}`: the layout is unchanged, `sizeof` is unchanged, the mangled symbol is unchanged, and the dynamic linker resolves the call perfectly. But the v1 caller passes `x`, `y` in `%xmm0`, `%xmm1` while the v2 callee reads `%rdi`, `%rsi` as pointers and dereferences them — segfault or silent garbage with no diagnostic from the toolchain.

No header-diff tool that looks only at declarations will catch this; abicheck reports it as `value_abi_trait_changed` by inspecting the DWARF trivially-copyable flag. Any class you expect callers to pass by value across a library boundary must have its trivially-copyable status pinned from version 1. If cleanup might ever be needed, commit from day one to a *user-provided* destructor — either an empty body (`~T() {}`) or an out-of-line defaulted definition (`~T();` in the header, `T::~T() = default;` in the `.cpp`). An in-class `~T() = default;` on the first declaration is user-declared but *not* user-provided, so it does not make the type non-trivial and does not pin the calling convention.

### 7. Base Class Position and Layout

Multiple inheritance places each base sub-object at a specific offset inside the most-derived object, and those offsets are compiled into every upcast and virtual call at the call site. [case60](../examples/case60_base_class_position_changed.md) shows the textbook case: swapping `Widget : Drawable, Clickable` to `Widget : Clickable, Drawable` leaves the type name and all method signatures identical, yet `static_cast<Drawable*>(widget)` now produces a pointer into the `Clickable` sub-object because the compiler applied v1's zero offset to a v2 layout that moved `Drawable` further down.

[case37](../examples/case37_base_class.md) generalizes this with three independent hazards on the same class. Reordering bases changes `this`-pointer adjustments and reshuffles which vptr sits at offset 0. Converting non-virtual to `virtual` inheritance restructures the entire object: the virtual base moves to the end of the most-derived layout and a vbase-offset table is inserted to resolve it at runtime. Appending a new base class grows `sizeof` and shifts every data-member offset, just as adding a first virtual method does.

All three variants are reported as `BASE_CLASS_POSITION_CHANGED` or `type_base_changed` when DWARF or header information is available; ELF symbol tables alone cannot see them, which is why C++ ABI checking requires either debug info or headers. Base-class composition is, along with vtable ordering, one of the two C++ design decisions you cannot revisit after publishing a library — prefer composition and Pimpl for anything you expect to evolve.

> **Best Practice — Designing C++ libraries for ABI stability**
>
> - **Interface versioning via pure-virtual interface + factory.** Expose a pure-virtual class (no data members, no inline methods) and a C-linkage factory function `create_foo()`. Consumers hold only the abstract pointer, so you can evolve the implementation class freely without touching any consumer vtable layout.
> - **Non-Virtual Interface (NVI) pattern.** Make your public methods *non-virtual* wrappers that call a small, stable set of `virtual` hooks. You can add new public methods (non-virtual additions are ABI-compatible) without appending vtable slots, and you can change the hook set only when you intend an ABI bump.
> - **ABI firewall via opaque pointers (Pimpl).** Put every data member into an `Impl` struct whose definition lives only in the `.cpp`; the public class holds a single `std::unique_ptr<Impl>`. `sizeof(Widget)` never changes, field offsets are invisible, and you can add, remove, or reorder internal state without ABI consequences.
> - **Inline namespaces for generational ABI.** Wrap every public declaration in `inline namespace abi_v1 { ... }`. When you need a breaking change, ship `abi_v2` alongside `abi_v1` and keep the old symbols exported; consumers migrate on their own schedule, mirroring libstdc++ `__cxx11`.
> - **`-fvisibility=hidden` with explicit export macros.** Compile with hidden default visibility and annotate exported declarations with a `FOO_API` macro (expanding to `__attribute__((visibility("default")))` on ELF and `__declspec(dllexport)` on PE). This shrinks the exported surface to exactly what you intend to stabilize, eliminating accidental ABI commitments on internal helpers, inline template bodies, and private vtables.
## Part 4: ELF and Linker-Level Concerns

A second contract sits between the source-level ABI and the running
process: the one enforced by the dynamic linker. SONAME, visibility bits,
version nodes, calling-convention attributes, and the TLS access model are
all recorded in the `.so` and consulted at load time.

### SONAME and Library Identity

The SONAME is how `ld.so` answers "is this the library you asked for?". It
lives in the `DT_SONAME` entry of `.dynamic` and is set via
`-Wl,-soname,libfoo.so.MAJOR` at link time. When an app links against
`libfoo.so`, the linker copies the SONAME — not the filename — into
`DT_NEEDED`, and at runtime `ld.so` searches for a file (usually an
`ldconfig`-managed symlink) matching that string.

[Case 05](../examples/case05_soname.md) covers a library built
without `-Wl,-soname` at all: `DT_NEEDED` points at the bare `libfoo.so`,
which `ldconfig` cannot manage, so shipping `libfoo.so.1` later breaks
every consumer. [Case 50](../examples/case50_soname_inconsistent.md)
is the subtler bug where a 1.x release is tagged `libfoo.so.0`: packaging
generates dependencies on the wrong major, and the cutover forces a
distribution-wide rebuild. Rule: SONAME major equals ABI epoch, and it
never silently changes.

### Symbol Visibility

Every `.dynsym` entry has an `st_other` visibility byte: `STV_DEFAULT`
(public, interposable), `STV_HIDDEN`, `STV_PROTECTED` (exported but not
interposable), or `STV_INTERNAL`. Without `-fvisibility=hidden`, every
non-`static` function defaults to `STV_DEFAULT`, dragging the entire
translation unit into the public ABI.
[Case 06](../examples/case06_visibility.md) is the accidental
leak: `internal_helper` was never intended as public API, but lacking
`static` consumers can resolve it — the later "cleanup" that hides it
breaks them. [Case 53](../examples/case53_namespace_pollution.md)
is the related design error: exporting unprefixed names like `init` that
collide in the process's flat symbol namespace.
[Case 51](../examples/case51_protected_visibility.md) rounds it
out: `DEFAULT` → `PROTECTED` is ABI-compatible for normal callers but
silently defeats `LD_PRELOAD` interposition.

### Symbol Versioning

A version script (`-Wl,--version-script=libfoo.map`) groups symbols into
named nodes like `LIBFOO_1.0`, recorded in `.gnu.version_d` and tagged in
`.gnu.versym`; consumers carry matching `.gnu.version_r` entries. This lets
one `.so` ship multiple ABI generations side by side.
[Case 13](../examples/case13_symbol_versioning.md) shows that
*adding* a version script is backward compatible — old binaries have no
`DT_VERNEED`, so `ld.so` resolves by name.
[Case 65](../examples/case65_symbol_version_removed.md) is the
opposite: once a node has shipped, removing it deletes every symbol it
tagged. glibc's `GLIBC_2.0` has been append-only since 1997 — which is
why a binary built against an old glibc still loads against a current one,
and why OpenSSL 3.0's version-node removals forced the SONAME bump from
`libssl.so.1.1` to `.so.3`.

### Calling Conventions

A calling convention is the register-and-stack contract: which registers
hold args, which are callee-saved, and how the return comes back. On
x86-64 the two you meet are System V AMD64 (Linux/macOS/BSD, args in
`rdi, rsi, rdx, rcx, r8, r9`) and Microsoft x64 (Windows or via
`__attribute__((ms_abi))`, args in `rcx, rdx, r8, r9`). On 32-bit x86 the
zoo is larger: `cdecl`, `stdcall`, `fastcall`, `thiscall`, `vectorcall`.
[Case 64](../examples/case64_calling_convention_changed.md) shows
the attribute flipping silently: the v1 caller loads pointers into
`rdi`/`rsi`, the v2 `ms_abi` callee reads `rcx`/`rdx`, and the function
operates on stale register contents — zero results or a segfault.
`abicheck` catches it by diffing the `DW_AT_calling_convention` DWARF
attribute; the signature is unchanged, so name-and-type-only checks miss
it.

### Security Metadata

The `PT_GNU_STACK` program header advertises whether the process stack
must be executable, and the linker unions it across input objects — so a
single assembly file missing its `.note.GNU-stack` annotation promotes the
entire `.so` (and every process that loads it) to an executable stack.
[Case 49](../examples/case49_executable_stack.md) shows
`readelf -l` reporting `RWE` instead of `RW`; rpmlint and Debian lintian
both reject the package.
`DT_RPATH`/`DT_RUNPATH` hold extra linker search paths.
[Case 52](../examples/case52_rpath_leak.md) shows a build system
baking `/home/build/myproject/lib` into the artifact: it only works on the
build host, and anyone who can write that path gets a library-injection
primitive. Use `$ORIGIN`-relative paths or strip `RPATH` entirely.

### Language Linkage and TLS

[Case 66](../examples/case66_language_linkage_changed.md) covers
`extern "C"` removal during a C++ modernization: source still compiles,
but the `.dynsym` symbol flips from unmangled `parse_config` to mangled
`_Z12parse_configPKc`, and every pre-linked consumer fails at load time.
Treat `extern "C"` blocks as part of the public ABI.
TLS has four access models: `global-dynamic` (default for `.so`,
`dlopen`-safe), `local-dynamic`, `initial-exec` (faster but requires
presence at startup — `dlopen` fails), and `local-exec` (main executable
only). Libraries intended for `dlopen` must avoid `initial-exec`.
[Case 67](../examples/case67_tls_var_size_changed.md) adds a
second hazard: any exported `__thread` struct whose layout shifts corrupts
consumers per-thread. Freeze size, layout, and access model of TLS exports
as first-class ABI.

> **Best practice**
>
> - **Version scripts as the source of truth.** A `.map` file enumerating
>   every intentional export is the canonical place to negotiate API surface.
> - **`ABI_EXPORT` macro discipline.** Build with `-fvisibility=hidden` and
>   annotate public functions with a project-specific macro.
> - **CI gate: `abicheck` on every PR.** Dump the previous release, compare
>   the candidate, fail on any `BREAKING` not paired with a SONAME bump.
> - **Never link with absolute `--rpath`.** Use `$ORIGIN` or install-time
>   rewriting; absolute build paths are non-portable and a security hazard.
> - **Declare TLS access models explicitly.** If a TLS variable is ever
>   reached via `dlopen`, pin `-ftls-model=global-dynamic`.

## Part 5: Subtle and Transitive Breaks

The breaks covered in this part are the ones that survive code review. The
exported symbol table is byte-identical, every function keeps its signature, and
`nm --dynamic` reports no diff between `libfoo.so.1` and its replacement — yet
consumers corrupt memory on the first call. What these cases share is a
*transitive* dependency: an ABI contract implied by a type the library doesn't
itself define but publishes through a header, a padding field, or a nested
member. Static analyzers that look only at the shipped `.so` miss them;
DWARF-aware tools catch most but require debug info to travel with the binary.

### Dependency Leaks

When a public header includes a third-party type — `std::string`, `boost::any`,
`tbb::task_arena`, `grpc::Status` — the library silently inherits that type's
ABI contract. Upgrade the third-party library and every consumer's compiled
size, field offsets, and vtable assumptions become wrong, even though the
wrapper library's own source never changed.
[Case 18](../examples/case18_dependency_leak.md) demonstrates this
with a `ThirdPartyHandle` that grows from 4 to 8 bytes: `libfoo`'s exported
symbols are identical, `nm` and naive `abidiff` see no difference, but a caller
built against v1 headers allocates a 4-byte struct that the v2 library reads
8 bytes from. libstdc++'s dual-ABI split (`std::string` after GCC 5) and the
TBB 2021.3 `task_arena` re-layout both propagated through exactly this
mechanism, fracturing every consumer who had leaked the type into its public
API. abicheck flags this only when DWARF for the third-party type is present
in the shipped `.so`; stripped distributions hide the hazard entirely, which
is why the fix is structural — pimpl or opaque handles — not tooling.

### Anonymous Structs

C and C++ permit unnamed nested structs and unions inside a public type. The
containing type's size and alignment depend entirely on the unnamed member's
contents, but the anonymous member has no stable name to refer to in a diff,
and in C++ it changes the mangled layout without touching any source-visible
identifier.
[Case 36](../examples/case36_anon_struct.md) shows a
`struct Variant { int tag; union { int i; float f; }; }` where replacing
`float f` with `double d` inflates the union from 4 to 8 bytes and shifts the
whole struct's size from 8 to 16, moving `i` from offset 4 to offset 8 due to
8-byte alignment. A caller allocating `sizeof(Variant) == 8` on the stack then
calls into a v2 library that reads `i` at offset 8, four bytes past the
allocation, and lands in uninitialized memory. The source diff is one line
inside an anonymous scope; the ABI diff is total. abicheck traces the layout
through DWARF's anonymous-member rules and reports it as `TYPE_SIZE_CHANGED`
with the exact member offset delta.

### Type Kind Changes

Swapping `struct` for `union`, `enum` for plain `int`, or `class` for `struct`
at the same name — even when the size happens to match — is always an ABI
break, because the *semantics* of member storage differ.
[Case 55](../examples/case55_type_kind_changed.md) changes `Data`
from a struct with sequential fields `x, y` (size 8) to a union where `x` and
`y` overlap at offset 0 (size 4): `sizeof` shrinks, `y`'s offset moves, and
writing one member now clobbers the other. Even a same-size swap — for
example, `enum E : int` to plain `int` in a C++ API — breaks overload
resolution and name mangling (`E` and `int` mangle differently), so function
symbols vanish from the new `.so`. abicheck reads DWARF's
`DW_TAG_structure_type` vs `DW_TAG_union_type` vs `DW_TAG_enumeration_type`
and classifies any transition as `TYPE_KIND_CHANGED` / BREAKING regardless of
byte-for-byte size equality, because a consumer's code generation assumes the
kind, not just the footprint.

### Reserved Field Misuse

Reserving padding fields for future growth — `int __reserved1`,
`char _pad[16]` — is the standard way to extend a struct without bumping
SONAME, but it only works if *no shipped binary ever touched the reserved
bytes*. The moment a consumer writes to reserved storage (deliberately, via a
cast, or accidentally, via `memset(&s, 0xFF, sizeof(s))` followed by
field-wise init), repurposing those bytes becomes a silent data corruption.
[Case 54](../examples/case54_used_reserved_field.md) shows the
*correct* pattern: v1 ships `__reserved1` and `__reserved2` at defined
offsets; v2 renames them to `priority` and `max_retries` with the same types
and offsets, and abicheck's `_diff_reserved_fields` detector recognizes the
naming convention (`__reserved`, `_reserved`, `__pad`, `_unused`) and
classifies the transition as COMPATIBLE. The hazard is that there is no way
for the library author to *verify* that no consumer ever wrote to the
reserved slot; the safety of the pattern rests on a documented contract that
users zero-initialize and ignore the field. Linux's `struct stat`, glibc's
`pthread_attr_t`, and Wayland's protocol structs all rely on exactly this
contract.

### Leaf Structs Through Pointers

Pointer indirection is the single strongest ABI firewall available in C: a
caller that handles only `T*` is agnostic to `sizeof(T)`, to `T`'s field
offsets, and to the kind-tag of `T`.
[Case 48](../examples/case48_leaf_struct_through_pointer.md)
contrasts this with the failing case — a `Container` that *embeds* `Leaf` by
value. When `Leaf` grows from 4 to 8 bytes, `Container::flags` shifts from
offset 8 to offset 16; the public API still takes only `Container*`, but the
size change propagates through embedding, and a v1-compiled caller reads
`flags` at the wrong offset. The fix is to replace the embedded `Leaf` with
`Leaf*` (pointer to incomplete type declared via `struct Leaf;`): the caller's
compilation now depends only on pointer size, which is stable per ABI, and
the library alone controls allocation and layout. This is the mechanism
behind every opaque-handle C API (`FILE*`, `sqlite3*`, `git_repository*`) —
the only type that crosses the ABI boundary is a pointer, so layout evolution
is private to the library.

> **Best practice**
>
> - **Opaque wrappers around third-party types.** Never forward-publish
>   `std::string`, `boost::any`, `tbb::task_arena`, or any vendored-dependency
>   type in your headers; wrap it in a type you own and control.
> - **Stable DTOs at the API boundary.** Define plain structs with explicit
>   layout for every value that crosses the ABI, and treat those DTOs as a
>   versioned schema separate from the library's internal types.
> - **Build-time abicheck in CI.** Run `abicheck compare` against the last
>   released `.so` on every PR; flag anything above `COMPATIBLE_WITH_RISK` as
>   a release blocker.
> - **Zero reserved fields before public release**, or commit in documentation
>   to never activating them. Reserved padding that is never used is free;
>   reserved padding whose safety you can't audit is a future BREAKING
>   verdict.
> - **Pointer-to-incomplete-type for anything you might evolve.** If you
>   can't guarantee a struct's layout for the lifetime of a SONAME, don't
>   expose the definition — expose a forward-declared tag and a
>   constructor/destructor pair.

## Global recommendations

Five rules subsume every mechanism above:

1. **Treat public headers as ABI contracts.** Anything reachable from a public
   header — type layout, enum values, vtable shape, exported globals — is part
   of the binary contract whether or not you intended it to be.
2. **Govern release identity.** Use SONAME + symbol versioning + a
   `-fvisibility=hidden` export policy on every release; bump the SONAME major
   on any binary-incompatible change. abicheck surfaces these as
   `soname_bump_recommended`, `symbol_version_node_removed`,
   `symbol_moved_version_node`, and `version_script_missing`.
3. **Prefer opaque handles and Pimpl** over exposing mutable layouts, so the
   library — not the consumer — owns size, offsets, and the kind-tag of a type.
4. **Evolve additively, never in place.** Append new symbols, enum members, and
   struct fields (where no embedded `sizeof` assumption exists); ship breaking
   changes under a new inline-namespace or a new symbol rather than mutating an
   existing one.
5. **Gate every PR with abicheck in CI.** Dump the last released artifact and
   compare the candidate; block anything above `COMPATIBLE_WITH_RISK` that is
   not paired with a deliberate SONAME bump. See the
   [GitHub Action](../user-guide/github-action.md) for a ready-to-paste workflow.

## Detection coverage and roadmap

abicheck detects **183 change kinds** today (see the
[Change Kind Reference](../reference/change-kinds.md)), spanning every family in
the table above — including the calling-convention, alignment/packing,
bit-field, dual-ABI (`_GLIBCXX_USE_CXX11_ABI`), ABI-tag, `char8_t`, `_BitInt`,
`_Atomic`, and CPU-dispatch cases that were once on the wishlist. Areas still
deepening: richer cross-compiler ABI-drift modelling (GCC vs Clang vs MSVC for
the same headers) and LTO/visibility interactions where an inlined symbol
disappears. The authoritative, always-current taxonomy is the generated
[Change Kind Reference](../reference/change-kinds.md) and
[Examples Encyclopedia](../examples/index.md).
