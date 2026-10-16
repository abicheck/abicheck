# Case 215: Returned Double Pointer Lost `const`

**Category:** Source API | **Verdict:** 🟠 API_BREAK

## Verdict and consumer impact

`mylib::get_names()` changes its return type from `const char **` to
`char **`. The symbol, the return register and the pointer are unchanged, so
every prebuilt consumer keeps working.

It is still a source break. Losing a qualifier is a widening only at the
first pointer level: C++ ([conv.qual]) converts `char **` to
`const char * const *` but not to `const char **`, because that would let the
callee's `char *` slot be pointed at a `const char`. A caller that binds the
result to v1's type, `const char **p = mylib::get_names();`, no longer
compiles with GCC or Clang. Before the 2026-10-11 re-audit fix, abicheck
reported every lost return qualifier as the `func_return_pointee_qualifier_removed`
risk (exit 0).

Multi-level sibling of
[`case211`](../case211_return_pointee_gained_const/README.md).

## Old/new diff

| v1.h | v2.h |
|---|---|
| `const char **get_names();` | `char **get_names();` |

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: API_BREAK (exit 2)
  func_return_pointee_qualifier_changed  mylib::get_names: const char ** -> char **
  (no func_return_changed, no func_return_pointee_qualifier_removed)
```

Same result with the CastXML and the Clang header backend
(`ABICHECK_AST_FRONTEND=castxml|clang`).

## Minimum evidence

`L2` (headers): the pointee qualifiers of a return type are not in the Itanium mangled name of a free function, and DWARF alone is not read for them.

## Why abicheck catches it

The detector compares the qualifier at every pointer level and asks whether
the new result still converts to the old type under [conv.qual]. Only a
convertible loss (`const char * const *` -> `char * const *`) stays a risk;
this one does not convert, so it is `func_return_pointee_qualifier_changed`.

## Runtime failure demonstration

```bash
g++ app.cpp -L. -l:libv1.so -Wl,-rpath,. -o app && ./app   # alpha beta, exit 0
cp libv2.so libv1.so && ./app                               # alpha beta, exit 0
sed 's/v1.h/v2.h/' app.cpp > app2.cpp && g++ -c app2.cpp   # error: invalid conversion
```

## Safe redesign

Return `const char * const *` if the table must not be modified, and keep the type once published.
