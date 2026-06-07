# Part 7 — Designing for Stability

> **Series navigation:** [0. Product Contract](00-product-contract.md) ·
> [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> [3. Type Layout](03-type-layout.md) ·
> [4. C++ ABI](04-cpp-abi.md) ·
> [5. Linker & ELF](05-linker-elf.md) ·
> [6. Transitive Breaks](06-transitive-breaks.md) ·
> **7. Designing for Stability**

**What you'll learn on this page**

- The handful of design patterns that make a library *evolvable*, each with
  copy-pasteable code: **opaque handles, Pimpl, reserved padding, version
  scripts, visibility control, inline-namespace generations**.
- Five top-level rules that subsume every mechanism in the series.
- How to wire `abicheck` into CI as a release gate, and how to read its verdict.

This is the capstone. Each previous page ended with a "how to fix" box pointing
here; this page gives the full pattern.

---

## The one idea behind every pattern

Every break in this series came from the same root: **a fact the library
publishes (a size, an offset, a symbol name, a register choice, a vtable slot)
got baked into a consumer's binary and then changed.**

So every fix is a variation on a single move:

> **Stop publishing the fact.** If consumers can't see a struct's layout, you can
> change the layout. If they can't see a symbol's mangling, you can re-mangle it.
> If they only ever hold a pointer, the size on the other end is yours forever.

The patterns below are concrete ways to *not publish* a fact you'd otherwise be
committed to for the lifetime of a SONAME.

---

## Pattern 1 — Opaque handles (the strongest C firewall)

Expose only a pointer to an incomplete type. Define the struct in the `.c` file
so callers can never take `sizeof` or `offsetof`.

```c
// foo.h — the public header
typedef struct foo foo_t;        // incomplete: callers know only that foo_t exists

foo_t *foo_create(void);
void   foo_destroy(foo_t *);
int    foo_get_version(foo_t *);
void   foo_set_name(foo_t *, const char *);
```

```c
// foo.c — the implementation; layout lives here and ONLY here
struct foo {
    int   version;
    char  name[64];
    void *anything_you_want_to_add_later;   // free to change forever
};

foo_t *foo_create(void) { return calloc(1, sizeof(struct foo)); }
void   foo_destroy(foo_t *f) { free(f); }
int    foo_get_version(foo_t *f) { return f->version; }
```

**Why it works:** the only thing crossing the ABI boundary is `foo_t *` — pointer
size is fixed per ABI. You can add, remove, and reorder fields in `struct foo`
across releases and no consumer is affected. This is exactly how `FILE*`,
`sqlite3*`, and `git_repository*` stay stable for decades. It neutralizes the
entire [Part 3](03-type-layout.md) family in one move.

---

## Pattern 2 — Pimpl (the C++ equivalent)

The public class holds a single pointer to a privately-defined `Impl`. All state
lives in `Impl`, so `sizeof` of the public class never changes and no field
offset is ever visible.

```cpp
// widget.hpp — public
class Widget {
public:
    Widget();
    ~Widget();
    Widget(Widget&&) noexcept;
    Widget& operator=(Widget&&) noexcept;

    void resize(int w, int h);
    int  area() const;

private:
    struct Impl;                       // forward declaration only
    std::unique_ptr<Impl> d_;          // sizeof(Widget) == sizeof(void*), forever
};
```

```cpp
// widget.cpp — private
struct Widget::Impl {
    int w = 0, h = 0;
    std::string label;                 // safe to add: never crosses the boundary
};

Widget::Widget() : d_(std::make_unique<Impl>()) {}
Widget::~Widget() = default;           // defined here, where Impl is complete
void Widget::resize(int w, int h) { d_->w = w; d_->h = h; }
int  Widget::area() const { return d_->w * d_->h; }
```

**Why it works:** `sizeof(Widget)` is one pointer regardless of how `Impl` grows.
Qt enforces this across every public class, which is why Qt 5.x held ABI for
years through deep internal refactors. It also closes off
[trivial→non-trivial](04-cpp-abi.md) surprises, because the public class's
special members are declared once and pinned.

<!-- markdownlint-disable MD046 -->
!!! warning "Pimpl gotcha"
    `std::unique_ptr<Impl>` is **not** an automatic ABI firewall. Three things
    must hold:

    1. **Out-of-line special members.** Declare and *define in the `.cpp`* the
       destructor, move constructor, and move assignment (where `Impl` is
       complete). A defaulted destructor in the header forces the compiler to
       see `Impl`'s definition there — defeating the firewall.
    2. **No custom deleter / completeness leak.** The default `unique_ptr`
       deleter requires a complete type at the point of destruction; keep that
       point out-of-line.
    3. **Don't leak standard-library ABI.** If the public class still exposes
       `std::string`, `std::vector`, etc. by value in its interface, you've
       re-exported the stdlib's ABI (and its dual-ABI flips — see
       [case104](../../examples/case104_glibcxx_dual_abi_flip.md)) through the
       firewall. Keep standard-library types behind the `Impl` boundary too.
    4. **The wrapper itself is still a commitment.** Pimpl keeps `Impl`'s
       layout *out* of the public ABI — that's the whole point, so `Impl` is free
       to change. What stays frozen is `Widget`'s *own* size/alignment and the
       representation of its pointer member. Switching `std::unique_ptr<Impl>` to a
       raw pointer, a `shared_ptr`, or a different deleter, or adding an inline
       member function / changing a special member, is itself ABI-relevant
       ([case80](../../examples/case80_pimpl_shared_to_unique.md) shows a
       `shared_ptr`→`unique_ptr` flip detected as breaking).

