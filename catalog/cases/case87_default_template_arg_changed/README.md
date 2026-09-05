# Case 87: Default Template Argument Changed

**Category:** Template ABI | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

```cpp
// v1
template <typename Float, typename Distance = minkowski_distance<Float>>
class descriptor;

// v2
template <typename Float, typename Distance = euclidean_distance<Float>>
class descriptor;
```

Consumer source like `mylib::descriptor<float> d;` compiles unchanged
against both headers — the default-argument change is invisible at the call
site. But the *substituted* type differs, so the mangled symbol differs
too: v1 ships `descriptor<float, minkowski_distance<float>>`, v2 ships
`descriptor<float, euclidean_distance<float>>`. A consumer compiled against
v1's header calls the first mangled name; the v2 library exports only the
second. Recompilation is mandatory even though nothing in the consumer's
own source changed.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `template <typename Float, typename Distance = minkowski_distance<Float>> class descriptor;` | `template <typename Float, typename Distance = euclidean_distance<Float>> class descriptor;` |

## abicheck command

```bash
g++ -shared -fPIC -g -std=c++17 -I. v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g -std=c++17 -I. v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so \
  --ast-frontend clang -H old=v1.h -H new=v2.h --lang c++
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- default_template_arg_changed: Template instantiation 'descriptor'
  substitutes to different arguments than its surviving sibling
  'descriptor'. Consistent with a change to a default template argument
  in the declaring header: consumer source compiles unchanged, but the
  substituted mangled symbol differs.
  (float, mylib::minkowski_distance<float> -> float, mylib::euclidean_distance<float>)
  > Consumers built against the old default reference a symbol that no
    longer exists. Unlike function default parameter changes (NO_CHANGE),
    template default arguments ARE part of the substituted type and
    affect mangling.

- instantiation_missing_from_binary / func_removed (per instantiation):
  the old library's descriptor<float, minkowski_distance<float>> symbols
  are gone; only descriptor<float, euclidean_distance<float>> ships in v2.
```

## Minimum evidence

`min_evidence: L2` — the raw symbol diff shows an unrelated-looking
add/remove pair (old mangled name gone, new one added); recovering the
*template's default argument* fact needs the public header AST, since
`default_template_arg_changed`'s detector demangles `Function.mangled` to
recover the template-argument-embedded name and correlates it against the
default declared in the header. `castxml` is the documented default header
backend; a clang-based AST frontend is a supported alternative that reaches
the same evidence.

## Why abicheck catches it

`detect_default_template_arg_changed` (`abicheck/diff_cpp_patterns.py`)
demangles each removed/added function's mangled name to recover its
substituted template arguments, groups instantiations by their unqualified
template name, and flags a surviving sibling whose only difference is the
substituted-default-argument slot — the header AST confirms which
parameter actually carries a default, distinguishing this from an
unrelated overload change.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** app instantiates `descriptor<float>` using v1's default
(`minkowski_distance<float>`); v2's library only ships the
`euclidean_distance<float>` instantiation.

```bash
# Build old library + app
g++ -shared -fPIC -g -std=c++17 -I. v1.cpp -o libfoo.so
g++ -g -std=c++17 -I. app.cpp -L. -lfoo -Wl,-rpath,. -o app
./app
# → dim = 0

# Swap in new library (no recompile)
g++ -shared -fPIC -g -std=c++17 -I. v2.cpp -o libfoo.so
./app
# → ./app: symbol lookup error: ./app: undefined symbol:
#   _ZNK5mylib10descriptorIfNS_18minkowski_distanceIfEEE9dimensionEv
```

**Why CRITICAL:** the app's own source (`mylib::descriptor<float> d;`)
never changed and recompiles cleanly against either header — the failure
only shows up as a runtime unresolved-symbol error against a library built
from the other header's default.

## Safe redesign

Never change a template's default argument once instantiations of it have
shipped — it's part of the substituted type, not a source-only convenience.
Either keep the old default and add an explicitly-named alias for the new
behavior (`descriptor_euclidean<Float>`), or bump the SONAME/major version
so old and new defaults can never mix in the same deployment.

**Real-world example:** oneDAL's `cpp/oneapi/dal/algo/knn/common.hpp`
declares `descriptor<Float, Method, Task, Distance = ...>` with several
defaulted template parameters; changing any one of them re-mangles every
explicit instantiation that didn't override that argument.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

Not independently re-verified in this environment (`abidiff` unavailable
here) — see case02's parameter-type-change case for a documented `abidiff`
exit-code comparison.
