# Case 34 — Access Level Changed


**Verdict:** 🟠 API_BREAK
**abicheck verdict: API_BREAK** (with headers) / **NO_CHANGE** (ELF-only)

## What changes

| Version | Member | v1 access | v2 access |
|---------|--------|-----------|-----------|
| `helper()` | method | `public` | `private` |
| `cache` | field | `public` | `private` |
| `internal_init()` | method | `protected` | `public` |

```cpp
// v1
class Widget {
public:
    void render();
    void helper();       // public
    int cache;           // public
protected:
    void internal_init();
};

// v2
class Widget {
public:
    void render();
    void internal_init();  // promoted: protected → public
private:
    void helper();         // narrowed: public → private
    int cache;             // narrowed: public → private
};
```

## Real Failure Demo

**Severity: API BREAK / COMPILE-TIME FAILURE**

Old binaries still run with v2 because C++ access control is not encoded in the
mangled symbol. The real failure appears when source using the formerly-public
members is rebuilt against the v2 header.

```bash
tmp=$(mktemp -d)
cp examples/case35_access_level/app.cpp "$tmp/app.cpp"
cp examples/case35_access_level/v1.hpp "$tmp/v1.hpp"
cp examples/case35_access_level/v2.hpp "$tmp/v2.hpp"
g++ -std=c++17 -DUSE_V2 -I"$tmp" -c "$tmp/app.cpp" -o "$tmp/app.o"
# error: 'void Widget::helper()' is private within this context
# error: 'int Widget::cache' is private within this context
```

## Why this is NOT a binary ABI break

Access specifiers (`public`, `private`, `protected`) are **compile-time only** in C++.
They are not stored in the ELF symbol table and do not appear in the mangled name.

At the binary level:
- Symbol `_ZN6Widget6helperEv` is exported identically in both versions.
- No vtable changes (non-virtual methods).
- No struct layout changes (`cache` stays at same offset).

Old binaries compiled against v1 that call `widget.helper()` continue to **link and run**
without errors — the symbol resolves to the same address.

## Why this IS a source-level break

New code compiled against v2 **cannot call** `widget.helper()` or `widget.cache` from
outside the class — the compiler will reject the access. This breaks source compatibility
but not binary compatibility.

abicheck reports:
- `METHOD_ACCESS_CHANGED: helper (public → private)` — **API_BREAK**
- `FIELD_ACCESS_CHANGED: cache (public → private)` — **API_BREAK**

## Tool comparison

| Tool | Verdict | Reason |
|------|---------|--------|
| abicheck (ELF only) | NO_CHANGE | No ELF difference |
| abicheck (with headers) | API_BREAK | Parses C++ headers, detects access narrowing |
| abidiff | NO_CHANGE | No DWARF/ELF difference |
| ABICC | API_BREAK | Header parser detects `Method_Became_Private` |

## Benchmark note

The benchmark runs abicheck **with headers** for this case, so the expected verdict is
`API_BREAK`. Without headers, abicheck correctly returns `NO_CHANGE` (the binary is
unchanged).

## Reproduce steps

```bash
cd examples/case34_access_level
g++ -shared -fPIC -o libv1.so v1.cpp
g++ -shared -fPIC -o libv2.so v2.cpp

# ELF-only: no change detected
abicheck dump libv1.so -o v1.json
abicheck dump libv2.so -o v2.json
abicheck compare v1.json v2.json   # → NO_CHANGE

# With headers: API_BREAK detected
abicheck dump libv1.so --header v1.hpp -o v1h.json
abicheck dump libv2.so --header v2.hpp -o v2h.json
abicheck compare v1h.json v2h.json  # → API_BREAK: METHOD_ACCESS_CHANGED
```

## Why runtime result may differ from verdict
Access level narrowing: binary layout unchanged, compile fails

## References

- [C++ access specifiers](https://en.cppreference.com/w/cpp/language/access)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