!!! tip "Stricter variant: hide even the smart pointer"
    For the most conservative C++ ABI, hold a **raw** `Impl*` so the public
    class layout doesn't depend on the standard library's smart-pointer ABI at
    all — at the cost of writing lifetime management by hand:

    ```cpp
    class Widget {
    public:
        Widget();
        ~Widget();
        Widget(Widget&&) noexcept;
        Widget& operator=(Widget&&) noexcept;
        Widget(const Widget&) = delete;
        Widget& operator=(const Widget&) = delete;
    private:
        struct Impl;
        Impl* p_;   // layout depends on nothing but pointer size
    };
    ```
<!-- markdownlint-enable MD046 -->

---

## Pattern 3 — Reserved padding (evolve a value type in place)

When you *must* expose a struct by value (a C plain-old-data DTO), pre-reserve
space so you can add fields later without changing `sizeof` or moving offsets.

```c
// v1
typedef struct {
    int     priority;
    int     timeout_ms;
    uint64_t _reserved[6];     // 48 bytes held in reserve
} job_config_t;

// v2 — consumes two reserved slots; sizeof and all prior offsets UNCHANGED
typedef struct {
    int     priority;
    int     timeout_ms;
    int     max_retries;       // was _reserved[0]'s low word
    int     _pad;
    uint64_t _reserved[5];
} job_config_t;
```

**The contract you must document and consumers must honor:** zero-initialize the
whole struct and never read or write the reserved bytes. Linux `struct stat`,
glibc `pthread_attr_t`, and Wayland protocol structs all rely on this. The hazard
([Part 6, reserved-field misuse](06-transitive-breaks.md)) is that you cannot
*prove* a consumer obeyed it — so reserve generously and document loudly.

---

## Pattern 4 — Version scripts + visibility (own your export surface)

Compile with hidden visibility and enumerate exactly what you export. Everything
else stays internal and never becomes an ABI commitment.

```c
// foo_export.h
#define FOO_API __attribute__((visibility("default")))

FOO_API int  foo_compute(int);
/* internal helpers carry no macro → hidden under -fvisibility=hidden */
```

```text
# libfoo.map — the canonical list of intentional exports
LIBFOO_1.0 {
    global:
        foo_compute;
        foo_create;
        foo_destroy;
    local:
        *;            # everything else is hidden
};
```

```bash
gcc -fvisibility=hidden -shared -fPIC *.c \
    -Wl,--version-script=libfoo.map \
    -Wl,-soname,libfoo.so.1 -o libfoo.so.1
```

**Why it works:** `-fvisibility=hidden` plus the `local: *` rule means only the
names you listed enter `.dynsym` — closing off the accidental-leak hazard from
[Part 5](05-linker-elf.md). When you genuinely need a breaking change, add a new
node (`LIBFOO_2.0 { ... } LIBFOO_1.0;`) and keep the old symbols exported, so old
binaries keep resolving the old implementation.

### The same surface control on Windows and macOS

