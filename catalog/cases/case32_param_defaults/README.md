# Case 32: Parameter Default Value Changes (C++)

**Category:** C++ Defaults | **Verdict:** 🟠 API_BREAK (binary compatible)

## Verdict and consumer impact

C++ resolves default argument values at the *call site*, during
compilation of the caller — the library's `.so` never sees them, it only
receives whatever arguments the compiled caller actually passes. That
means an already-built binary calling `conn.connect()` keeps passing `30`
forever, regardless of what v2's header now says the default is; the
mangled symbols (`_ZN10Connection7connectEi`, etc.) don't encode defaults
at all, so binary compatibility is total. The break is source-only, and
only for `configure()`: `verbose` loses its default, so any caller that
recompiles against v2 while still calling `configure()` with zero
arguments fails to compile. `connect`'s changed default and
`disconnect`'s added default don't break anything, recompiled or not.

## Old/new diff

| Method | v1.hpp | v2.hpp | Effect |
|--------|--------|--------|--------|
| `connect` | `void connect(int timeout = 30)` | `void connect(int timeout = 60)` | default changed |
| `configure` | `void configure(bool verbose = true, int retries = 3)` | `void configure(bool verbose, int retries = 5)` | `verbose` default removed; `retries` default changed |
| `disconnect` | `void disconnect(int code)` | `void disconnect(int code = 0)` | default added (compatible) |

## abicheck command

```bash
g++ -shared -fPIC -g -std=c++17 v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g -std=c++17 v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so \
  --header old=v1.hpp --header new=v2.hpp \
  --ast-frontend clang --compiler-option -std=c++17
```

## Expected abicheck finding

```text
Verdict: API_BREAK (exit 2)

- param_default_value_removed: Parameter default removed: configure param verbose (True)

Quality issues:
- param_default_value_changed: Parameter default changed: connect param timeout
- param_default_value_changed: Parameter default changed: configure param retries
```

## Minimum evidence

`min_evidence: L2` — default argument values live only in the header's AST
(they're never emitted into DWARF or the symbol table), so this class of
change is invisible below the header/AST evidence layer. abicheck's
default AST backend is castxml; clang is a supported alternative frontend
(`--ast-frontend clang`), used here because castxml isn't installed in
this environment. `min_evidence` is scoped to `linux` in
`ground_truth.json` because the castxml build used elsewhere doesn't
always emit the `default=` attribute on Homebrew/macOS; header-AST default
extraction is otherwise cross-platform.

## Why abicheck catches it

The header AST records each parameter's `default=` value alongside its
type. abicheck diffs the two versions' default-value lists per parameter:
a default present in v1 and absent in v2 is a removal (source break —
existing zero-arg call sites stop compiling); a default whose value simply
changes is a quality-level finding, since it doesn't break compilation,
only silently changes behavior for callers that get recompiled.

## Runtime failure demonstration

**Severity: NONE (binary compatible)**

**Scenario:** compile app against v1 headers, swap in the v2 `.so` without
recompiling.

```bash
# Build old library + app
g++ -shared -fPIC -g -std=c++17 v1.cpp -o libfoo.so
g++ -g -std=c++17 app.cpp -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → Calling connect() with default timeout:
# →   Compiled as connect(30) from v1 header
# →   OK — v2 default is 60, but caller already passed 30
# → ...
# → Binary is 100% compatible: same mangled symbols, same calling convention
# → NO_CHANGE at binary ABI level

# Swap in new library (no recompile)
g++ -shared -fPIC -g -std=c++17 v2.cpp -o libfoo.so
./app
# → identical output — the caller's defaults were baked in at compile
#   time and never touch the library
```

**Why no runtime failure:** defaults are a pure caller-side compile-time
concept in C++; there is nothing for the v2 library to get wrong at
runtime, since it only ever sees the explicit integer/bool arguments the
already-compiled caller passes.

**Source break verification** (recompiling a zero-arg `configure()` call
against v2 fails):

```bash
cat > /tmp/source_break.cpp << 'SRC'
#include "v2.hpp"
int main() {
    Connection conn;
    conn.configure();  // no args — v2 requires explicit 'verbose'
    return 0;
}
SRC
g++ -g -std=c++17 -I. /tmp/source_break.cpp -L. -lfoo -Wl,-rpath,. -o /tmp/app_v2
# → error: no matching function for call to 'Connection::configure()'
# → note: candidate: 'void Connection::configure(bool, int)'
# → note:   candidate expects 2 arguments, 0 provided
rm -f /tmp/source_break.cpp /tmp/app_v2
```

## Safe redesign

No fix is needed for binary compatibility — it's already preserved. For
source compatibility: don't remove a default from a public header in a
minor/patch release; if a default value must change, document it clearly,
since existing compiled binaries keep silently using the old default until
they're recompiled. Consider overloaded functions instead of defaults for
parameters where the old-vs-new behavioral difference actually matters.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
echo "exit: $?"   # → 0 (no binary ABI change; abidiff doesn't read header
                   #     default-argument AST at all)
```

## References

- [C++ default arguments](https://en.cppreference.com/w/cpp/language/default_arguments)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
