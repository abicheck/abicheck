---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - public-surface
depends_on:
  - abicheck/surface.py
lifecycle: active
generated: false
---

# What Is Part of Your ABI Surface?

The single most common ABI-review mistake is judging a change by *where the
code lives* ("it's in an internal helper, so it's safe") instead of by *what
crosses the compile / link / load boundary*. This page is the deep-dive on
that boundary: which of your library's symbols, types, constants, layouts, and
inline code existing consumers are actually bound to — and the checklist to
run before calling an internal change safe.

> **Where this fits.** This is a deep-dive of the
> [ABI/API handling series](abi-api-handling.md);
> [Part 6 — Transitive Breaks](abi-series/06-transitive-breaks.md) covers the
> mechanisms in detail, and
> [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md)
> covers the patterns that *shrink* the surface on purpose.

## Runtime calls are not the same as ABI dependencies

A public entry point may call a long chain of private helpers at runtime. That
runtime call graph is **not** automatically the consumer's ABI contract. Existing
binaries are bound only to the symbols, types, constants, layouts, and inline
code that cross the **compile / link / load boundary**: what appears in installed
public headers, what the consumer object directly references, and what the loader
must resolve.

```text
Safe runtime call chain:
app -> public_func
       public_func -> hidden internal_helper

Consumer binary depends on public_func only. internal_helper can change because
it is not exported, not referenced by public headers, and not part of public
layout or inline code.
```

The same private helper becomes an ABI dependency if the boundary shifts:

```text
Unsafe link-time dependency:
inline public_func in an installed header -> detail::internal_helper

The consumer object now directly references detail::internal_helper. Removing,
renaming, hiding, or changing that helper can break already-built consumers.
```

Private types follow the same rule. A helper struct is safely private while it is
fully hidden behind an opaque pointer or implementation file, but not when the
public header exposes it by value:

```text
Unsafe compile-time layout dependency:
public header exposes InternalType by value

The consumer bakes sizeof(InternalType), alignment, field offsets, base-class
layout, and calling-convention facts into its own object code.
```

## The private-change safety checklist

Use this checklist before calling an internal change ABI-safe. A private change
is safe only when **all** of these remain true:

- the private symbol is not exported or otherwise load-resolvable by consumers;
- public inline, template, `constexpr`, or macro bodies do not reference it;
- it is not part of any public struct/class layout, base class, field, parameter,
  return value, exception specification, allocator/deallocator rule, or calling
  convention;
- it is absent from installed public headers except behind an opaque declaration
  that reveals no size, members, bases, or required helper symbols;
- no plugin, callback, subclassing, serialization, or user-extension model
  promises that consumers may provide or observe the changed detail;
- the public behavior contract remains compatible, even if the binary boundary is
  intact.

This distinction is why [Part 5](abi-series/05-linker-elf.md) treats leaked
private exports as dangerous, [Part 4](abi-series/04-cpp-abi.md) treats
inline/template bodies as part of the contract, and
[Part 6](abi-series/06-transitive-breaks.md) treats exposed dependency types as
transitive ABI.

## Checking the boundary with abicheck

Two abicheck features map directly onto this page:

- **Public-surface scoping** — supply the public headers
  (`-H include/ --public-header-dir include/`) and abicheck itself applies the
  boundary: internal-type churn is scoped out
  ([case118](../examples/case118_internal_struct_field_added_scoped.md)–[120](../examples/case120_internal_struct_reordered_scoped.md)),
  public changes stay. Without headers, every exported symbol is treated as
  contract — the safe over-approximation.
- **Audit mode** (`abicheck scan --audit`) — a single-build hygiene lint for a
  *leaking* boundary: accidental exports
  ([case143](../examples/case143_audit_accidental_export.md)), private-header
  leaks ([case144](../examples/case144_audit_private_header_leak.md)),
  unversioned exports
  ([case145](../examples/case145_audit_unversioned_export.md)), and exported
  RTTI for internal types
  ([case146](../examples/case146_audit_rtti_for_internal.md)). See
  [Source-Scan Depth § single-build audit](../user-guide/scan-levels.md#single-build-audit-no-against).

The design patterns that keep the surface small — opaque handles, Pimpl,
version scripts, `-fvisibility=hidden` + explicit exports — are the subject of
[Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md).
