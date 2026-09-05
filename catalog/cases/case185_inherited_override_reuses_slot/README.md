# Case 185: Inherited override reuses the base's vtable slot

**Category:** Addition | **Verdict:** ✅ COMPATIBLE (exit 0)

## What changes

`Base::paint(int)` is a virtual method. In v1, `Derived` does not override
it — calls dispatch straight through to `Base::paint()`. In v2, `Derived`
adds `int paint(int x) override;` with the **exact same signature** as
`Base::paint(int)`. `Derived` also keeps its unrelated, non-virtual
`helper()` method.

## Why this is not a new vtable slot

A naive "did a new virtual method appear on this class?" scan would see
`Derived::paint` show up where it didn't exist before and flag it as
`VIRTUAL_METHOD_ADDED` — normally a `BREAKING` finding, since inserting a
slot shifts every subsequent entry in the vtable and desyncs old callers.

But overriding an *already-virtual* base method with a matching signature
doesn't insert anything — it replaces the function pointer **at the same
slot index** `Base::paint` already occupied. `Derived`'s vtable is the same
size before and after:

```
vtable for Derived: 40 bytes (5 pointers: offset-to-top, RTTI, ~Derived,
                    ~Derived (deleting), paint) -- unchanged in both versions
```

`abicheck/diff_cxx_rules.py` builds `old_virtual_signatures()` — a per-class
set of `leaf-name(params)cv-ref` identities — from the *old* snapshot's
class hierarchy, walking transitive bases. When `virtual_method_addition()`
sees `Derived::paint(int)` in the new snapshot, it checks whether any base
in `Derived`'s inheritance chain already had a virtual with that exact
signature key. Here `Base::paint(int)` matches, so the new function is
recognized as an override reusing an inherited slot — no
`VIRTUAL_METHOD_ADDED`/vtable-layout finding is emitted for it at all. What
*is* reported is a plain, additive `func_added` for the newly-materialized
`Derived::paint(int)` symbol (it didn't have its own mangled symbol before,
since calls went through `Base::paint` directly) — a `COMPATIBLE` addition,
not a break.

```bash
abicheck compare libv1.so libv2.so --header old=v1.hpp --header new=v2.hpp
# verdict: COMPATIBLE (exit 0)
# func_added: New public function: paint
```

## Negative twin: same name, different signature *does* add a slot

If `Derived` instead declared `virtual int paint(double x);` — same method
name, but a signature that does **not** match any inherited virtual — there
is no existing slot to reuse. This genuinely grows `Derived`'s vtable by one
entry (40 → 48 bytes) and is correctly reported as `BREAKING`:

```
vtable_slot_count_changed: Vtable for 'Derived' changed size: 40 -> 48 bytes
virtual_method_added: New virtual method added to existing class Derived: paint
```

See `tests/test_kde_compat_detectors.py::test_inherited_override_is_not_virtual_method_added`
and `::test_same_name_different_signature_virtual_is_new_slot` for the
unit-level pair this compiled example mirrors.

## Two detectors, one exemption

`diff_types.py`'s `_diff_type_vtable()` independently compares each class's
list of vtable entries. Left on its own, it would just see `Derived`'s
vtable entry list textually change (`Base::paint`'s slot now names
`Derived::paint`), even though its length and order are identical, and
unconditionally emit `type_vtable_changed` — disagreeing with
`virtual_method_addition()`'s slot-reuse exemption above and reporting
`BREAKING` for a case that is genuinely `COMPATIBLE`.

`_diff_type_vtable()` calls `diff_cxx_rules.vtable_slot_is_override_reuse()`
— the same signature-key comparison `virtual_method_addition()` uses — to
recognize when a differing vtable slot is exactly this override-reuse
relationship, and withholds `TYPE_VTABLE_CHANGED` for it too. The two
detectors now agree:

```bash
abicheck compare libv1.so libv2.so --header old=v1.hpp --header new=v2.hpp
# verdict: COMPATIBLE
# func_added: New public function: paint
```

Reaching that agreement at the castxml layer, on real compiled binaries,
also needed a fix one level lower: `dumper_castxml.py`'s `_build_vtable()`
previously deduplicated an override against the base slot it reuses only via
castxml's `vtable_index` attribute. Not every castxml/Clang build emits that
attribute, and without it the reused slot was never deduplicated at all —
`Derived`'s reconstructed vtable listed *both* `Base::paint` and
`Derived::paint`, one entry longer than the real vtable, which
`vtable_slot_is_override_reuse()` (a same-length, positional check) cannot
see through. `_build_vtable()` now falls back to castxml's `overrides`
attribute (resolved through any multi-level override chain) to collapse the
reused slot in place when `vtable_index` is absent.

## How to reproduce

```bash
cmake -S examples -B /tmp/abicheck-examples-build
cmake --build /tmp/abicheck-examples-build --target \
    case185_inherited_override_reuses_slot_v1 case185_inherited_override_reuses_slot_v2

python3 -m abicheck.cli compare \
    /tmp/abicheck-examples-build/case185_inherited_override_reuses_slot/libv1.so \
    /tmp/abicheck-examples-build/case185_inherited_override_reuses_slot/libv2.so \
    --header old=v1.hpp --header new=v2.hpp --lang c++
```
