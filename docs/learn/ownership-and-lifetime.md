---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - ownership-and-lifetime
depends_on:
  - abicheck/diff_symbols.py
lifecycle: active
generated: false
---

# Ownership & Lifetime Contracts

A pointer, handle, or reference crossing an API boundary carries an implicit
contract — **who allocated it, who may free it, how long it stays valid,
and who is responsible for its destruction.** A raw C pointer encodes none
of it; a C++ smart pointer (`std::unique_ptr<T>`/`std::shared_ptr<T>`) can
encode *part* of it — ownership transfer, in particular — in the type
itself, as the "Designing for it" section below recommends, but even that
only goes so far: an allocator/CRT mismatch across the boundary, or a
lifetime shorter than the pointer's own scope suggests, is invisible to
the type either way. No ABI/API checker can read the remainder off a
signature. This page names that
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
  [plugin/callback contracts](../use/plugin-systems.md)) narrows an ABI/
  symbol-availability comparison to one specific plugin/host pair — two
  different ways: `--used-by` derives what's required from a supplied
  consumer binary's *actual* imports, while `--required-symbol` checks an
  explicit, hand-specified entrypoint list (the host's own `dlopen`/`dlsym`
  contract, which no import table records). Either is real and useful for
  that narrower question — but neither executes the registration/unregister/
  shutdown paths, so a clean
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
  possible: `T&`/`const T&`/a non-owning `std::span`/`std::string_view` for
  borrowed access, a library-provided RAII wrapper (a "handle" class) around
  an opaque C pointer for a stable ABI boundary that still encodes ownership
  in C++ consumer code. For transferred ownership, `std::unique_ptr<T>`'s and
  `std::shared_ptr<T>`'s *default* deleters behave differently across a
  CRT/allocator-mismatch boundary, and the two must not be treated the same
  way: `unique_ptr<T>`'s deleter is baked into its own type, so whichever code
  instantiates the destructor — typically the consumer, wherever the
  `unique_ptr` variable goes out of scope — runs `delete` there; a
  default-deleted `unique_ptr<T>` returned from the library therefore runs
  the *consumer's* `delete` on memory the *library* allocated, exactly the
  heap corruption described above. `shared_ptr<T>`'s deleter, by contrast, is
  *type-erased into its control block* at construction time, so a
  `shared_ptr` the library constructs keeps calling back into the library's
  own deletion path even when the consumer releases the last reference —
  the default deleter is safe on this specific hazard. `shared_ptr` still
  needs a compatible standard-library ABI on both sides (the control block's
  own layout) and the same careful cross-module lifetime discipline this
  whole page is about, just not a custom deleter for *this* reason. Across an
  allocator/CRT-mismatch boundary, give `unique_ptr<T>` a custom deleter that
  calls an exported library-side destroy function, or use the opaque-handle
  RAII wrapper instead.
- **Never change an ownership contract silently**, even when the signature
  is unaffected — this is exactly the class of change
  [Behavioral & Semantic Compatibility](behavioral-compatibility.md) says
  static analysis structurally cannot flag for you. If the old contract was
  promised, an *incompatible* change to it (a borrow becoming a take, a
  return value's lifetime shrinking) needs the same major version bump any
  other incompatible public change needs — see
  [Product Contract §4](abi-series/00-product-contract.md#4-semantic-versioning-turning-the-promise-into-a-number)
  — not just a changelog note; only a change that keeps the promised
  contract intact can go out as a patch or minor release.
- **For plugin/callback boundaries**, document the ownership contract
  (who owns `user_data`, who frees it, and when) directly alongside the
  entrypoint declarations `--required-symbol`/`--required-symbols` checks
  — see [Plugin Systems](../use/plugin-systems.md). abicheck itself has no
  ownership-aware check to run here (`--required-symbol` verifies only
  entrypoint *availability*, not the contract around it, per "What abicheck
  can and cannot see" above) — the point is to put the contract somewhere a
  reviewer changing that boundary will actually see it, not to have it
  automatically enforced.

See also: [Behavioral & Semantic Compatibility](behavioral-compatibility.md)
for the broader category this belongs to, and
[Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md)
for the pimpl/opaque-handle patterns that make an ownership contract part of
a stable, checkable ABI boundary.

---

**Ladder:** ← [Data, Wire & Storage Compatibility](data-wire-compatibility.md) · Tier 8 · Beyond static ABI · [Concurrency & Initialization Contracts](concurrency-and-initialization.md) →
