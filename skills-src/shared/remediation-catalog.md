---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - change-kinds
---

# Remediation catalogue

Patterns for making a change without breaking installed consumers, and for
repairing a break already detected. The exhaustive catalogue of what abicheck
detects is [the change kinds reference](../../docs/reference/change-kinds.md);
this file is about what to *do* about each family.

Every remediation ends the same way: re-run the same check that flagged the
problem and report the new result
([safety-invariants.md](safety-invariants.md), closing rule).

## By break family

| Break | Remediation |
|---|---|
| Removed export | Keep the old symbol as a thin forwarding shim; deprecate rather than delete. On ELF, a versioned symbol lets old and new coexist. |
| Changed function signature | Add a new, differently-named entry point and keep the old one delegating to it. Never mutate a shipped signature in place. An *overload* of the existing name also preserves the ABI, but can make an existing call ambiguous on rebuild (implicit conversions, `&f`), so prefer a new name when source compatibility matters. |
| Added parameter | Same — and a default does not rescue it. The mangled name changes, so it is ABI-breaking regardless; it is source-compatible only for ordinary calls that omit the new argument, and still breaks source that takes the function's address, uses the old function-pointer type, or overrides it as a virtual. |
| Struct/class size or layout change | Do not change a public aggregate. Move state behind an opaque pointer (pImpl), or spend a previously reserved field. |
| New data member | Only safe in a type consumers never allocate, embed, or derive from. Otherwise, pImpl or a new versioned type. |
| New virtual function | Appending to the vtable breaks any consumer that derives from the class or was compiled against the old vtable size. Prefer a non-virtual API plus internal dispatch, or a new interface version. |
| Reordered virtual functions | Never. Append-only at best; usually a new interface. |
| Changed enum value | Enum values reaching a public signature or a struct field are ABI. Never renumber. Appending is safe only when the underlying type is fixed (`: int` / `enum class`) or the new value fits the old range — otherwise the implementation may pick a wider underlying type, changing `sizeof(E)` and the layout of everything containing it. Fix the underlying type explicitly, ideally from the start. |
| Changed inline function or template | Its body is baked into consumers. Treat a semantic change as breaking unless the old behaviour is preserved for old callers. |
| Narrowed visibility | Re-export, or accept the break and version the SONAME. |
| Raised runtime/symbol-version floor | **Visible to `compare`** as `runtime_floor_raised` (a risk on its own; promoted to a break against declared floors). Read it from the report — do not assume runtime is out of scope. Document the new floor, or lower it. The wider dependency graph is `abicheck deps compare`'s job. |

## Design patterns that avoid the problem

- **pImpl / opaque handle.** All state behind a pointer to an
  implementation-private type. The public type's size never changes. The cost
  is an allocation and an indirection.
- **Reserved fields / slots.** Deliberate padding (`void* reserved[4]`) in a
  public aggregate, spent later without changing size. Only works if reserved
  from the start.
- **Versioned interfaces.** `IFoo2` alongside `IFoo`, or symbol versioning
  (`.symver`, version scripts). Old consumers keep the old contract.
- **Capability negotiation.** For plugin/host boundaries, an explicit
  version-and-capability handshake beats an implicit struct layout.
- **Additive-only free functions.** A C-style API of free functions over
  opaque handles is far cheaper to evolve than a C++ class exposed by value.
- **Deprecation lifecycle.** Announce → mark deprecated → keep working for at
  least one supported release → remove only at a major/SONAME bump.

## When there is no compatible path

Some changes cannot be made compatibly. Say so plainly, and describe the cost
of each honest option:

1. **Ship it as a major/SONAME bump.** Correct, but consumers must rebuild.
2. **Keep the old surface alongside the new one.** Compatible, but doubles
   the surface you maintain.
3. **Do not make the change.**

Never present a fourth option consisting of relaxing the check
([policies-and-suppressions.md](policies-and-suppressions.md)).

## Verifying a remediation

1. Apply the change (with explicit confirmation before editing anything —
   [safety-invariants.md](safety-invariants.md) item 8).
2. Rebuild both sides under the same profile
   ([compiler-and-build-profiles.md](compiler-and-build-profiles.md)).
3. Re-run the identical `compare` invocation.
4. Report the new verdict, the findings that cleared, and any that remain.
