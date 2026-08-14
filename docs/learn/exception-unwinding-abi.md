---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - exception-unwinding-abi
depends_on:
  - abicheck/diff_symbols.py
lifecycle: active
generated: false
---

# Exception Unwinding: The Machinery Behind `noexcept`

[Part 4 §5](abi-series/04-cpp-abi.md#5-noexcept-why-this-is-risk-not-a-hard-break)
explains why removing `noexcept` is deployment *risk*, not a hard break: it
turns on the caller's **unwinding assumption** without saying what that
machinery actually is. It is a full ABI in its own right, and the Itanium
C++ ABI freezes it as part of your binary contract — this page is that
machinery.

## The unwinding tables and the personality routine

When you throw, control does not "return" — the runtime **unwinds** the
stack, running each frame's destructors on the way out until it finds a
matching handler. To do that without any source, it reads two
compiler-emitted tables baked into the binary:

- **`.eh_frame`** — call-frame information (CFI): for every code address, how
  to restore registers and find the caller's frame. This is what lets the
  unwinder walk frames it has never seen.
- **`.gcc_except_table`** — the *language-specific data area* (LSDA): per
  function, which address ranges are covered by which `catch` clauses and
  which cleanups (destructors) must run.

Each frame that participates names a **personality routine** — for
GCC/Clang C++ this is `__gxx_personality_v0`. The generic unwinder
(`_Unwind_RaiseException` in libgcc/libunwind) walks frames using
`.eh_frame`, and at each frame calls the personality routine, which reads
that frame's LSDA to answer "does this frame catch this exception, or does
it just have cleanups to run?" The throw itself goes through the ABI's
`__cxa_allocate_exception` / `__cxa_throw` entry points.

The consequence: **these tables and the `__gxx_personality_v0` reference are
part of the artifact's ABI**, exactly like the vtable or a mangled symbol. A
caller compiled expecting a landing pad relies on them existing and being
correct — the same dependency that makes a silent `noexcept` removal
([Part 4 §5](abi-series/04-cpp-abi.md#5-noexcept-why-this-is-risk-not-a-hard-break))
dangerous.

## Throwing across a DSO boundary

An exception thrown in `libfoo.so` and caught in the main program (or
another `.so`) has to cross a module boundary — and catch matching is
**RTTI-based**. `__cxa_throw` carries a pointer to the exception type's
`std::type_info`, and the personality routine matches it against each
`catch`'s `type_info`. This is the same RTTI/`type_info` object that backs
the vtable in
[Part 4 §1](abi-series/04-cpp-abi.md#1-vtables-and-virtual-methods) and
that `-fno-rtti` strips (see the
[modern hazards table](modern-cpp-toolchain-hazards.md)).

Matching *ideally* compares `type_info` by pointer identity — but each DSO
can emit its **own copy** of a type's `type_info`. The GNU runtime therefore
falls back to comparing the mangled **type-name string** when the pointers
differ, and relies on the dynamic linker to **merge** the copies to one when
the `type_info` has default visibility. That is the trap:

> If a type used in a cross-DSO exception has **hidden visibility** (or its
> `type_info` is otherwise not exported/merged — see
> [Part 5 §Symbol visibility](abi-series/05-linker-elf.md#2-symbol-visibility)),
> the two modules can hold `type_info` copies the runtime refuses to equate.
> The `catch` then **does not match**, the exception unwinds past the
> intended handler, and `std::terminate()` fires.

The rule that follows: any type thrown across a library boundary must have
**default visibility on its `type_info`** (in practice: a polymorphic type
with a key function, or an explicitly exported RTTI symbol), so both sides
share one identity.

## Mixing `-fexceptions` and `-fno-exceptions`, and changing throw specs

`-fno-exceptions` tells the compiler *no exception will ever pass through
this code*, so it omits landing pads, cleanups, and often the
`.eh_frame`/LSDA detail those need. A translation unit built that way
becomes a **no-throw barrier**: if an exception is unwound *through* one of
its frames, there is no metadata to run destructors or continue — the
runtime calls `std::terminate()`. Building half a call chain
`-fno-exceptions` and half `-fexceptions` is therefore an ABI decision, not
just an optimization flag, and flipping a shipped library between the two
changes what every caller can assume.

Changing a function's **exception specification** is the same hazard
[Part 4 §5](abi-series/04-cpp-abi.md#5-noexcept-why-this-is-risk-not-a-hard-break)
already dissects — pre-C++17 it is invisible to the mangled name, so it
links fine but silently alters the caller's unwinding contract. (C++17
removed dynamic `throw(T)` specifications entirely; only `noexcept`
remains.) We do not repeat that analysis here.

!!! note "How abicheck sees it"
    Be honest about the boundary: **whether a given throw is actually
    caught across a DSO is not decidable from the artifacts** — it depends
    on runtime control flow abicheck does not simulate, and on which
    `type_info` copies the loader merges in a particular process. abicheck
    does **not** try to prove exception-safety end-to-end.

    What it *can* observe is structural and symbol-level:

    - the `-fno-exceptions` / `-fno-rtti` **build flags**, surfaced as
      toolchain/deployment risk when build context is captured (same row as
      the [modern hazards](modern-cpp-toolchain-hazards.md) table);
    - the presence/removal or visibility change of RTTI symbols
      (`_ZTI…`/`_ZTS…`) and vtables (`_ZTV…`) at the ELF level — which is
      exactly what lets a cross-DSO `catch` stop matching;
    - the `noexcept`-driven library version requirement from
      [case15](../reference/examples/case15_noexcept_change.md).

    The end-to-end *"will this exception escape?"* question is a genuine
    artifact-level limitation; see [Limitations](limitations.md) for the
    full catalog of what binary/header analysis cannot recover.

See also: [Part 4 — C++ ABI Specifics §5](abi-series/04-cpp-abi.md#5-noexcept-why-this-is-risk-not-a-hard-break)
for why a bare `noexcept` toggle classifies as risk rather than a hard
break, and [Modern C/C++ and Toolchain ABI Hazards](modern-cpp-toolchain-hazards.md)
for the `-fno-exceptions`/`-fno-rtti` build-flag row in context with its
siblings.
