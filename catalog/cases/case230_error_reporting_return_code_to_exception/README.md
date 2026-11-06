# Case 230: Error Reporting Changed from Return Code to Exception

**Category:** No Change (declared interface) | **Verdict:** ✅ NO_CHANGE — with a separate **behavioral break**

## Verdict and consumer impact

`cfg::parse_port(const char*)` keeps its declaration, its mangled symbol,
its exception specification (none; it was never `noexcept`) and the
imports of the library. Only its error reporting changes: v1 returns the
documented sentinel `-1` for invalid input, v2 throws
`std::invalid_argument`. An unchanged old consumer that checks for `-1`
and has no `try`/`catch` (correct code under v1's contract) now
terminates on the first bad input.

This is a **behavioral break**, not a source (API) or binary (ABI) break. As
a statement about the declared interface the verdict is `NO_CHANGE`, and that
stays true even with build data and sources (see below). The break is
recorded in `ground_truth.json` as `behavioral_break: true` with
`truth_scope: declared-interface`.

## Old/new diff

| v1.cpp | v2.cpp |
|--------|--------|
| invalid input → `return -1;` | invalid input → `throw std::invalid_argument(...)` |

`v1.h` and `v2.h` are identical. Both versions already throw for a null
pointer, so the libraries import the same C++ runtime symbols.

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Compatibility: NO_CHANGE | Policy: strict_abi | Gate: ACCEPTED (exit 0)
Findings: 0 detected; 0 retained (0 gating, 0 non-gating); 0 review groups.
```

No `ChangeKind` is expected. The absence is the detection control; the
behavioral regression is recorded separately.

## Minimum evidence

`min_evidence: L2`. The public header AST is what establishes that the
*published* declaration is unchanged. Nothing deeper adds a fact: the case
was also compared at `--depth source` with `compile_commands.json` and both
source trees, and the result is still `NO_CHANGE`.

## Why abicheck reports nothing

abicheck's deterministic checks compare what the artifacts *declare*:
exported symbols, DWARF types and layout, header declarations, build flags,
and source-graph dependencies. All of those are identical here. The change
sits in an out-of-line function body, and its meaning (which errors are reported by return value and which by exception) exists only in
documentation. No static rule can tell a bug fix from a contract break in a
function body, so abicheck correctly does not guess. That is why
`ground_truth.json` records `truth_scope: declared-interface` and a separate
`behavioral_break: true`: **`NO_CHANGE` here does not mean "safe to ship".**
See [Behavioral & Semantic Compatibility](../../../docs/learn/behavioral-compatibility.md).

## Runtime failure demonstration

**Severity: an unchanged old consumer breaks at runtime; no static tool shows
it.**

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -g app.cpp -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app
./app            # port = 8080                                   exit 0
g++ -shared -fPIC -g v2.cpp -o libv1.so   # swap in v2, no recompile
./app            # terminate called after throwing an instance of
                 # 'std::invalid_argument'                        exit 134 (abort)
```

## Safe redesign

Keep the old function's error contract. Add the throwing variant under a new
name (`parse_port_or_throw`), or a new overload that takes an error-out
parameter, and deprecate the old entry point. If the error contract must
change, treat it as a major-version change and say so in the release notes.
A `noexcept` on the old function would have made the contract visible to the
compiler (and to abicheck: `func_noexcept_removed`).

## Cross-tool comparison

`abidiff` and ABICC also report no change: they compare the same symbols,
types and declarations. Catching this break needs a test that runs a consumer
against the new library (the `app.cpp` above is such a test).