The *principle* — export exactly what you intend, version your identity — is
universal; only the spelling changes. (Loader-level details are in
[Part 5 §PE/COFF and Mach-O parallels](05-linker-elf.md#pecoff-and-mach-o-parallels).)

| Goal | Linux / ELF | Windows / PE | macOS / Mach-O |
|------|-------------|--------------|----------------|
| **Hide everything by default** | `-fvisibility=hidden` | nothing is exported unless marked | `-fvisibility=hidden` |
| **Mark a public export** | `__attribute__((visibility("default")))` | `__declspec(dllexport)` (and `dllimport` in consumers) | `__attribute__((visibility("default")))` |
| **Authoritative export list** | version script (`--version-script`) | a **`.def`** file (`EXPORTS`) — and pin **ordinals** so a rebuild can't renumber | `-exported_symbols_list file.txt` |
| **Library identity / epoch** | `-Wl,-soname,libfoo.so.1` | the **DLL file name** + its **import library** | **install name** + `-compatibility_version` / `-current_version` |
| **Generational ABI** (incompatible, must coexist) | new version node, keep old symbols | side-by-side DLL (new name) | **new install name / path** (e.g. `libfoo.2.dylib`) — the install name *is* the epoch¹ |

```text
; libfoo.def — stable Windows export surface (pin ordinals!)
LIBRARY libfoo
EXPORTS
    foo_compute   @1
    foo_create    @2
    foo_destroy   @3
```

```bash
# macOS — explicit export list + versioned install name
clang -dynamiclib -fvisibility=hidden *.c \
    -exported_symbols_list exports.txt \
    -install_name @rpath/libfoo.1.dylib \
    -compatibility_version 1.0 -current_version 1.2 -o libfoo.1.dylib
```

!!! warning "¹ macOS: `compatibility_version` is a floor, not an epoch"
    Clients select a dylib by its **install name**, and `compatibility_version`
    is only a *minimum* check — the loader rejects a runtime dylib whose
    compatibility version is *lower* than what the client recorded at link time,
    but a *higher* one still loads. So bumping `compatibility_version` under the
    **same install name** does **not** let an old and a new ABI coexist: an old
    client will happily load the new, incompatible dylib. For a breaking change
    where both must coexist, ship under a **new install name / path** (e.g.
    `@rpath/libfoo.2.dylib`) — that change of identity is the real epoch bump,
    the Mach-O analog of an ELF SONAME-major or a side-by-side Windows DLL.
    Reserve `compatibility_version` for *backward-compatible* additions within
    one generation.

!!! warning "Windows: the CRT allocation boundary"
    A DLL with its own statically-linked CRT must not hand out memory the caller
    `free`s (or vice versa) — `malloc`/`free` and `new`/`delete` must pair within
    the **same** module. Expose explicit `foo_create()`/`foo_destroy()` instead
    of letting callers `delete` your objects; this has no ELF equivalent but is a
    hard rule on Windows.

---

## Pattern 5 — Inline namespaces for generational C++ ABI

Wrap public declarations in an inline namespace. Source ignores it; the symbol
encodes it. When you need a breaking change, ship a new generation alongside the
old.

```cpp
namespace mylib {
inline namespace abi_v1 {
    class Codec { /* v1 layout & vtable */ };
    void process(Codec&);
}
}
// consumers write mylib::Codec / mylib::process — unaware of abi_v1
```

To ship an incompatible `Codec`, add `inline namespace abi_v2 { ... }` and demote
`abi_v1` to a *non-inline* namespace that still compiles for old TUs. New builds
bind `abi_v2` symbols; old binaries keep resolving `abi_v1`. This is libstdc++'s
`__cxx11` mechanism, used deliberately.

---

## Pattern 6 — Pure-virtual interface + factory

For polymorphic C++ APIs, never expose a concrete class with data members.
Expose an abstract interface and a C-linkage factory.

```cpp
// codec.hpp
struct ICodec {
    virtual ~ICodec() = default;
    virtual int  encode(const Frame&) = 0;
    virtual void reset() = 0;
};
extern "C" ICodec *codec_create();     // stable C symbol; hides the concrete type
```

**Why it works:** consumers hold only `ICodec*` and call through the vtable. The
concrete implementation class — its data members, its `sizeof`, its non-virtual
helpers — lives entirely in your `.so` and can change freely. The rule you
inherit from [Part 4](04-cpp-abi.md): never insert or reorder virtual methods on
`ICodec`. **Appending** is safe *only if your library owns every implementation
of `ICodec`.* The moment downstreams are allowed to derive from it — the usual
case for plugin or callback interfaces — even appending is breaking: an old
plugin's vtable was compiled with the old shape, so a host call into the new slot
dispatches past the end of that vtable. For a downstream-implementable interface,
treat *any* vtable change as a SONAME-bump-worthy break and version the interface
itself (a new `ICodec2`, or the inline-namespace generation pattern above).

---

## The five rules that subsume everything

1. **Treat public headers as ABI contracts.** Anything reachable from a public
   header — type layout, enum values, vtable shape, exported globals — is part of
   the binary contract whether or not you intended it to be.
2. **Govern release identity.** Use SONAME + symbol versioning + a
   `-fvisibility=hidden` export policy on every release; bump the SONAME *major*
   on any binary-incompatible change. abicheck surfaces the signals as
   `soname_bump_recommended`, `symbol_version_node_removed`,
   `symbol_moved_version_node`, and `version_script_missing`.
3. **Prefer opaque handles and Pimpl** over exposing mutable layouts, so the
   library — not the consumer — owns size, offsets, and the kind-tag of a type.
4. **Evolve additively, never in place.** Append new symbols, enum members, and
   struct fields (where no embedded `sizeof` assumption exists); ship breaking
   changes under a new inline-namespace or a new symbol rather than mutating an
   existing one.
5. **Gate every PR with abicheck in CI.** Dump the last released artifact and
   compare the candidate; block anything above `COMPATIBLE_WITH_RISK` that isn't
   paired with a deliberate SONAME bump.

---

## Wiring abicheck into CI

The minimal gate compares the candidate against the last released `.so`:

```bash
abicheck compare libfoo.so.old libfoo.so.new \
  --old-header include/old/foo.h \
  --new-header include/new/foo.h \
  --policy strict_abi
```

It exits non-zero on any 🔴 BREAKING or 🟠 API_BREAK finding. Add
`--suppress suppressions.yaml` to allowlist changes you've consciously accepted.

For a ready-to-paste GitHub Actions workflow that dumps the previous release and
fails the build on regressions, see the
[GitHub Action guide](../../user-guide/github-action.md). For the full CLI surface
and policy options, see [CLI Usage](../../user-guide/cli-usage.md) and
[Policy Profiles](../../user-guide/policies.md).

**Reading the verdict in CI:**

| Verdict | Exit behavior | What to do |
|---------|--------------|------------|
| ✅ `NO_CHANGE` / 🟢 `COMPATIBLE` | pass | merge |
| 🟡 `COMPATIBLE_WITH_RISK` | configurable | review the deployment risk (e.g. new GLIBC requirement, `noexcept` removal) |
| 🟠 `API_BREAK` | non-zero | intended? bump minor and document; else revert |
| 🔴 `BREAKING` | non-zero | bump SONAME major, or revert the change |

Ship your release builds **with debug info** (or feed abicheck the public
headers) — the [transitive breaks](06-transitive-breaks.md) in Part 6 are
invisible to any tool working from a stripped `.so` alone.

---

## You've finished the series

You now have the full picture: how a library becomes a running process
([Part 1](01-foundations.md)), the four families of break
([Parts 2–5](02-symbol-contracts.md)), the ones that hide from code review
([Part 6](06-transitive-breaks.md)), and the patterns that make a library
evolvable (this page).

**Where to go next:**

- [ABI Cheat Sheet](../abi-cheat-sheet.md) — the 2-minute scannable card of all
  of the above.
- [Examples & Case Encyclopedia](../../examples/index.md) — every mechanism here
  as a minimal, runnable v1/v2 reproduction with a real failure demo.
- [Verdicts](../verdicts.md) & [Exit Codes](../../reference/exit-codes.md) — the
  full classification and CI-integration semantics.
- [Change Kind Reference](../../reference/change-kinds.md) — the authoritative,
  always-current taxonomy of every detected change.

---

## Further reading (external, authoritative)

The canonical primary sources behind this series. When a claim here matters for a
real release decision, these are where to verify it:

- **[KDE — Binary Compatibility Issues With C++](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B)**
  — the most widely-cited practitioner checklist of what is and isn't binary-
  compatible in C++. Pairs directly with Parts 3–4.
- **[Itanium C++ ABI specification](https://itanium-cxx-abi.github.io/cxx-abi/abi.html)**
  — the authority for vtable layout, name mangling, and the trivially-copyable
  passing rules discussed in Part 4 (GCC/Clang on Linux/macOS/BSD).
- **[GCC / libstdc++ ABI policy and guidelines](https://gcc.gnu.org/onlinedocs/libstdc++/manual/abi.html)**
  — symbol versioning, the `_GLIBCXX_USE_CXX11_ABI` dual-ABI, and library
  versioning practice behind Parts 5–6.
- **[Ulrich Drepper — *How To Write Shared Libraries*](https://www.akkadia.org/drepper/dsohowto.pdf)**
  — the definitive treatment of ELF symbol resolution, visibility, versioning,
  and TLS that underpins Parts 1, 2, and 5.
- **Martin Reddy — *C++ API Design* (Morgan Kaufmann, 2011)** — book-length
  treatment of the opaque-handle / Pimpl / versioning patterns in this page.
- **[“20 ABI-breaking changes every C++ developer should know”](https://www.acodersjourney.com/20-abi-breaking-changes/)**
  — a concise lay summary (MSVC/DLL-flavored); this series is a strict superset
  of its checklist and additionally covers enums, unions, bitfields, alignment,
  TLS, and transitive/dependency leaks.

*Back to the [series overview](../abi-api-handling.md).*
