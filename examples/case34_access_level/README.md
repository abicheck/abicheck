# Case 34: Access Level Changed

**Category:** C++ Access Control | **Verdict:** 🟠 API_BREAK

## Verdict and consumer impact

Access specifiers (`public`/`private`/`protected`) are a compile-time-only
concept in C++ — they're not stored in the ELF symbol table or encoded in
the mangled name. `Widget::helper()`'s mangled symbol
(`_ZN6Widget6helperEv`) is exported identically in both versions, no
vtable changes occur (these are non-virtual methods), and `cache` stays at
the same struct offset. An already-built binary that calls `widget.helper()`
or reads/writes `widget.cache` keeps linking and running against v2
unmodified. The break only appears at recompile time: new code built
against the v2 header can no longer call `helper()` or touch `cache` from
outside the class — the compiler rejects the access.

## Old/new diff

| Member | v1.hpp | v2.hpp |
|--------|--------|--------|
| `helper()` (method) | `public` | `private` |
| `cache` (field) | `public` | `private` |
| `internal_init()` (method) | `protected` | `public` *(widened — compatible)* |

## abicheck command

```bash
g++ -shared -fPIC -g -std=c++17 v1.cpp -o libv1.so
g++ -shared -fPIC -g -std=c++17 v2.cpp -o libv2.so
abicheck compare libv1.so libv2.so \
  --header old=v1.hpp --header new=v2.hpp \
  --ast-frontend clang --compiler-option -std=c++17
```

## Expected abicheck finding

```text
Verdict: API_BREAK (exit 2)

- method_access_changed: Method access level narrowed: helper (public -> private)
  > Method access level narrowed (e.g. public->private); old code calling
    it won't compile.
- field_access_changed: Field access level narrowed: Widget::cache (public -> private)
  > Field access level narrowed; old code accessing it won't compile.
```

## Minimum evidence

`min_evidence: L2` — DWARF alone already sees `cache`'s narrowed field
access (member-variable `DW_AT_accessibility` is emitted regardless of
headers) as a `field_access_changed` finding, but `helper()`'s method
access narrowing needs the header AST to resolve reliably — abicheck's
default AST backend is castxml; clang is a supported alternative frontend
(`--ast-frontend clang`), used here because castxml isn't installed in
this environment. The full `method_access_changed` finding this case is
named for requires L2.

## Why abicheck catches it

The header AST records each class member's access specifier alongside its
declaration. abicheck compares old vs. new access for every method and
field reachable from the public surface; a `public` member that becomes
`private` (or `protected`) is a narrowing and reported as a source-level
break, while a member that becomes *more* accessible (`internal_init`:
`protected` → `public`) is compatible — existing callers were never
allowed to reach it in the first place.

## Runtime failure demonstration

**Severity: NONE (binary compatible) / compile-time failure on rebuild**

**Scenario:** compile app against v1 headers, swap in the v2 `.so` without
recompiling.

```bash
# Build old library + app
g++ -shared -fPIC -g -std=c++17 v1.cpp -o libwidget.so
g++ -std=c++17 -g app.cpp -I. -L. -lwidget -Wl,-rpath,. -o app
./app
# → render() called OK
# → helper() called OK
# → cache = 123

# Swap in new library (no recompile)
g++ -shared -fPIC -g -std=c++17 v2.cpp -o libwidget.so
./app
# → render() called OK
# → helper() called OK
# → cache = 123
```

**Why no runtime failure:** access control is enforced entirely by the
compiler at the call site; the emitted machine code for `w.helper()` is
identical regardless of what the header currently says is allowed, so
swapping the `.so` changes nothing observable at runtime.

**Source break verification** (recompiling against v2 fails):

```bash
g++ -std=c++17 -DUSE_V2 -I. -c app.cpp -o app.o
# → error: 'void Widget::helper()' is private within this context
# → error: 'int Widget::cache' is private within this context
```

## Safe redesign

Don't narrow the access of a member that's already part of the public API
— treat it the same as removing a public symbol. If a member genuinely
needs to become internal, add a new, deliberately-private implementation
member instead and leave the old public one in place (deprecated) for at
least one release cycle, or route access through an accessor function that
can later be restricted more gracefully.

**Real-world example:** "we accidentally exposed too much" cleanups
(narrowing an implementation-detail method or field back to `private`)
are common in libraries after an initial 1.0 release, and are exactly this
class of break — binary-safe, but silently fails every downstream rebuild
until callers stop touching the now-private members.

## Cross-tool comparison

| Tool | Verdict | Reason |
|------|---------|--------|
| abicheck (ELF/DWARF only) | API_BREAK (`field_access_changed` only) | DWARF exposes field accessibility but not method accessibility |
| abicheck (with headers) | API_BREAK (both `method_access_changed` and `field_access_changed`) | Header AST resolves access for both members and methods |
| abidiff | NO_CHANGE | No DWARF/ELF layout or symbol-table difference |
| ABICC | API_BREAK | Header parser detects `Method_Became_Private` |

```bash
abidw --out-file v1.xml libv1.so
abidw --out-file v2.xml libv2.so
abidiff v1.xml v2.xml
echo "exit: $?"
```

## References

- [C++ access specifiers](https://en.cppreference.com/w/cpp/language/access)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
