# Case 167: Base Class Became Virtual (`: public Device` → `: public virtual Device`)

**Category:** Class Layout / Virtual Inheritance | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

v2 turns `Stream`'s base into a **virtual base** — the classic preparation
for a diamond hierarchy ("a future `DuplexStream : InStream, OutStream`
must share one `Device`"). One keyword, and the entire object model is
rewritten: the `Device` subobject moves from offset 0 to the *end* of the
object, `bytes_moved` shifts from offset 16 to offset 8, `sizeof(Stream)`
grows from 24 to 32 bytes, and `Stream` gains a VTT plus vbase-offset
machinery to locate its own base at runtime. Source compatibility is
perfect — no call site changes, everything recompiles cleanly — but every
consumer binary compiled against v1 reads fields at dead offsets.
Recompilation is mandatory.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `class Stream : public Device { ... long bytes_moved; };` | `class Stream : public virtual Device { ... long bytes_moved; };` |
| `Device` @ offset 0, `bytes_moved` @ offset 16, `sizeof(Stream)=24` | `Device` @ offset 16 (end), `bytes_moved` @ offset 8, `sizeof(Stream)=32` |

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so
abicheck compare libv1.so libv2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_size_changed: Size changed: Stream (192 -> 256 bits)
  > Old code allocates or copies the type with the old size;
    heap/stack corruption, out-of-bounds access.
- type_field_offset_changed: Field offset changed: Stream::bytes_moved (128 -> 64 bits)
  > Old code reads/writes fields at stale offsets; silent data corruption.
- type_base_changed: Base classes changed: Stream (['Device'] -> [])
  > Base class layout change shifts derived member offsets and vtable
    pointers; this-pointer arithmetic breaks.
- vtable_slot_count_changed: Vtable for 'Stream' changed size: 40 -> 104 bytes
- rtti_inheritance_changed: RTTI typeinfo for 'Stream' changed size: 24 -> 40 bytes
  (single base (__si_class_type_info) -> 2 bases (__vmi_class_type_info))
- vtable_thunk_set_changed: thunk added for Stream::~Stream() and Stream::kind() const
- base_class_virtual_changed: Base class virtual inheritance changed:
  Stream — became virtual: ['Device']

Deployment Risk Changes:
- imported_symbol_added: New imported symbol: vtable for
  __cxxabiv1::__vmi_class_type_info@CXXABI_1.3
```

`base_class_virtual_changed` is the fact that names the actual source
change; the layout/vtable/RTTI findings are the fallout — visible even
without headers.

## Minimum evidence

`min_evidence: L1` — DWARF's `DW_AT_virtuality` on the inheritance DIE
records whether `Device` is a virtual base for both versions, and DWARF's
struct-layout info corroborates the size/offset fallout. No public headers
are required; the binary-only `_ZTV`/`_ZTI` layer independently corroborates
the same break even on a stripped binary.

## Why abicheck catches it

DWARF records each inheritance relationship's virtuality attribute directly;
abicheck compares the base-class lists (virtual vs. non-virtual) for `Stream`
between the two snapshots and reports the move as
`base_class_virtual_changed`. The size, offset, vtable-slot-count, and RTTI
findings are independently derived from the same DWARF/ELF evidence and
corroborate the single root cause from different angles.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile app against v1 (direct field access at offset 16),
link to v2 `.so` without recompiling.

```bash
# Build old library + app
g++ -shared -fPIC -g v1.cpp -o liblib.so
g++ -g app.cpp -L. -llib -Wl,-rpath,. -o app
./app
# → bytes via library = 4096 (expected 4096)
# → bytes direct      = 4096 (expected 4096)

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o liblib.so
./app
# → bytes via library = 4096 (expected 4096)
# → bytes direct      = 140027503222112 (expected 4096)
# → CORRUPTION: direct access used the v1 (non-virtual base) offset
#   and read the virtual-base machinery!
```

**Why CRITICAL:** the app reads `s->bytes_moved` at v1's offset 16 — where
v2 now stores the virtual `Device` base's vtable pointer — and interprets
an address as a byte count. Writes through the same offset would corrupt
the vtable pointer and crash on the next virtual call.

## Safe redesign

1. **Design the hierarchy up front**: if a class may ever sit in a diamond,
   make the base virtual in the first released version.
2. **Prefer composition or interfaces** (pure-virtual, data-free bases) at
   ABI boundaries — data-free virtual bases still change layout, but
   hierarchies that never need a shared-state base avoid the diamond
   entirely.
3. **SONAME bump** when the refactor is unavoidable — this cannot be
   shipped compatibly.

**Real-world example:** the iostreams hierarchy is the canonical shape:
`std::basic_ios` is a *virtual* base of `basic_istream`/`basic_ostream`
precisely so that `basic_iostream` contains a single copy — a decision that
had to be made before the ABI froze, because retrofitting it would have
rewritten the layout of every stream object. The KDE binary-compatibility
policy forbids "changing the class hierarchy in any way" for exactly this
reason.

## References

- [Itanium C++ ABI §2.4/§2.6 — virtual base offsets, VTT and construction vtables](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#vtable)
- [KDE ABI Policy — "you cannot change the class hierarchy"](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B)
