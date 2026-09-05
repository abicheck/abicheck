# Case 113: ABI-tag set change ([abi:cxx11] lost on a single symbol)

**Category:** Binary ABI break / C++ mangling | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

In v1, `get_id()` carries an explicit Itanium ABI tag via
`[[gnu::abi_tag("cxx11")]]`, so its mangled symbol is `_Z6get_idB5cxx11v`
(the `B5cxx11` component encodes the tag). In v2 the tag is removed, so the
symbol becomes `_Z6get_idv`. The *demangled* declaration is identical, but
the two are different linker symbols — any consumer binary linked against
the v1 tagged symbol gets `undefined symbol` at load time against v2.

## Old/new diff

| v1.cpp | v2.cpp |
|--------|--------|
| `[[gnu::abi_tag("cxx11")]] int get_id();` | `int get_id();` |

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- abi_tag_changed: ABI-tag set changed for 'get_id': lost [abi:cxx11].
  The mangled name encodes the tag, so the old symbol
  (get_id[abi:cxx11]()) no longer exists under that name (get_id()).
  (cxx11 -> (none))
  > The Itanium ABI-tag set on a symbol changed; old binaries reference
    a symbol that no longer exists under that name.
- func_removed: Public function removed: get_id

Additions:
- func_added: New public function: get_id
```

## Minimum evidence

`min_evidence: L0` — the ABI tag is part of the mangled name itself, so the
exported-symbol table alone shows `_Z6get_idB5cxx11v` present in v1 and
absent from v2, with a plain `_Z6get_idv` in its place. No headers or debug
info needed (this case has no public header on purpose — the snapshot comes
from the compiled `.so`'s own symbol table / DWARF).

## Why abicheck catches it

abicheck demangles each exported symbol, strips any Itanium ABI-tag
components, and compares the tag-stripped base name across versions. When
two symbols share a base name but differ only in their ABI-tag set, it
reports `abi_tag_changed` — distinct from a mass dual-ABI flip (which
churns hundreds of symbols at once and is reported separately as
`glibcxx_dual_abi_flip_detected`) — plus the ordinary `func_removed` /
`func_added` pair for the underlying mangled-symbol change.

## Runtime failure demonstration

**Severity: BREAKING**

**Scenario:** `app.cpp` in this case is a minimal stub (`int main() { return
0; }`) that doesn't call `get_id()`, so the real substitution failure is
demonstrated with a small standalone consumer instead:

```bash
cat > consumer.cpp <<'EOF'
#include <cstdio>
[[gnu::abi_tag("cxx11")]] int get_id();
int main() {
    std::printf("get_id() = %d\n", get_id());
    return 0;
}
EOF

# Build old library + consumer
g++ -shared -fPIC -g v1.cpp -o libfoo.so
g++ consumer.cpp -L. -lfoo -Wl,-rpath,. -o consumer
./consumer
# → get_id() = 7

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o libfoo.so
./consumer
# → ./consumer: symbol lookup error: ./consumer: undefined symbol:
#   _Z6get_idB5cxx11v
```

**Why BREAKING:** the consumer was compiled against the tagged declaration,
so its call site references `_Z6get_idB5cxx11v` specifically; v2's `.so`
only exports the untagged `_Z6get_idv`, so the dynamic linker cannot
resolve the call.

## Safe redesign

Never change a symbol's ABI-tag set in a compatible release — adding or
removing `[[gnu::abi_tag(...)]]` is exactly as breaking as renaming the
function, because the tag is part of the mangled name. If the tag was
added by mistake, ship it as a new overload/alias and deprecate the tagged
one across a release cycle instead of silently dropping it.

**Real-world example:** libstdc++'s dual-ABI mechanism (`_GLIBCXX_USE_CXX11_ABI`)
uses the same `[abi:cxx11]` tag mechanism at a mass scale — flipping the
macro changes hundreds of symbols at once, which is why abicheck reports
that scenario as a single `glibcxx_dual_abi_flip_detected` finding rather
than hundreds of individual `abi_tag_changed` findings. This case isolates
the same underlying mechanism down to one symbol.

## References

- [Itanium C++ ABI: `abi_tag` attribute](https://itanium-cxx-abi.github.io/cxx-abi/abi.html)
- [GCC docs: `abi_tag` attribute](https://gcc.gnu.org/onlinedocs/gcc/Common-Function-Attributes.html)
