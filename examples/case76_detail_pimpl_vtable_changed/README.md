# Case 76: Internal `detail::` polymorphic base vtable change

**Category:** Internal-leak | **Verdict:** BREAKING

## What breaks

```cpp
namespace mylib::detail {
    class algorithm_iface {
        virtual ~algorithm_iface();
        virtual int run() = 0;
        virtual int status() const = 0;
    };
}
class svm_algorithm : public detail::algorithm_iface { /* ... */ };
```

v2 inserts a new virtual `progress()` *between* `run()` and `status()`
in the "internal" `detail::algorithm_iface`. The vtable layout shifts:

| Slot | v1                   | v2                     |
| ---- | -------------------- | ---------------------- |
| 0    | `~algorithm_iface()` | `~algorithm_iface()`   |
| 1    | `run()`              | `run()`                |
| 2    | `status()`           | `progress()`  ← NEW    |
| 3    | —                    | `status()`             |

A v1-compiled consumer that calls `status()` on a `svm_algorithm`
instance now dispatches through the slot occupied at runtime by
`progress()` — wrong return value at best, crash at worst. The
reshuffle never touches any public *name*; only the slot indices
move. That makes the break invisible to symbol-level tools.

## Real Failure Demo

**Severity: BREAKING / WRONG RESULT**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case77_detail_pimpl_vtable_changed_app case77_detail_pimpl_vtable_changed_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case77_detail_pimpl_vtable_changed/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case77_detail_pimpl_vtable_changed/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# status=50 (expect 1)
```

## Why abicheck catches it

On Linux the vtable symbol (`_ZTVN5mylib6detail15algorithm_ifaceE`)
grows from 48 to 56 bytes; `symbol_size_changed` catches that and
combined with the inherited public class drives the verdict to
BREAKING. The `internal_type_leaks_via_public_api` overlay attaches a
synthetic finding citing the inheritance chain

```text
mylib::svm_algorithm → base:mylib::detail::algorithm_iface
```

so reviewers see the "internal" vtable change is in fact part of the
public ABI.

> **Known gap on macOS / Windows:** Mach-O `LC_DYSYMTAB` and PE export
> tables do not carry a symbol-size field, so the
> `symbol_size_changed` signal cannot fire. castxml additionally
> emits `vtable_index=None` for every virtual on these toolchain
> profiles, so the structural vtable diff collapses all virtuals into
> a single slot and `type_vtable_changed` also misses the reshuffle.
> The case is registered as a `known_gap` in
> `examples/ground_truth.json` and the autodiscovery test xfails on
> those platforms.

## Code diff

```cpp
// v1
namespace mylib::detail {
class algorithm_iface {
public:
    virtual ~algorithm_iface();
    virtual int run() = 0;
    virtual int status() const = 0;
};
}

// v2 — one new virtual inserted MID-vtable
namespace mylib::detail {
class algorithm_iface {
public:
    virtual ~algorithm_iface();
    virtual int run() = 0;
    virtual int progress() const;   // NEW — shifts every later slot
    virtual int status() const = 0;
};
}
```

## How to fix

Treat `detail::` polymorphic bases as a frozen surface, or hide them
behind pimpl so the public class no longer inherits from anything
that can change:

```cpp
class svm_algorithm {
public:
    int run();
    int status() const;
private:
    struct impl;
    impl* p_;             // any virtual dispatch happens inside *p_,
                          // never through this class's vtable.
};
```

If polymorphism must remain part of the public surface, only ever
*append* new virtuals at the end of the vtable — never insert mid-table
— and document the slot order as part of the binary contract.

## References

- Itanium C++ ABI §2.5.3: vtable layout, slot indices, and the
  "appending is OK, inserting is not" rule.
- KDE Techbase, *Binary Compatibility Issues With C++*: section on
  virtual-method insertion.
