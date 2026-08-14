---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - ownership-and-lifetime
depends_on:
  - abicheck/diff_symbols.py
lifecycle: active
generated: false
---

# Ownership & Lifetime Contracts

A pointer, handle, or reference crossing an API boundary carries an implicit
contract that no C or C++ type system enforces and no ABI/API checker can
read off a signature: **who allocated it, who may free it, how long it stays
valid, and who is responsible for its destruction.** This page names that
contract explicitly — it comes up constantly in
[Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md)'s
pimpl and opaque-handle patterns and in
[plugin/callback contracts](../use/plugin-systems.md), but deserves its own
treatment because getting it wrong is a distinct failure mode from every
other compatibility dimension on this site.

## Why this is invisible to a signature

`Widget* create_widget();` and `void destroy_widget(Widget*);` fully
describe the *types* involved and nothing about the *contract*: does the
caller own the returned pointer and must call `destroy_widget`? Does the
library retain ownership and the pointer is only valid until the next call?
Is `destroy_widget` idempotent, or is calling it twice undefined behavior?
None of this is expressible in the C signature, and in C++ even RAII
wrappers only push the same questions one level down — a
`std::unique_ptr<Widget>` returned across a DLL/shared-library boundary
still needs both sides compiled with the *same* allocator, the same
standard-library ABI, and often the same runtime (see the CRT-mismatch
discussion below), or destruction from the "wrong" side corrupts the heap.

An ownership/lifetime break is therefore a **behavioral** break in the sense
[Behavioral & Semantic Compatibility](behavioral-compatibility.md) describes
— it changes what happens when the same code runs — but it deserves its own
page because the *mechanism* is specific enough to have its own vocabulary,
its own recurring bug shapes, and its own design patterns.

## Recurring failure shapes

- **Allocator/CRT mismatch.** On Windows especially (see
  [Dependency & Runtime Floors](dependency-floors.md) for the platform
  background), each DLL can be linked against a different C runtime; an
  object `new`'d in one runtime's heap and `delete`'d through another's
  `operator delete` is heap corruption, not a clean crash. A library that
  starts allocating in a different way — switching allocators, changing
  which side owns allocation — changes this contract silently.
- **Ownership transfer direction changes.** A function that used to *borrow*
  a pointer (caller retains ownership, library only reads it during the
  call) starts *taking* ownership (library will free it) or vice versa. The
  signature can be identical; only the documented contract changed.
  Consumers written against the old contract now either leak or
  double-free.
- **Lifetime-of-return-value changes.** A function that used to return a
  pointer valid until the *next call* on the same handle now returns one
  valid only until the *end of the current call*, or the reverse. Any
  consumer holding the pointer across the boundary they used to be allowed
  to cross now has a dangling reference.
- **Callback/context ownership.** In a plugin or callback-registration API,
  who owns the `void* user_data` (or equivalent) passed at registration
  time, and who is responsible for freeing it when the callback is
  unregistered or the host shuts down, is a contract that exists nowhere in
  the function pointer's type. `compare --used-by`/`--required-symbol` (see
  [plugin/callback contracts](../use/plugin-systems.md)) scopes an ABI/
  symbol-availability comparison to one specific plugin/host pair's actual
  imports — real and useful for that narrower question — but it does not
  execute the registration/unregister/shutdown paths, so a clean
  consumer-scoped result says nothing about *this* contract; only a runtime
  test that actually exercises those paths does — the same execution-based
  evidence [Behavioral & Semantic Compatibility](behavioral-compatibility.md)
  recommends for the wider category this belongs to.
- **Reentrancy and self-deletion.** A callback that used to be safe to call
  from within another call to the same library now isn't (or the reverse) —
  a lifetime contract about the *call stack*, not just about pointers.

## What abicheck can and cannot see

Nothing here has a stable syntactic signal an artifact comparison can key
on. A signature, its mangled name, and its parameter/return types can be
byte-identical across an ownership-contract change — this is definitionally
the same shape as a
[behavioral compatibility](behavioral-compatibility.md) break: the contract
lives in documentation and convention, not in anything the compiler encodes.
Two indirect signals are sometimes present, though neither is reliable
enough to treat as proof:

- A `[[nodiscard]]`, `_Frees_ptr_` (SAL), or similar annotation change is a
  real, if rare, structural signal — if your codebase uses these
  consistently, a change to one is at least *visible* to a header diff, even
  though abicheck has no dedicated detector for ownership annotations
  specifically today.
- A parameter type changing from a raw pointer to `std::unique_ptr<T>` (or
  the reverse) is visible as an ordinary signature/mangling change — but it
  only catches the subset of ownership-contract changes that happen to
  coincide with a type change; a raw-pointer-to-raw-pointer contract
  reversal (borrow → take ownership) is completely invisible.

## Designing for it

- **State the ownership contract in the header, next to the declaration**,
  not only in separate prose documentation — a comment directly on the
  function is far more likely to be kept in sync with the implementation and
  seen by the next person to touch it.
- **Prefer types that make the contract part of the signature** where
  possible: `std::unique_ptr<T>`/`std::shared_ptr<T>` for transferred
  ownership, `T&`/`const T&`/a non-owning `std::span`/`std::string_view` for
  borrowed access, a library-provided RAII wrapper (a "handle" class) around
  an opaque C pointer for a stable ABI boundary that still encodes ownership
  in C++ consumer code.
- **Never change an ownership contract without a version bump and explicit
  changelog note**, even when the signature is unaffected — this is exactly
  the class of change [Behavioral & Semantic Compatibility](behavioral-compatibility.md)
  says static analysis structurally cannot flag for you.
- **For plugin/callback boundaries**, write the ownership contract into the
  plugin manifest alongside the ABI contract — see
  [Plugin Systems](../use/plugin-systems.md) — so it's checked as part of
  the same review as everything else about that boundary, not left as an
  unwritten assumption.

See also: [Behavioral & Semantic Compatibility](behavioral-compatibility.md)
for the broader category this belongs to, and
[Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md)
for the pimpl/opaque-handle patterns that make an ownership contract part of
a stable, checkable ABI boundary.
