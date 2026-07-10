# Case 165: Polymorphic Type Without a Virtual Destructor (New Anti-Pattern)

**Category:** API Design / Latent UB | **Verdict:** COMPATIBLE_WITH_RISK (bad practice)

## What breaks

Nothing — yet. v2 adds a brand-new polymorphic type to the public API:

```cpp
class Exporter {
public:
    Exporter();
    ~Exporter();                          // NOT virtual — the anti-pattern
    virtual void write(const char* path); // polymorphic: has a vtable
    long bytes_written;
};

Exporter* make_exporter();                // callers own and delete the result
```

`Exporter` has a vtable (via `virtual write()`) but its destructor is **not
virtual**, and the factory hands out owning pointers. The moment the library
(or a plugin) returns any subclass of `Exporter` from `make_exporter()`,
`delete` through the base pointer is **undefined behavior** ([expr.delete]/3):
the derived destructor is silently skipped, leaking resources or corrupting
state — with no compiler or linker diagnostic on either side.

## Why this matters

- **It's a trap that springs later.** The v2 release is fully compatible;
  the UB arrives in v3 when someone adds `class PdfExporter : Exporter` and
  returns it from the existing factory. At that point no ABI checker sees any
  change at the call boundary — the bug was planted here, in v2.
- **The classic C++ design rule** (Meyers, *Effective C++* Item 7): a class
  with any virtual function that is deleted polymorphically must have a
  virtual destructor.
- **Existing binaries are unaffected**, which is exactly why this is
  COMPATIBLE_WITH_RISK and not BREAKING: the finding is a design-debt
  warning on the *new* surface, not a diff of the old one.

The contrast is encoded in the fixture itself: v1's `Renderer` has a virtual
destructor and is **clean** — only the newly introduced `Exporter` is flagged.

## Code diff

```cpp
// v1: the only polymorphic type is safe
class Renderer {
public:
    virtual ~Renderer();          // virtual dtor — deleting through base OK
    virtual void draw(int frame);
};
Renderer* make_renderer();

// v2: new polymorphic type + owning factory, but a non-virtual dtor
class Exporter {
public:
    ~Exporter();                  // NOT virtual
    virtual void write(const char* path);
};
Exporter* make_exporter();        // caller deletes through Exporter*
```

## Real Failure Demo

**Severity: INFORMATIONAL**

**Scenario:** existing consumers keep working — the risk is planted for the
future, so the demo shows the *absence* of a runtime break:

```bash
# Build old library + app
g++ -shared -fPIC -g v1.cpp -o liblib.so
g++ -g app.cpp -L. -llib -Wl,-rpath,. -o app
./app
# → frames drawn = 2 (expected 2)

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o liblib.so
./app
# → frames drawn = 2 (expected 2)      ← still fine; the debt is in the NEW API
```

The failure this case guards against looks like this (a future v3):

```cpp
class PdfExporter : public Exporter {
    ~PdfExporter() { fclose(f_); }     // never runs!
};
// consumer:
Exporter* e = make_exporter();          // now returns a PdfExporter
delete e;                               // UB: ~Exporter() only, file leaks
```

## How to fix

1. **Make the destructor virtual** before the type ships: `virtual ~Exporter();`
   (costless here — the class already has a vtable).
2. If the type is *not* meant to be deleted polymorphically, make the
   destructor `protected` (and non-virtual) so `delete base` does not compile.
3. Or return by `std::unique_ptr<Exporter, void(*)(Exporter*)>` with a library-side
   deleter, so destruction always happens inside the library.

## Real-world example

The C++ Core Guidelines encode this as
[C.35: "A base class destructor should be either public and virtual, or
protected and non-virtual"](https://isocpp.github.io/CppCoreGuidelines/CppCoreGuidelines#c35-a-base-class-destructor-should-be-either-public-and-virtual-or-protected-and-non-virtual).
GCC/Clang ship `-Wdelete-non-virtual-dtor` (and `-Wnon-virtual-dtor`) because
this bug class is so common — but those warnings only fire in the *consumer's*
translation unit, after the trap is already in the released header.

## abicheck detection

abicheck detects this as `polymorphic_type_non_virtual_dtor` (RISK), an
ADR-027 single-snapshot **anti-pattern**: a type with a vtable but no
destructor slot, used as a base class or returned by pointer from a public
factory. It is reported only for anti-patterns **newly introduced** on the new
side (pre-existing debt is not nagged about on every run), and it requires the
opt-in pattern analysis:

```bash
abicheck compare libv1.so libv2.so --old-header v1.h --new-header v2.h --pattern-verdicts
# Verdict: COMPATIBLE_WITH_RISK (polymorphic_type_non_virtual_dtor: Exporter)
```

`Renderer` (virtual dtor, present in both versions) is not flagged.

## References

- [C++ Core Guidelines C.35](https://isocpp.github.io/CppCoreGuidelines/CppCoreGuidelines#c35-a-base-class-destructor-should-be-either-public-and-virtual-or-protected-and-non-virtual)
- [KDE ABI Policy — adding a virtual destructor later is itself BREAKING](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B)
- Scott Meyers, *Effective C++*, Item 7: "Declare destructors virtual in polymorphic base classes"
