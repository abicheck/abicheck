---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - exception-unwinding-abi
depends_on:
  - abicheck/diff_symbols.py
  - abicheck/diff_platform.py
  - abicheck/buildsource/build_diff.py
  - abicheck/elf_metadata.py
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
`catch`'s `type_info`. This is the same kind of RTTI/`type_info` object that
backs the vtable in
[Part 4 §1](abi-series/04-cpp-abi.md#1-vtables-and-virtual-methods) — but not
the same one `-fno-rtti` strips (see the
[modern hazards table](modern-cpp-toolchain-hazards.md)): `-fno-rtti`
disables `typeid`/`dynamic_cast` support, but GCC/Clang still emit the
`type_info` a thrown/caught type needs regardless of that flag, since the
exception-handling ABI requires it independently. A DSO compiled with
`-fno-rtti` can still throw a type that an RTTI-enabled consumer catches
correctly.

Matching *ideally* compares `type_info` by pointer identity — but each DSO
can emit its **own copy** of a type's `type_info`. On GCC/libstdc++'s default
configuration, `type_info::operator==` falls back to comparing the mangled
**type-name string** (`strcmp` on `name()`) whenever the pointers differ, and
that string content is identical in both DSOs' `.rodata` regardless of
whether the linker actually merged the two copies — so a cross-DSO `catch`
on this runtime typically still matches correctly even when the type's
`type_info` has **hidden visibility**, confirmed empirically (GCC, two
private `_ZTI` copies, catch still matches via the string fallback). The
real trap is narrower than "hidden visibility breaks the catch":

> A `catch` genuinely fails to match when there is **no string fallback to
> fall back to** — a runtime/toolchain configuration that compares
> `type_info` by pointer identity alone (some non-GNU C++ runtimes, or a
> GNU target configured to *assume* `type_info` names are already merged
> into one unique instance — `__GXX_MERGED_TYPEINFO_NAMES`, not the common
> default on typical Linux/glibc targets — which skips the string fallback
> because it isn't supposed to need it; hidden visibility can then violate
> that very assumption without the runtime detecting it), or when the
> thrown type's RTTI symbol is genuinely stripped from the binary entirely
> (not merely hidden, and not merely `-fno-rtti` — see above, that flag
> alone doesn't remove exception `type_info`). In either case the exception
> unwinds *past* the intended handler. What happens next is the ordinary
> unwinding rule, not an automatic abort: the search continues up the chain,
> so an outer `catch (...)` (or another matching handler) still takes it, and
> `std::terminate()` fires only if no handler anywhere matches.

**Default visibility on a thrown type's `type_info`** (in practice: an
export/default-visibility annotation on the class itself, or an explicitly
exported RTTI symbol — a key function is *not* sufficient under
`-fvisibility=hidden`, since it only decides which translation unit owns
the vtable/RTTI emission, not what visibility those symbols get — see
[Part 5 §Symbol visibility](abi-series/05-linker-elf.md#2-symbol-visibility))
is still the right default to design for: it lets the dynamic linker merge
the copies into one true pointer identity, which is faster and more robust
than relying on every runtime having (and correctly implementing) the
string-comparison fallback — but on the common GCC/libstdc++ case, hidden
visibility alone is a robustness/performance concern, not the guaranteed
catch failure the string fallback exists specifically to prevent.

## Mixing `-fexceptions` and `-fno-exceptions`, and changing throw specs

`-fno-exceptions` tells the compiler *no exception will ever pass through
this code*, so it omits landing pads and cleanup handling for that
translation unit — but it does not reliably omit `.eh_frame` itself: GCC
commonly still emits call-frame information for such code (other consumers,
like debuggers and plain backtraces, need it), so an unwinder can usually
still walk *through* a `-fno-exceptions` frame and reach a handler further
up the call chain. What that frame genuinely lacks is the
`.gcc_except_table` (LSDA) cleanup metadata, so **local destructors in that
frame are skipped** on the way past — silent resource leaks/UB in that one
frame, not necessarily an immediate `std::terminate()`. `std::terminate()`
still fires the same way it always does when unwinding finds *no* handler
anywhere in the chain, which a `-fno-exceptions` frame makes no more or less
likely on its own. Building half a call chain `-fno-exceptions` and half
`-fexceptions` is therefore an ABI decision, not just an optimization flag —
the risk is silently-skipped cleanup along the mixed boundary, exact
behavior target/toolchain-dependent, not a guaranteed hard stop — and
flipping a shipped library between the two changes what every caller can
assume.

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
      (`_ZTI…`/`_ZTS…`) and vtables (`_ZTV…`) at the ELF level. Read these
      as *risk*, not as a proven catch failure. What abicheck observes is
      the **export table**, and a hidden/internal symbol is excluded from it
      (the `.dynsym` parse drops `STV_HIDDEN`/`STV_INTERNAL` entries),
      so a default→hidden visibility flip presents identically to a genuine
      removal — while the local RTTI object and its type-name string are
      still there, and GNU libstdc++'s name fallback still matches. A
      cross-DSO `catch` only genuinely stops matching in either of the two
      cases the section above sets out — a pointer-identity-only runtime with
      unmerged copies, *or* RTTI that is genuinely unavailable — and even
      then the exception continues unwinding to whatever handler does match;
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

---

**Ladder:** ← [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md) · Tier 2 · Mechanics · [Part 5 — ELF & Linker-Level Concerns](abi-series/05-linker-elf.md) →
