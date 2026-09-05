# Case 99: Experimental to Stable Graduation (Compatible)

**Category:** Addition | **Verdict:** 🟢 COMPATIBLE

## Verdict and consumer impact

A library first publishes a feature under an `experimental::` namespace and
later promotes it to the stable `lib::` namespace, keeping the experimental
alias as a friendly forward. Existing consumers written against
`lib::experimental::sort()` keep compiling and linking unmodified — nothing
is removed. New consumers can migrate to `lib::sort()` at their own pace.

## Old/new diff

| old/lib.h | new/lib.h |
|-----------|-----------|
| `namespace lib { namespace experimental { void sort(); } }` | `namespace lib { void sort(); namespace experimental { void sort(); } }` |

## abicheck command

```bash
g++ -shared -fPIC -g -std=c++17 old/lib.cpp -Iold -o libfoo_v1.so
g++ -shared -fPIC -g -std=c++17 new/lib.cpp -Inew -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so \
  --header old=old/lib.h --header new=new/lib.h --ast-frontend clang
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

- func_added: New public function: sort (lib::sort())
  > New function available; existing binaries are unaffected.

- experimental_graduated: Experimental declaration 'lib::experimental::sort()'
  graduated to stable name 'lib::sort()'; experimental alias retained.
  > A declaration that previously lived under an experimental:: (or similar)
    namespace is now also available at a stable name in the same library,
    while the experimental alias is retained. Existing consumers keep
    compiling; new consumers are encouraged to migrate to the stable name.
```

## Minimum evidence

`ground_truth.json` lists `min_evidence: L0` — the underlying signal (a new
public name, `lib::sort`, appearing in the exported-symbol table) is visible
from `.dynsym` alone: `g++ -shared -fPIC -std=c++17 ... && abicheck compare`
with no headers reports a plain `func_added` for it. Classifying that
addition specifically as an `experimental_graduated` migration event (rather
than a bare addition) needs namespace-qualified name evidence; this
reproduction supplies that via header/AST evidence (`--ast-frontend clang`,
since castxml isn't installed in this environment — castxml is the
documented default AST backend where available).

## Why abicheck catches it

Without the dedicated detector the diff is just a `func_added` for
`lib::sort` — a silent compatible change that gives no hint the library is
signaling readiness for migration. abicheck's namespace-graduation detector
indexes public functions by `(namespace-stripped qualified name, leaf name)`
on both sides: when a name that used to exist only under `experimental::`
now also exists at the corresponding stable name, and the experimental
spelling is still present, it emits `experimental_graduated` instead of a
bare addition — surfacing the migration event for reviewers.

## Runtime failure demonstration

No observable effect on existing binaries — this is a pure addition with
the old spelling preserved.

```bash
# Build old library + app (app.cpp calls lib::experimental::sort/other_fn)
g++ -shared -fPIC -g -std=c++17 old/lib.cpp -Iold -o libfoo.so
g++ -g -std=c++17 app.cpp -I. -L. -lfoo -Wl,-rpath,. -o app
./app; echo "exit: $?"
# → exit: 0

# Swap in new library (no recompile)
g++ -shared -fPIC -g -std=c++17 new/lib.cpp -Inew -o libfoo.so
./app; echo "exit: $?"
# → exit: 0   ← identical
```

The v1-era consumer keeps compiling and running against v2 because the
experimental alias is preserved alongside the stable name.

## Safe redesign

This case *is* the safe redesign pattern for promoting an experimental API:
keep the experimental alias forwarding to the new stable implementation
(`void sort() { ::lib::sort(); }` in `new/lib.cpp`) for at least one
deprecation cycle before ever considering removal, and only remove the
experimental alias itself as a separate, clearly-flagged breaking change
(see case100 for what happens when that alias is dropped without a
replacement).

**Real-world example:** the C++ standard library did exactly this with
`std::experimental::filesystem` (TS) graduating to `std::filesystem` (C++17)
— implementations kept the `experimental` namespace as a compatible alias
for years after standardization.

## References

- [cppreference: `<filesystem>`](https://en.cppreference.com/w/cpp/filesystem)
