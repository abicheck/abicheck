# Case 79: Missing template instantiation in shipped binary

**Category:** Header-vs-binary parity | **Verdict:** BREAKING

## What breaks

The v1 library declares two explicit template instantiations:

```cpp
template class descriptor<float>;
template class descriptor<double>;
```

…and the header forward-declares both via `extern template`. Consumers using
`descriptor<double>` link against the symbol shipped in the `.so`.

In v2, the `template class descriptor<double>;` line is dropped from the
`.cpp` file (e.g. "build-time savings — nobody uses it"). The header is
**unchanged** — it still says `extern template class descriptor<double>`.

- Consumer compiles successfully against v2 header (no diagnostic).
- Linker against `libv2.so` fails with `undefined reference to descriptor<double>::descriptor()`.
- Or worse: dynamic load succeeds (because consumer's own translation unit
  emits no instantiation either), then crashes the first time a method is
  called from a different consumer that *did* link the v1 symbol.

This is the failure mode for oneDAL's "ship `float` instantiation only" build
trim: the public combinatorics promised by `cpp/oneapi/dal/algo/*/common.hpp`
must match the explicit instantiations in `*_instantiation.cpp`.

## Real Failure Demo

**Severity: BREAKING / LOAD-TIME FAILURE**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case79_missing_template_instantiation_app case79_missing_template_instantiation_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case79_missing_template_instantiation/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case79_missing_template_instantiation/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# ./app_v1: symbol lookup error: undefined symbol: _ZN5mylib10descriptorIdE13set_thresholdEd
```

## Why abicheck catches it

A new `INSTANTIATION_MISSING_FROM_BINARY` ChangeKind is emitted when a
symbol exported in the **old** library disappears from the **new** library
while the **header** of the new release still references its mangled signature.

Mechanically this is similar to `func_removed`, but the report distinguishes
"the API author removed this on purpose" from "the API author thinks this
still ships but it doesn't" — the latter is a strictly worse signal because
no source-level deprecation warning fires.

## Code diff

```cpp
// v1.cpp — both instantiations shipped
template class descriptor<float>;
template class descriptor<double>;

// v2.cpp — double instantiation accidentally dropped
template class descriptor<float>;
// template class descriptor<double>;   // BUG: header still references this
```

## Real-world reference

oneDAL's algorithm modules each have an `*_instantiation.cpp` file (one per
combination of `Float × Method × Task`). If a build is trimmed without
updating headers, the binary stops shipping symbols the header still
advertises. case79 reproduces this failure with a minimal two-instantiation
example.
