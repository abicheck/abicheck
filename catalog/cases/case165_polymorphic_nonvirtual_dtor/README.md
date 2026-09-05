# Case 165: Polymorphic Type Without a Virtual Destructor (New Anti-Pattern)

**Category:** API Design / Latent UB | **Verdict:** ⚠️ COMPATIBLE_WITH_RISK

## Verdict and consumer impact

Nothing breaks *today*. v2 adds a brand-new polymorphic type, `Exporter`
(polymorphic because `write()` is virtual), whose destructor is **not**
virtual, plus a public factory (`make_exporter()`) that hands out owning
pointers. Existing binaries keep working unchanged — the risk is planted
for the future: the moment a subclass of `Exporter` is returned from that
factory (e.g. a `PdfExporter` added in v3), `delete` through the base
pointer is undefined behavior ([expr.delete]/3) — the derived destructor
is silently skipped, with no compiler or linker diagnostic on either side.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `class Renderer { virtual ~Renderer(); virtual void draw(int); };` | *(unchanged)* |
| *(no Exporter)* | `class Exporter { ~Exporter(); /* not virtual */ virtual void write(const char*); };` |
| *(no factory)* | `Exporter* make_exporter();` |

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so
abicheck compare libv1.so libv2.so --pattern-verdicts
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

Deployment Risk Changes:
- polymorphic_type_non_virtual_dtor: polymorphic type Exporter (used as
  factory return) has a vtable but no virtual destructor — delete through
  base is UB

Additions:
- func_added: New public function: make_exporter
- type_added: New type: Exporter
```

`Renderer` (virtual dtor, present in both versions) is not flagged — only
the newly introduced `Exporter` triggers the finding.

## Minimum evidence

`min_evidence: L1` — DWARF alone carries enough to reconstruct the vtable
(via the mangled `_ZTV...` symbols and `DW_AT_vtable_elem_location`) and the
absence of a virtual destructor slot; no public headers are required. The
finding also needs `--pattern-verdicts` (ADR-027 opt-in anti-pattern
analysis) — it is not emitted by a bare `compare`.

## Why abicheck catches it

abicheck's ADR-027 single-snapshot anti-pattern analysis inspects each
type's vtable shape from DWARF: a type with a vtable (virtual functions)
but no destructor slot, used as a base class or returned by pointer from a
public factory, is flagged as `polymorphic_type_non_virtual_dtor`. It is
reported only for anti-patterns **newly introduced** on the new side —
pre-existing debt already in v1 is not re-flagged on every run.

## Runtime failure demonstration

**Severity: INFORMATIONAL**

**Scenario:** existing consumers keep working — the risk is planted for the
future, so there is no observable break to reproduce today.

```bash
# Build old library + app
g++ -shared -fPIC -g v1.cpp -o liblib.so
g++ -g app.cpp -L. -llib -Wl,-rpath,. -o app
./app
# → frames drawn = 2 (expected 2)

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o liblib.so
./app
# → frames drawn = 2 (expected 2)      ← no observable effect on existing binaries
```

The failure this case guards against only appears once a future release
adds a subclass and returns it through the same factory:

```cpp
class PdfExporter : public Exporter {
    ~PdfExporter() { fclose(f_); }     // never runs!
};
// consumer:
Exporter* e = make_exporter();          // now returns a PdfExporter
delete e;                               // UB: ~Exporter() only, file leaks
```

## Safe redesign

1. **Make the destructor virtual** before the type ships: `virtual ~Exporter();`
   (costless here — the class already has a vtable).
2. If the type is *not* meant to be deleted polymorphically, make the
   destructor `protected` (and non-virtual) so `delete base` does not compile.
3. Or return by `std::unique_ptr<Exporter, void(*)(Exporter*)>` with a
   library-side deleter, so destruction always happens inside the library.

**Real-world example:** the C++ Core Guidelines encode this as
[C.35: "A base class destructor should be either public and virtual, or
protected and non-virtual"](https://isocpp.github.io/CppCoreGuidelines/CppCoreGuidelines#c35-a-base-class-destructor-should-be-either-public-and-virtual-or-protected-and-non-virtual).
GCC/Clang ship `-Wdelete-non-virtual-dtor` (and `-Wnon-virtual-dtor`) because
this bug class is so common — but those warnings only fire in the
*consumer's* translation unit, after the trap is already in the released
header.

## References

- [C++ Core Guidelines C.35](https://isocpp.github.io/CppCoreGuidelines/CppCoreGuidelines#c35-a-base-class-destructor-should-be-either-public-and-virtual-or-protected-and-non-virtual)
- [KDE ABI Policy — adding a virtual destructor later is itself BREAKING](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B)
- Scott Meyers, *Effective C++*, Item 7: "Declare destructors virtual in polymorphic base classes"
