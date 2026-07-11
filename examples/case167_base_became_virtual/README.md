# Case 167: Base Class Became Virtual (`: public Device` → `: public virtual Device`)

**Category:** Class Layout / Virtual Inheritance | **Verdict:** BREAKING

## What breaks

v2 turns `Stream`'s base into a **virtual base** — the classic preparation for
a diamond hierarchy ("a future `DuplexStream : InStream, OutStream` must share
one `Device`"). One keyword, and the entire object model is rewritten:

| | v1 (non-virtual base) | v2 (virtual base) |
|---|---|---|
| `Device` subobject | offset 0 (start) | offset 16 (**end** of the object) |
| `bytes_moved` | offset 16 | offset 8 |
| `sizeof(Stream)` | 24 | 32 |
| locating the base | compile-time constant | runtime lookup via vbase offset |
| vtable structure | 1 vtable | vtable group + **VTT** + vcall/vbase offsets |

With a virtual base, the derived class can no longer assume where its base
lives: the offset is read from the vtable at runtime, constructors take a
hidden VTT parameter, and virtual dispatch goes through adjusting thunks.
Every one of those is baked into consumer binaries compiled against v1.

## Why this matters

- **It looks like a no-op.** Source compatibility is perfect — no call site
  changes, everything recompiles cleanly — so nothing in a normal review flags
  it as an ABI event.
- **It's the most invasive single-token layout change in C++.** Fields shift,
  the base subobject physically moves to the end of the object, constructors
  change their calling convention (VTT), and new symbols (`_ZTT…`,
  construction vtables) appear.
- **Both directions break.** "Cleaning up" an unnecessary virtual base is the
  same rewrite in reverse.

## Code diff

```cpp
// v1
class Stream : public Device {           // Device at offset 0
public:
    long bytes_moved;                    // offset 16
};

// v2
class Stream : public virtual Device {   // Device at the END (offset 16)
public:
    long bytes_moved;                    // offset 8 — shifted!
};
```

## Real Failure Demo

**Severity: CRITICAL**

**Scenario:** compile app against v1 (direct field access at offset 16), link
to v2 `.so` without recompiling.

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
# → bytes direct      = 140444079725920 (expected 4096)
# → CORRUPTION: direct access used the v1 (non-virtual base) offset
#   and read the virtual-base machinery!
```

The app reads `s->bytes_moved` at v1's offset 16 — where v2 stores the virtual
`Device` base's vtable pointer — and interprets an address as a byte count.
Writes through the same offset would corrupt the vtable pointer and crash on
the next virtual call.

## How to fix

1. **Design the hierarchy up front**: if a class may ever sit in a diamond,
   make the base virtual in the first released version.
2. **Prefer composition or interfaces** (pure-virtual, data-free bases) at ABI
   boundaries — data-free virtual bases still change layout, but hierarchies
   that never need a shared-state base avoid the diamond entirely.
3. **SONAME bump** when the refactor is unavoidable — this cannot be shipped
   compatibly.

## Real-world example

The iostreams hierarchy is the canonical shape: `std::basic_ios` is a
*virtual* base of `basic_istream`/`basic_ostream` precisely so that
`basic_iostream` contains a single copy — a decision that had to be made
before the ABI froze, because retrofitting it would have rewritten the layout
of every stream object. The KDE binary-compatibility policy forbids "changing
the class hierarchy in any way" for exactly this reason.

## abicheck detection

abicheck reports `base_class_virtual_changed` (BREAKING) — the base moved
between the non-virtual and virtual base lists — from DWARF
(`DW_AT_virtuality` on the inheritance DIE) or the header AST
(`min_evidence: L1`). The layout fallout is corroborated by additional
findings: `type_size_changed`, `type_field_offset_changed`,
`vtable_slot_count_changed` (the vtable group grows), and
`rtti_inheritance_changed` / `vtable_thunk_set_changed` from the binary-only
`_ZTV`/`_ZTI` layer, so even a stripped binary shows the break.

```bash
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
# Verdict: BREAKING (base_class_virtual_changed: Stream — became virtual: ['Device'])
```

## References

- [Itanium C++ ABI §2.4/§2.6 — virtual base offsets, VTT and construction vtables](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#vtable)
- [KDE ABI Policy — "you cannot change the class hierarchy"](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B)
